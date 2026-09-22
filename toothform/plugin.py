"""
Plugin `toothform` — exportar (QR + placa base) con la app de escritorio
ToothFORM y leer el resultado, para el flujo TOOTHFORM CNC4.

Tres tools, y el orden en que aparecieron explica por qué hay tres:

- `exportar` usa `process` + `fs`. Desde el release 20260518 ToothFORM se puede
  correr **por línea de comandos**: `Toothform.exe <config.json>` carga la
  carpeta del JSON (`FilesToProcess.OpenFolder` + `Type`), exporta y termina,
  sin abrir la ventana. El tool escribe ese JSON, corre el ejecutable, espera
  a que termine y lee el log que dejó. No hay ventana que encontrar, ni
  diálogo de archivo que llenar, ni operador que tenga que dejar el STL
  cargado: es el camino para producción.

  Lo que se verificó en esa versión y el tool asume: el exit code es siempre
  2 (también con un JSON inexistente), así que el resultado se lee del log y
  no del proceso; el log `<fecha>.<hora>.log` cae en `ExportPrintPath`; el
  export queda en `<ExportPrintPath>\\<nombre del dato>\\` (el prefijo del
  STL antes del primer `-`); y una ruta con caracteres fuera de ASCII en el
  JSON hace que no exporte nada ni deje log, en cualquier codificación. Por
  eso el tool corta antes de correr si alguna ruta no es ASCII.

- `add_qr` usa `window` (v0.3.0-beta.3, issue #12 del núcleo): encuentra la
  ventana de ToothFORM y clickea "Export". Es el camino para el release
  anterior (20260123), que no tiene modo cmd; ahí el operador deja el STL
  cargado con el tipo y "QRcode" como corresponde, y quien espera al
  resultado es `check_log`.

- `check_log` usa `fs` + `clock`: busca el `.log` más reciente en la carpeta
  de salida y lo interpreta. Leer la ventana del Notepad que ToothFORM abre
  al terminar no sirve (`window.read_text` sin control devuelve el título).

Semántica compartida por `exportar` y `check_log`, la que el flujo espera por
sus aristas: `ok` = export terminado sin fallas, `err` = terminado con fallas
(o sin log: el flujo copia el log si lo hay y avisa). `check_log` además
devuelve `loop` cuando todavía no hay un log nuevo.

`toothcam_enviar` es el "camino fácil" para ToothCAM (la app de corte de
líneas de cutting, no ToothFORM): no hay versión por línea de comandos
documentada, pero si alguien deja el "Watch directory" de la app ya activado
a mano sobre una carpeta fija, sólo hace falta mover los STL ahí y esperar a
que aparezca un log. Un tool que sólo mueve no alcanza: el flujo necesita
saber si terminó bien o mal antes de seguir, así que este tool mueve **y**
espera, bloqueando con `clock.sleep` en vez de devolver `loop` para que el
flujo lo reintente — es una sola espera acotada (`timeout`), no un polling
externo que valga la pena modelar como arista. No se conoce todavía el
formato real del log de ToothCAM (no hay licencia para probarlo), así que
reusa `_interpretar_log` -la misma lógica de `check_log`, con el mismo
fallback por palabras clave "success"/"fail"/"error" si no matchea el patrón
de totales de ToothFORM- como aproximación razonable hasta tener una muestra
real. Corregir el parseo cuando aparezca una es cambiar un regex acá, no
tocar el flujo.

El otro camino (elegir Lip Flat/Tongue Flat por corrida y abrir un archivo
puntual) necesita clickear la ventana de la app, no sólo mover un archivo:
para eso está el plugin `control_ventanas`, separado a propósito porque no es
nada específico de ToothCAM.
"""

from __future__ import annotations

import json
import re
import threading
import time

from backend.core import ports as port_names
from backend.core.contract import (
    FunctionTool,
    Output,
    Param,
    ParamType,
    Plugin,
    PluginManifest,
    Setting,
    ToolContext,
    ToolManifest,
    ToolResult,
)

# Medido el 14/09/2026 con un STL de 20 MB: cada Toothform.exe usa un core
# (es monohilo) y llega a ~700 MB de RAM; dos en paralelo tardan lo mismo
# que uno. El límite lo pone la memoria, no la CPU.
MAX_SIMULTANEOS = "toothformMaxSimultaneos"

MANIFEST = PluginManifest(
    name="toothform",
    label="ToothFORM",
    version="0.4.0",
    doc="Exportar (QR + placa base) con la app ToothFORM —por línea de comandos o clickeando su ventana—, leer su log de resultado, y el camino fácil de ToothCAM (mover a una carpeta vigilada y esperar el log).",
    ports=(port_names.PROCESS, port_names.FS, port_names.CLOCK, port_names.WINDOW),
    settings=(
        Setting(
            MAX_SIMULTANEOS, ParamType.INT, label="Exportaciones simultáneas", default=2,
            doc="Cuántos Toothform.exe pueden correr a la vez en esta máquina (exportar por línea de "
            "comandos). Cada uno usa un core y unos 700 MB de RAM. Vacío o 0 = sin límite.",
        ),
    ),
)


class _Turnos:
    """
    Cuántas exportaciones están corriendo en este proceso, y la espera por un
    lugar. Vive a nivel de módulo porque el límite es de la **máquina**: todos
    los runs de la webapp comparten el mismo proceso y la misma RAM. El núcleo
    sólo sabe de "concurrente" o "exclusivo" (`ToolManifest.concurrency`), y
    exclusivo frenaría todos los flujos, no sólo los exports.
    """

    def __init__(self) -> None:
        self._condicion = threading.Condition()
        self._en_curso = 0

    @property
    def en_curso(self) -> int:
        return self._en_curso

    def tomar(self, maximo: int, timeout: float) -> bool:
        limite = time.monotonic() + timeout
        with self._condicion:
            while self._en_curso >= maximo:
                resto = limite - time.monotonic()
                if resto <= 0:
                    return False
                self._condicion.wait(resto)
            self._en_curso += 1
            return True

    def soltar(self) -> None:
        with self._condicion:
            self._en_curso = max(0, self._en_curso - 1)
            self._condicion.notify_all()


_TURNOS = _Turnos()

# Lo que ToothFORM escribe al final del log, según el manual V2.0:
#   "Total 1 models, of which 1 succeeded and 0 failed"
_PATRON_TOTAL = re.compile(
    r"Total\s+(\d+)\s+models?,\s+of which\s+(\d+)\s+succeeded\s+and\s+(\d+)\s+failed", re.I,
)
# Cada modelo, una línea: "<nombre>    Export successfully" / "<nombre>    Failed to hollow".
_PATRON_EXITO = re.compile(r"^(\S+)\s+Export successfully\s*$", re.I | re.M)

_SALIDAS_LOG = (
    Output("log_file", ParamType.STR, doc="Nombre del archivo de log encontrado (vacío si no hubo)."),
    Output("checkLogResult", ParamType.JSON, doc="{log_file, message}, como lo devolvía el nodo legacy."),
    Output("exitosos", ParamType.INT),
    Output("fallidos", ParamType.INT),
    Output("log_texto", ParamType.STR),
)


def _interpretar_log(ctx: ToolContext, nombre: str, texto: str, **extra) -> ToolResult:
    """ok si el log no reporta fallas, err si las hay. `extra` son salidas más del tool que llama."""
    primera_linea = next((l.strip() for l in texto.splitlines() if l.strip()), "")
    ctx.log(f"{nombre}: {primera_linea[:160]}")
    resultado = {"log_file": nombre, "message": primera_linea}
    comunes = dict(log_file=nombre, checkLogResult=resultado, log_texto=texto, **extra)

    coincidencia = _PATRON_TOTAL.search(texto)
    if coincidencia is not None:
        total, exitosos, fallidos = (int(g) for g in coincidencia.groups())
        if fallidos > 0:
            return ToolResult.err(f"{fallidos} de {total} fallaron", exitosos=exitosos, fallidos=fallidos, **comunes)
        return ToolResult.ok(exitosos=exitosos, fallidos=fallidos, **comunes)

    # Sin la línea de totales (otra versión de la app): decidir por las palabras.
    bajo = texto.lower()
    if "fail" in bajo or "error" in bajo:
        return ToolResult.err("el log reporta una falla", exitosos=0, fallidos=1, **comunes)
    if "success" in bajo:
        return ToolResult.ok(exitosos=1, fallidos=0, **comunes)
    return ToolResult.err("el log no tiene un resultado reconocible", exitosos=0, fallidos=0, **comunes)


def _logs(fs, carpeta: str):
    return [e for e in fs.list_dir(carpeta) if not e.is_dir and e.name.lower().endswith(".log")]


# ── exportar ────────────────────────────────────────────────────────────

_TIPOS = ("Type1", "Type2", "Type3")

# Los valores con los que viene el Toothform.json del release 20260518, salvo
# LimitX, que en esta instalación es 2.5 (el manual dice que depende del
# equipo). Cualquiera se pisa desde el nodo: `LimitX=3.0`.
_PARAMETROS_DEFAULT = {
    "ShellThickness": "1.8",
    "BottomPlaneThickness": "1.0",
    "LimitR": "98.0",
    "LimitH": "23.0",
    "LimitX": "2.5",
    "ToothMinVolume": "30.0",
    "Boolean": "1",
    "QrSize": "15.0",
    "TransDis": "0.0",
    "Radius1": "2.62",
    "Radius2": "2.12",
    "ImportExtPos": "0",
}

EXPORTAR = ToolManifest(
    id="toothform.exportar",
    label="exportar (línea de comandos)",
    category="TOOTHFORM",
    doc=(
        "Corre Toothform.exe con un JSON de configuración: carga los STL de 'carpeta' "
        "—o sólo los de 'archivos'—, exporta con QR a 'salida' y termina, sin abrir la "
        "ventana (release 20260518 o posterior). Espera a que termine y lee el log. El "
        "export queda en <salida>\\<nombre del dato>. Cualquier parámetro de "
        "ParameterSettings del Toothform.ini se puede pasar como param extra (ej. "
        "LimitX=3.0). El ejecutable tiene que estar en process_allowlist, y las rutas ser ASCII."
    ),
    params=(
        Param("ejecutable", required=True, doc="Ruta de Toothform.exe, ej. D:\\4in1\\Toothform-20260518\\Toothform.exe."),
        Param("carpeta", ParamType.PATH, default="", doc="Carpeta con los STL a exportar: TODOS los que haya adentro. Alternativa a 'archivos'."),
        Param(
            "archivos", ParamType.JSON, default=[],
            doc="Alternativa a 'carpeta': sólo estos archivos. ToothFORM por línea de comandos "
            "únicamente sabe cargar una carpeta entera, así que el tool los copia antes a una "
            "carpeta aparte adentro de 'salida' — con archivos grandes o en un share de red, esa "
            "copia se paga. Con la carpeta ya armada, conviene 'carpeta'.",
        ),
        Param("salida", ParamType.PATH, required=True, doc="Carpeta de salida: ahí caen el log, el JSON y <nombre del dato>\\ con el export."),
        Param("tipo", default="Type1", doc="Type1, Type2 o Type3, según cómo viene armado el dato."),
        Param("timeout", ParamType.FLOAT, default=900.0, doc="Segundos máximos para que ToothFORM termine."),
    ),
    extra_params=True,
    outputs=_SALIDAS_LOG + (
        Output("hubo_log", ParamType.STR, doc="'si' o 'no': ToothFORM sin log es un crash o una ruta mala, no un export fallido."),
        Output("exportados", ParamType.JSON, doc="Nombres de los datos que exportaron bien."),
        Output("carpeta_export", ParamType.PATH, doc="<salida>\\<nombre del primer dato exportado>, donde ToothFORM dejó los archivos."),
        Output("config", ParamType.PATH, doc="El JSON que se le pasó a ToothFORM, para revisarlo."),
    ),
)


def _exportar(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    process = ctx.port(port_names.PROCESS)
    ejecutable, carpeta, salida = ctx.params["ejecutable"], ctx.params["carpeta"], ctx.params["salida"]
    tipo = ctx.params["tipo"] or "Type1"

    if tipo not in _TIPOS:
        return ToolResult.err(f"'tipo' tiene que ser uno de {', '.join(_TIPOS)}, no '{tipo}'")
    desconocidos = sorted(set(ctx.extras) - set(_PARAMETROS_DEFAULT))
    if desconocidos:
        return ToolResult.err(
            f"parámetros que ToothFORM no conoce: {', '.join(desconocidos)}. "
            f"Válidos: {', '.join(_PARAMETROS_DEFAULT)}"
        )
    archivos = ctx.params.get("archivos") or []
    if bool(carpeta) == bool(archivos):
        return ToolResult.err(
            "hace falta 'carpeta' (todos los STL de adentro) o 'archivos' (sólo ésos), no las dos "
            "ni ninguna" if carpeta else "hace falta 'carpeta' o 'archivos'"
        )
    no_ascii = [r for r in (ejecutable, carpeta, salida, *archivos) if r and not str(r).isascii()]
    if no_ascii:
        return ToolResult.err(
            "ToothFORM no encuentra rutas con caracteres fuera de ASCII (acentos, ñ) y termina sin "
            f"exportar ni dejar log; usar una ruta o junction ASCII: {' · '.join(no_ascii)}"
        )

    fs.make_dirs(salida)
    if archivos:
        faltan = [a for a in archivos if not fs.exists(a) or fs.is_dir(a)]
        if faltan:
            return ToolResult.err(f"no existen estos archivos: {', '.join(faltan)}")
        # ToothFORM por cmd sólo sabe cargar una carpeta entera, así que los
        # elegidos se copian a una propia. Se vacía antes: lo que quedó de una
        # corrida anterior se exportaría de nuevo sin que nadie lo pidiera, y
        # ése es el tipo de error que se ve como un export "de más" y no como
        # una falla.
        carpeta = fs.join(salida, f"entrada-{ctx.case_id or 'export'}")
        if fs.exists(carpeta):
            fs.remove_tree(carpeta)
        fs.make_dirs(carpeta)
        for origen in archivos:
            fs.copy_file(origen, fs.join(carpeta, fs.basename(origen)))
        ctx.log(f"{len(archivos)} archivo(s) copiados a {carpeta} para exportarlos solos")
    elif not fs.exists(carpeta) or not fs.is_dir(carpeta):
        return ToolResult.err(f"no existe la carpeta con los STL: {carpeta}")

    previos = {e.name for e in _logs(fs, salida)}

    config = {
        "FilesToProcess": {"OpenFolder": carpeta, "Type": tipo},
        "General": {"ExportPrintPath": salida, "ExportFilePath": salida},
        "ParameterSettings": {**_PARAMETROS_DEFAULT, **{k: str(v) for k, v in ctx.extras.items()}},
    }
    ruta_config = fs.join(salida, f"toothform-{ctx.case_id or 'export'}.json")
    fs.write_text(ruta_config, json.dumps(config, indent=2))
    ctx.log(f"ToothFORM cmd: {tipo} de {carpeta} → {salida} ({ruta_config})")

    timeout = ctx.params["timeout"]
    maximo = int(ctx.config(MAX_SIMULTANEOS) or 0)
    if maximo > 0:
        if _TURNOS.en_curso >= maximo:
            ctx.log(f"esperando turno: {_TURNOS.en_curso} exportaciones en curso (máximo {maximo})")
        if not _TURNOS.tomar(maximo, timeout):
            return ToolResult.err(
                f"no hubo turno para exportar en {timeout:g} s: {_TURNOS.en_curso} exportaciones en curso "
                f"(máximo {maximo}, setting {MAX_SIMULTANEOS})"
            )
    try:
        corrida = process.run([ejecutable, ruta_config], cwd=fs.parent(ejecutable) or None, timeout=timeout)
    finally:
        if maximo > 0:
            _TURNOS.soltar()
    sin_log = dict(
        log_file="", checkLogResult={"log_file": "", "message": ""}, exitosos=0, fallidos=0,
        log_texto="", hubo_log="no", exportados=[], carpeta_export="", config=ruta_config,
    )
    if corrida.timed_out:
        return ToolResult.err(f"ToothFORM no terminó en {timeout:g} s", **sin_log)

    nuevos = [e for e in _logs(fs, salida) if e.name not in previos]
    if not nuevos:
        return ToolResult.err(
            f"ToothFORM terminó (exit {corrida.exit_code}) sin dejar un log en {salida}: "
            "la carpeta de STL o la de salida no existe para la app, o el JSON no se pudo leer",
            **sin_log,
        )
    reciente = max(nuevos, key=lambda e: e.modified_at)
    texto = fs.read_text(reciente.path, encoding="utf-8")
    exportados = _PATRON_EXITO.findall(texto)
    carpeta_export = fs.join(salida, exportados[0].split("-")[0]) if exportados else ""
    return _interpretar_log(
        ctx, reciente.name, texto,
        hubo_log="si", exportados=exportados, carpeta_export=carpeta_export, config=ruta_config,
    )


# ── add_qr ──────────────────────────────────────────────────────────────

ADD_QR = ToolManifest(
    id="toothform.add_qr",
    label="exportar (agregar QR)",
    category="TOOTHFORM",
    doc=(
        "Clickea 'Export' en la ventana de ToothFORM, que ya tiene que estar abierta "
        "con el STL cargado y el tipo de dato y 'QRcode' dejados como corresponde. No "
        "espera a que termine: para eso, 'verificar log' a continuación. Para el "
        "release 20260518 o posterior conviene 'exportar (línea de comandos)'."
    ),
    params=(
        Param("ventana", default="Toothform", doc="Parte del título de la ventana de la app."),
        Param(
            "proceso", doc="Opcional: ruta del ejecutable (ej. D:\\4in1\\recover\\Toothform.exe). "
            "Más preciso que el título si hay otras ventanas con 'Toothform' en el nombre.",
        ),
        Param("timeout", ParamType.FLOAT, default=15.0, doc="Segundos para encontrar la ventana y el botón."),
    ),
)


def _add_qr(ctx: ToolContext) -> ToolResult:
    window = ctx.port(port_names.WINDOW)
    timeout = ctx.params["timeout"]
    proceso = ctx.params.get("proceso") or None

    ventana = window.find_window(
        title=None if proceso else ctx.params["ventana"], process=proceso, timeout=timeout,
    )
    window.click(ventana, "Export", timeout=timeout)
    ctx.log(f"ToothFORM ({ventana.title}): Export clickeado")
    return ToolResult.ok("export disparado")


# ── check_log ───────────────────────────────────────────────────────────

CHECK_LOG = ToolManifest(
    id="toothform.check_log",
    label="verificar log",
    category="TOOTHFORM",
    doc=(
        "Busca en la carpeta de salida de ToothFORM el archivo .log más reciente, "
        "modificado hace menos de 'max_edad' segundos. Sin uno: loop (todavía no "
        "terminó, reintentar). Con uno: ok si no hubo fallas, err si las hubo."
    ),
    params=(
        Param("carpeta", ParamType.PATH, required=True, doc="La carpeta de salida de ToothFORM (FilePath / ExportPrintPath de Toothform.ini)."),
        Param(
            "max_edad", ParamType.FLOAT, default=900.0,
            doc="Un .log más viejo que esto (segundos) es de otro caso y no cuenta.",
        ),
    ),
    outputs=_SALIDAS_LOG,
)


def _check_log(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    carpeta = ctx.params["carpeta"]
    if not fs.exists(carpeta) or not fs.is_dir(carpeta):
        return ToolResult.err(f"no existe la carpeta de salida de ToothFORM: {carpeta}")

    logs = _logs(fs, carpeta)
    reciente = max(logs, key=lambda e: e.modified_at) if logs else None
    ahora = ctx.port(port_names.CLOCK).now()
    if reciente is None or ahora - reciente.modified_at > ctx.params["max_edad"]:
        ctx.log("todavía no hay un log nuevo de ToothFORM")
        return ToolResult.again("todavía no apareció el log del export")

    return _interpretar_log(ctx, reciente.name, fs.read_text(reciente.path, encoding="utf-8"))


# ── toothcam_enviar ─────────────────────────────────────────────────────

TOOTHCAM_ENVIAR = ToolManifest(
    id="toothform.toothcam_enviar",
    label="ToothCAM: enviar y esperar",
    category="TOOTHCAM",
    doc=(
        "Mueve 'archivos' a 'carpeta_watch' -la carpeta que ToothCAM ya tiene "
        "vigilada con 'Watch directory' + 'Scan dir.' + 'Compute' activados a "
        "mano en la app- y espera a que aparezca un .log nuevo en "
        "'carpeta_salida', reintentando cada 'intervalo' segundos hasta "
        "'timeout'. Interpreta el log con el mismo criterio que "
        "'verificar log' de ToothFORM (no se conoce todavía el formato real "
        "del log de ToothCAM). Todo o nada: si falta un archivo no mueve "
        "nada y corta antes de esperar -mover la mitad de un caso a la "
        "carpeta vigilada dejaría a ToothCAM procesando un set incompleto."
    ),
    params=(
        Param("archivos", ParamType.JSON, required=True, doc="Rutas de los STL/PTS del caso (todos los que ToothCAM necesite juntos: gum, tooth, att, etc.)."),
        Param("carpeta_watch", ParamType.PATH, required=True, doc="La carpeta que ToothCAM tiene asignada en 'Watch directory'."),
        Param("carpeta_salida", ParamType.PATH, required=True, doc="Donde ToothCAM deja el/los log (normalmente 'batch_result' dentro de la carpeta vigilada)."),
        Param("intervalo", ParamType.FLOAT, default=5.0, doc="Segundos entre cada chequeo de si ya apareció el log."),
        Param("timeout", ParamType.FLOAT, default=900.0, doc="Segundos máximos totales de espera antes de darse por vencido."),
    ),
    outputs=_SALIDAS_LOG + (
        Output("archivos_movidos", ParamType.JSON, doc="Rutas finales dentro de 'carpeta_watch', en el mismo orden que 'archivos'."),
    ),
)


def _toothcam_enviar(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    clock = ctx.port(port_names.CLOCK)
    archivos = ctx.params["archivos"]
    if not archivos:
        return ToolResult.err("'archivos' vacío: no hay nada para enviar a ToothCAM")

    carpeta_watch = ctx.params["carpeta_watch"]
    carpeta_salida = ctx.params["carpeta_salida"]
    if not fs.exists(carpeta_watch) or not fs.is_dir(carpeta_watch):
        return ToolResult.err(f"no existe la carpeta vigilada por ToothCAM: {carpeta_watch}")
    faltantes = [a for a in archivos if not fs.exists(a)]
    if faltantes:
        return ToolResult.err(f"no existen estos archivos, no se movió nada: {', '.join(faltantes)}")

    fs.make_dirs(carpeta_salida)
    previos = {e.name for e in _logs(fs, carpeta_salida)}

    movidos = [fs.move(origen, fs.join(carpeta_watch, fs.basename(origen))) for origen in archivos]
    ctx.log(f"{len(movidos)} archivo(s) movidos a {carpeta_watch} (ToothCAM en modo watch)")

    intervalo, timeout = ctx.params["intervalo"], ctx.params["timeout"]
    limite = clock.monotonic() + timeout
    sin_log = dict(
        log_file="", checkLogResult={"log_file": "", "message": ""}, exitosos=0, fallidos=0,
        log_texto="", archivos_movidos=movidos,
    )
    while True:
        nuevos = [e for e in _logs(fs, carpeta_salida) if e.name not in previos]
        if nuevos:
            reciente = max(nuevos, key=lambda e: e.modified_at)
            texto = fs.read_text(reciente.path, encoding="utf-8")
            return _interpretar_log(ctx, reciente.name, texto, archivos_movidos=movidos)
        if ctx.cancelled:
            return ToolResult.err("cancelado mientras esperaba el log de ToothCAM", **sin_log)
        if clock.monotonic() >= limite:
            return ToolResult.err(
                f"no apareció un log nuevo de ToothCAM en {timeout:g} s en {carpeta_salida}", **sin_log,
            )
        clock.sleep(min(intervalo, limite - clock.monotonic()), ctx.is_cancelled_check)


_TOOLS = (
    (EXPORTAR, _exportar),
    (ADD_QR, _add_qr),
    (CHECK_LOG, _check_log),
    (TOOTHCAM_ENVIAR, _toothcam_enviar),
)


def build_plugin() -> Plugin:
    return Plugin(manifest=MANIFEST, tools=[FunctionTool(manifest=m, fn=f) for m, f in _TOOLS])


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
