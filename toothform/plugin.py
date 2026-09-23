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

  Lo que se verificó en esa versión y el tool asume: el exit code es 2
  siempre que ToothFORM llegue a terminar —también con un JSON inexistente—,
  así que el resultado se lee del log y no del proceso. Cualquier otro
  código es que no llegó: 0xC0000005 cuando se cae solo, 1 cuando lo matan
  por afuera. Ahí el log, si lo hay, quedó a medias y no se puede creer —las
  líneas que alcanzó a escribir dicen "Export successfully" y leídas por
  palabras darían por bueno medio export.

  Por qué se cae no se sabe. Medido el 22/09/2026 sobre el caso BY733 (36
  STL, 595 MB): muere a los 32,5 s con 0xC0000005 habiendo llegado a 655 MB
  de espacio de direcciones y 522 MB de private bytes. `Toothform.exe` es
  x86 y sin LARGEADDRESSAWARE —techo de 2 GB—, así que ni se acercó: no es
  falta de memoria. Un caso de 3 STL y 20 MB exporta en 43 s, pero son casos
  distintos, o sea que tampoco está probado que sea la cantidad. Por eso el
  tool parte la carpeta en tandas de `max_mb_por_tanda` y corre el ejecutable
  una vez por tanda, copiando cada una a una carpeta propia —ToothFORM sólo
  sabe cargar una carpeta entera—: no arregla la caída, pero deja un export
  parcial en vez de nada y acota a qué datos mirarle.

  El log `<fecha>.<hora>.log` cae en `ExportPrintPath`; el export queda en
  `<ExportPrintPath>\\<nombre del dato>\\` (el prefijo del STL antes del
  primer `-`); y una ruta con caracteres fuera de ASCII en el JSON hace que
  no exporte nada ni deje log, en cualquier codificación. Por eso el tool
  corta antes de correr si alguna ruta no es ASCII.

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
externo que valga la pena modelar como arista. El log de ToothCAM (visto
en producción con BY275) va sumando una línea por dato mientras procesa
—`4\\7    <dato>    <fecha>    success`—, y el número de la derecha son los
archivos que vio hasta ahí, no el total del caso: crece mientras escanea.
Tampoco sirve el `finish.log` que deja: aparece cuando terminó de escanear,
no de procesar. Por eso el flujo le pasa el total de modelos del caso
(`esperados`, el conteo de "Buscar archivos") y el tool termina cuando el
log llega a ese número y, si se le da `patron_salida`, cuando están todos
los archivos de salida. Un log con otro formato se lee con
`_interpretar_log`, como el de ToothFORM.

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
from dataclasses import dataclass, field

from backend.core import ports as port_names
from backend.core.contract import (
    STATUS_OK,
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
from backend.core.ports import PortError

# Medido el 14/09/2026 con un STL de 20 MB: cada Toothform.exe usa un core
# (es monohilo) y llega a ~700 MB de RAM; dos en paralelo tardan lo mismo
# que uno. El límite lo pone la memoria, no la CPU.
MAX_SIMULTANEOS = "toothformMaxSimultaneos"

MANIFEST = PluginManifest(
    name="toothform",
    label="ToothFORM",
    version="0.5.1",
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


# Medido contra el release 20260518: ToothFORM devuelve 2 cuando llega a
# terminar, exporte bien o mal, y también con un JSON inexistente. Cualquier
# otro código es que no llegó: 0xC0000005 cuando se cae solo, 1 cuando lo
# matan por afuera. Por eso "distinto de cero" no sirve para decidir y "no es
# de los que deja al terminar" sí.
_TERMINA_EN = (0, 2)


def _termino(exit_code: int) -> bool:
    return exit_code in _TERMINA_EN


def _crasheo(exit_code: int) -> bool:
    """Si el código es una excepción de Windows (0xC0000005 y compañía). Sólo cambia el texto."""
    return exit_code < 0 or exit_code >= 0xC000_0000


def _como_termino(exit_code: int) -> str:
    if _crasheo(exit_code):
        return f"exit {exit_code}, 0x{exit_code & 0xFFFF_FFFF:08X}: excepción de Windows"
    return f"exit {exit_code}, cuando al terminar deja {' o '.join(str(c) for c in _TERMINA_EN)}"


_SALIDAS_LOG = (
    Output("log_file", ParamType.STR, doc="Nombre del archivo de log encontrado (vacío si no hubo)."),
    Output("checkLogResult", ParamType.JSON, doc="{log_file, message}, como lo devolvía el nodo legacy."),
    Output("exitosos", ParamType.INT),
    Output("fallidos", ParamType.INT),
    Output("log_texto", ParamType.STR),
)


def _leer_log(texto: str) -> tuple[bool, str, int, int]:
    """
    Qué dice un log de ToothFORM: (anduvo, por qué no, exitosos, fallidos).

    Aparte de `_interpretar_log`, que arma un `ToolResult`, porque exportar por
    tandas necesita los números de cada corrida para sumarlos y un solo
    resultado al final.
    """
    coincidencia = _PATRON_TOTAL.search(texto)
    if coincidencia is not None:
        total, exitosos, fallidos = (int(g) for g in coincidencia.groups())
        if fallidos > 0:
            return False, f"{fallidos} de {total} fallaron", exitosos, fallidos
        return True, "", exitosos, fallidos

    # Sin la línea de totales (otra versión de la app): decidir por las palabras.
    bajo = texto.lower()
    if "fail" in bajo or "error" in bajo:
        return False, "el log reporta una falla", 0, 1
    if "success" in bajo:
        return True, "", 1, 0
    return False, "el log no tiene un resultado reconocible", 0, 0


def _interpretar_log(ctx: ToolContext, nombre: str, texto: str, **extra) -> ToolResult:
    """ok si el log no reporta fallas, err si las hay. `extra` son salidas más del tool que llama."""
    primera_linea = next((l.strip() for l in texto.splitlines() if l.strip()), "")
    ctx.log(f"{nombre}: {primera_linea[:160]}")
    resultado = {"log_file": nombre, "message": primera_linea}
    comunes = dict(log_file=nombre, checkLogResult=resultado, log_texto=texto, **extra)

    anduvo, porque, exitosos, fallidos = _leer_log(texto)
    if anduvo:
        return ToolResult.ok(exitosos=exitosos, fallidos=fallidos, **comunes)
    return ToolResult.err(porque, exitosos=exitosos, fallidos=fallidos, **comunes)


def _logs(fs, carpeta: str):
    return [e for e in fs.list_dir(carpeta) if not e.is_dir and e.name.lower().endswith(".log")]


def _stl_de(fs, carpeta: str) -> list:
    return [e for e in fs.list_dir(carpeta) if not e.is_dir and e.name.lower().endswith(".stl")]


def _tandas(entradas: list, limite: int) -> list[list]:
    """
    Parte `[(ruta, bytes), ...]` en grupos que no pasen de `limite` bytes.

    Un archivo más grande que el límite se va solo en su grupo: partirlo no se
    puede, y dejarlo afuera sería exportar de menos sin decirlo.
    """
    grupos: list[list] = []
    actual: list = []
    peso = 0
    for entrada in entradas:
        if actual and peso + entrada[1] > limite:
            grupos.append(actual)
            actual, peso = [], 0
        actual.append(entrada)
        peso += entrada[1]
    if actual:
        grupos.append(actual)
    return grupos


_MB = 1024 * 1024


def _primera(texto: str) -> str:
    return next((l.strip() for l in texto.splitlines() if l.strip()), "")


def _peso(entradas: list) -> int:
    return sum(t for _ruta, t in entradas)


def _tamano(fs, ruta: str) -> int:
    try:
        return fs.stat(ruta).size
    except PortError:
        return 0


def _cuanto_carga(entradas: list) -> str:
    """Lo que ToothFORM va a cargar de una: es lo que después explica un 0xC0000005."""
    return f"; {len(entradas)} STL, {_peso(entradas) / _MB:.0f} MB a cargar de una"


def _listar_o_nada(fs, carpeta: str) -> list | None:
    try:
        return [(e.path, e.size) for e in _stl_de(fs, carpeta)]
    except PortError:
        return None  # el dato es para el diagnóstico: no vale frenar un export por él


def _sin_log(exit_code: int, carpeta: str) -> str:
    """
    Por qué no hay log. Dice qué pasó y dónde mirar, no por qué pasó: de la
    causa el ejecutable no dejó nada, y afirmarla manda a buscar donde no está.
    """
    if _termino(exit_code):
        return (
            f"ToothFORM terminó (exit {exit_code}) sin dejar un log: la carpeta de STL o la de "
            "salida no existe para la app, o el JSON no se pudo leer"
        )
    return (
        f"ToothFORM se cortó antes de escribir el log ({_como_termino(exit_code)}): no exportó "
        f"nada de {carpeta} y no dejó dicho por qué. Para acorralarlo, bajar "
        "'max_mb_por_tanda': con tandas más chicas el log dice cuáles alcanzó a exportar y "
        "la que se corta deja a la vista qué datos tiene adentro."
    )


@dataclass
class _Acumulado:
    """
    Lo que van dejando las tandas, para devolver un solo resultado al final.

    Con una sola tanda las salidas son las mismas que antes de que las tandas
    existieran: `log_file` el log, `log_texto` su texto y `checkLogResult` su
    primera línea. Con varias, `log_texto` es la concatenación con un
    encabezado por tanda y el `message` pasa a ser el resumen, porque la
    primera línea de la última tanda no describe el export.
    """

    config: str
    tandas: int
    exit_code: int = 0
    exitosos: int = 0
    fallidos: int = 0
    carpeta_export: str = ""
    nombres: list = field(default_factory=list)
    textos: list = field(default_factory=list)
    exportados: list = field(default_factory=list)
    vistos: set = field(default_factory=set)

    def sumar(self, fs, salida: str, nombre: str, texto: str) -> tuple[bool, str]:
        self.vistos.add(nombre)
        self.nombres.append(nombre)
        self.textos.append(texto)
        self.exportados.extend(_PATRON_EXITO.findall(texto))
        if not self.carpeta_export and self.exportados:
            self.carpeta_export = fs.join(salida, self.exportados[0].split("-")[0])
        anduvo, porque, exitosos, fallidos = _leer_log(texto)
        self.exitosos += exitosos
        self.fallidos += fallidos
        return anduvo, porque

    @property
    def salidas(self) -> dict:
        nombre = self.nombres[-1] if self.nombres else ""
        if len(self.nombres) > 1:
            texto = "\n".join(f"=== {n} ===\n{t}" for n, t in zip(self.nombres, self.textos))
            mensaje = f"{len(self.nombres)} tandas: {self.exitosos} exportados, {self.fallidos} fallidos"
        else:
            texto = self.textos[0] if self.textos else ""
            mensaje = _primera(texto)
        return dict(
            log_file=nombre, log_files=list(self.nombres),
            checkLogResult={"log_file": nombre, "message": mensaje},
            exitosos=self.exitosos, fallidos=self.fallidos, log_texto=texto,
            hubo_log="si" if self.nombres else "no", exportados=list(self.exportados),
            carpeta_export=self.carpeta_export, config=self.config,
            exit_code=self.exit_code, tandas=self.tandas,
        )

    def ok(self) -> ToolResult:
        return ToolResult.ok(**self.salidas)

    def err(self, mensaje: str) -> ToolResult:
        return ToolResult.err(mensaje, **self.salidas)

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
        "ventana (release 20260518 o posterior). Espera a que termine y lee el log. Si la "
        "carpeta pesa más de 'max_mb_por_tanda' la parte en tandas y lo corre una vez por "
        "tanda: una carpeta de caso entera lo mata sin exportar nada, y así lo que salió "
        "antes queda. Las salidas vienen unificadas y 'tandas' dice en cuántas fue. El "
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
        Param(
            "timeout", ParamType.FLOAT, default=900.0,
            doc="Segundos máximos para que ToothFORM termine. Es por tanda, no por el total: "
            "con varias tandas el tool corre el ejecutable una vez por cada una.",
        ),
        Param(
            "max_mb_por_tanda", ParamType.FLOAT, default=50.0,
            doc="Cuántos MB de STL le da a ToothFORM por vez. Por arriba de eso el tool parte "
            "la carpeta en tandas y lo corre una vez por tanda, juntando los resultados. Una "
            "carpeta de caso entera lo mata con 0xC0000005 sin dejar log y sin exportar nada; "
            "en tandas, lo que salió antes de la que se corta queda exportado y el log dice "
            "hasta dónde llegó. Más chico acorrala mejor pero tarda más: son ~14 s por STL "
            "más el arranque de cada tanda. 0 desactiva las tandas (todo de una, como antes).",
        ),
    ),
    extra_params=True,
    outputs=_SALIDAS_LOG + (
        Output("hubo_log", ParamType.STR, doc="'si' o 'no': ToothFORM sin log es un crash o una ruta mala, no un export fallido."),
        Output("exportados", ParamType.JSON, doc="Nombres de los datos que exportaron bien."),
        Output("carpeta_export", ParamType.PATH, doc="<salida>\\<nombre del primer dato exportado>, donde ToothFORM dejó los archivos."),
        Output("config", ParamType.PATH, doc="El JSON que se le pasó a ToothFORM, para revisarlo."),
        Output(
            "log_files", ParamType.JSON,
            doc="Los logs de todas las tandas, en orden. Con una sola tanda es `[log_file]`.",
        ),
        Output("tandas", ParamType.INT, doc="En cuántas corridas de ToothFORM se partió el export."),
        Output(
            "exit_code", ParamType.INT,
            doc="Con qué terminó Toothform.exe. Cuando llega a terminar deja 2, exporte bien o "
            "mal, así que no sirve para saber cómo le fue —eso sale del log— pero sí para "
            "saber si llegó: cualquier otro código es que se cortó. 3221225477 (0xC0000005) "
            "es que se cayó solo; 1, que lo mataron por afuera.",
        ),
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

    # Qué se va a exportar, venga de 'archivos' o del contenido de 'carpeta'.
    # Hacen falta las rutas con su tamaño para armar las tandas; sin ellas —un
    # share que no se deja listar— se le pasa la carpeta entera, que es como se
    # comportaba el tool antes de que las tandas existieran.
    fuentes: list | None = None
    if archivos:
        faltan = [a for a in archivos if not fs.exists(a) or fs.is_dir(a)]
        if faltan:
            return ToolResult.err(f"no existen estos archivos: {', '.join(faltan)}")
        fuentes = [(a, _tamano(fs, a)) for a in archivos]
    else:
        if not fs.exists(carpeta) or not fs.is_dir(carpeta):
            return ToolResult.err(f"no existe la carpeta con los STL: {carpeta}")
        try:
            fuentes = [(e.path, e.size) for e in _stl_de(fs, carpeta)]
        except PortError as exc:
            ctx.log(f"no se pudo listar {carpeta} ({exc}): va entera, sin tandas", "warning")
        if fuentes is not None and not fuentes:
            return ToolResult.err(f"no hay ningún .stl en {carpeta}")

    limite = int((ctx.params["max_mb_por_tanda"] or 0) * _MB)
    if fuentes is None or (not archivos and (limite <= 0 or _peso(fuentes) <= limite)):
        # Entra de una y ya está en su propia carpeta: se la pasamos tal cual,
        # sin copiar nada. Es el camino de siempre.
        grupos: list = [None]
    else:
        grupos = _tandas(fuentes, limite) if limite > 0 else [fuentes]
        if len(grupos) > 1:
            ctx.log(
                f"{len(fuentes)} STL, {_peso(fuentes) / _MB:.0f} MB: van en {len(grupos)} tandas "
                f"de hasta {limite / _MB:.0f} MB, que es lo que ToothFORM aguanta de una"
            )

    # ToothFORM por cmd sólo sabe cargar una carpeta entera, así que cada tanda
    # se copia a una propia. Se vacía antes de cada una: lo que quedó de la
    # anterior se exportaría de nuevo sin que nadie lo pidiera, y ése es el tipo
    # de error que se ve como un export "de más" y no como una falla.
    entrada = fs.join(salida, f"entrada-{ctx.case_id or 'export'}")
    ruta_config = fs.join(salida, f"toothform-{ctx.case_id or 'export'}.json")
    config = {
        "FilesToProcess": {"OpenFolder": carpeta, "Type": tipo},
        "General": {"ExportPrintPath": salida, "ExportFilePath": salida},
        "ParameterSettings": {**_PARAMETROS_DEFAULT, **{k: str(v) for k, v in ctx.extras.items()}},
    }

    corrido = _Acumulado(config=ruta_config, tandas=len(grupos))
    corrido.vistos = {e.name for e in _logs(fs, salida)}

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
        for numero, grupo in enumerate(grupos, 1):
            de = f"tanda {numero}/{len(grupos)}: " if len(grupos) > 1 else ""
            if grupo is None:
                origen = carpeta
                # `fuentes` ya tiene el listado salvo que el share no se deje
                # mirar; no se lo vuelve a pedir sólo para el log.
                medido = fuentes if fuentes is not None else _listar_o_nada(fs, carpeta)
            else:
                origen = entrada
                medido = grupo
                if fs.exists(entrada):
                    fs.remove_tree(entrada)
                fs.make_dirs(entrada)
                for ruta, _bytes in grupo:
                    fs.copy_file(ruta, fs.join(entrada, fs.basename(ruta)))

            config["FilesToProcess"]["OpenFolder"] = origen
            fs.write_text(ruta_config, json.dumps(config, indent=2))
            cuantos = _cuanto_carga(medido) if medido is not None else ""
            ctx.log(f"{de}ToothFORM cmd: {tipo} de {origen} → {salida} ({ruta_config}){cuantos}")

            corrida = process.run(
                [ejecutable, ruta_config], cwd=fs.parent(ejecutable) or None, timeout=timeout,
            )
            corrido.exit_code = corrida.exit_code
            if corrida.timed_out:
                return corrido.err(f"{de}ToothFORM no terminó en {timeout:g} s")

            nuevos = [e for e in _logs(fs, salida) if e.name not in corrido.vistos]
            if not nuevos:
                return corrido.err(de + _sin_log(corrida.exit_code, origen))

            reciente = max(nuevos, key=lambda e: e.modified_at)
            texto = fs.read_text(reciente.path, encoding="utf-8")
            anduvo, porque = corrido.sumar(fs, salida, reciente.name, texto)
            ctx.log(f"{de}{reciente.name}: {_primera(texto)[:160]}")

            # No llegó a terminar. Con la línea de totales igual alcanzó a decir
            # cómo le fue, así que se respeta: lo que se cortó es de después.
            # Sin ella el log quedó a medias, y lo que alcanzó a escribir dice
            # "Export successfully": leído por palabras da ok, el flujo seguiría
            # por la rama buena y el caso avanzaría con la mitad de los datos.
            if not _termino(corrida.exit_code):
                if _PATRON_TOTAL.search(texto) is None:
                    return corrido.err(
                        f"{de}ToothFORM se cortó exportando ({_como_termino(corrida.exit_code)}): "
                        f"{reciente.name} quedó sin la línea de totales, con "
                        f"{len(corrido.exportados)} dato(s) exportado(s) de los que haya habido"
                    )
                ctx.log(
                    f"{de}ToothFORM dejó el log entero pero {_como_termino(corrida.exit_code)}",
                    "warning",
                )
            if not anduvo:
                return corrido.err(de + porque)
    finally:
        if maximo > 0:
            _TURNOS.soltar()

    # Terminó bien: las copias de la última tanda ya no le sirven a nadie y se
    # acumulan una por caso. Si hubo error quedan, que es de lo único que se
    # puede reproducir la corrida que falló.
    if fs.exists(entrada):
        fs.remove_tree(entrada)
    if len(grupos) > 1:
        ctx.log(f"{len(grupos)} tandas: {corrido.exitosos} exportados, {corrido.fallidos} fallidos")
    return corrido.ok()


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
        "Mueve (o copia, con 'copiar') 'archivos' -o todo lo que haya en 'carpeta'- a 'carpeta_watch' -la carpeta que ToothCAM ya tiene "
        "vigilada con 'Watch directory' + 'Scan dir.' + 'Compute' activados a "
        "mano en la app- y espera a que aparezca un .log nuevo en "
        "'carpeta_salida', reintentando cada 'intervalo' segundos hasta "
        "'timeout'. Termina cuando el log tiene 'esperados' datos (uno por "
        "modelo, ej. el conteo de 'Buscar archivos') y, si se pasa "
        "'patron_salida', cuando en 'carpeta_salida' hay esa misma cantidad de "
        "archivos de salida: ok si todos dicen success, err nombrando los que "
        "no. Un log con otro formato se lee como el de ToothFORM. Todo o nada: si falta un archivo no mueve "
        "nada y corta antes de esperar -mover la mitad de un caso a la "
        "carpeta vigilada dejaría a ToothCAM procesando un set incompleto."
    ),
    params=(
        Param(
            "carpeta", ParamType.PATH, default="",
            doc="Carpeta con los archivos del caso: se mueven TODOS los que haya adentro (no las "
            "subcarpetas), sin filtrar por extensión, porque ToothCAM necesita el set entero. "
            "Alternativa a 'archivos', para cuando lo que llega de un nodo anterior es una ruta y no una lista.",
        ),
        Param(
            "archivos", ParamType.JSON, default=[],
            doc="Alternativa a 'carpeta': rutas de los STL/PTS del caso (todos los que ToothCAM "
            "necesite juntos: gum, tooth, att, etc.).",
        ),
        Param(
            "copiar", ParamType.BOOL, default=False,
            doc="Copiar en vez de mover: los originales quedan donde estaban. Sin tildar, los mueve.",
        ),
        Param(
            "carpeta_watch", ParamType.PATH, required=True,
            doc="La carpeta que ToothCAM tiene asignada en 'Watch directory', o una subcarpeta del "
            "caso adentro de ella (ej. ...\\T1-C-INPUT\\{id_externo}): si no existe pero la de "
            "arriba sí, la crea antes de mandar los archivos.",
        ),
        Param(
            "carpeta_salida", ParamType.PATH, required=True,
            doc="Donde aparece el log de ToothCAM. Puede ser la carpeta que ToothCAM crea sola para el "
            "caso (ej. ...\\OUTPUT\\{id_externo}): si todavía no existe, espera a que aparezca en vez "
            "de crearla, para no adelantársele a la app.",
        ),
        Param("intervalo", ParamType.FLOAT, default=5.0, doc="Segundos entre cada chequeo de si ya apareció el log."),
        Param("timeout", ParamType.FLOAT, default=900.0, doc="Segundos máximos totales de espera antes de darse por vencido."),
        Param(
            "esperados", ParamType.INT, default=0,
            doc="Cuántos modelos tiene que procesar ToothCAM: uno por modelo 3D, ej. {cantidad} de "
            "'Buscar archivos'. El tool termina cuando el log llega a esa cantidad. 0 = sin control: "
            "termina cuando la última línea del log dice n\\n, que puede ser antes de tiempo "
            "porque ToothCAM sigue sumando archivos mientras escanea.",
        ),
        Param(
            "patron_salida", default="",
            doc="Regex opcional de los archivos que ToothCAM deja por cada modelo en "
            "'carpeta_salida' (ej. el de los STL de entrada con .txt en vez de .stl). Con "
            "'esperados': cuando el log está completo, cada modelo con success tiene que tener "
            "su archivo (uno cuyo nombre empiece con el del modelo); si falta alguno, espera un "
            "poco por si se está escribiendo y después es err nombrándolo. Se busca en "
            "subcarpetas también y sin distinguir mayúsculas.",
        ),
    ),
    outputs=_SALIDAS_LOG + (
        Output("archivos_movidos", ParamType.JSON, doc="Rutas finales dentro de 'carpeta_watch' (movidos o copiados), en el mismo orden que 'archivos' (o que el listado de 'carpeta')."),
        Output(
            "sin_salida", ParamType.JSON,
            doc="Con 'patron_salida': los modelos que el log da por success pero no dejaron su archivo.",
        ),
    ),
)


# El log de ToothCAM, visto en producción (BY275, 23/09/2026): una línea por
# dato a medida que los procesa, "<n>\<vistos>\t<dato>\t<fecha>\t<estado>":
#   1\1   BY275-L06-A  2026-09-23-15-43-41  success
#   2\3   BY275-L00-B  ...                  success
#   4\7   BY275-L03-A  ...                  success
# El número de la derecha NO es el total del caso: son los archivos que
# ToothCAM vio hasta ese momento, y crece mientras sigue escaneando. Tampoco
# sirve el "finish.log" que deja en la carpeta ("finish in this folder"):
# aparece cuando terminó de escanear, segundos después de la copia, no de
# procesar. Lo que dice que terminó es que el log llegue al total de modelos
# del caso, que el flujo ya sabe ('esperados').
_PATRON_TOOTHCAM = re.compile(r"^\s*(\d+)\s*[\\/]\s*(\d+)\s+(\S+)\s+\S+\s+(\S+)\s*$", re.M)


def _datos_toothcam(texto: str) -> list[tuple[str, str]] | None:
    """[(dato, estado), ...] del log de ToothCAM, o None si no tiene ese formato."""
    lineas = _PATRON_TOOTHCAM.findall(texto)
    if not lineas:
        return None
    return [(dato, estado) for _n, _vistos, dato, estado in lineas]


def _fallidos(datos: list) -> list[str]:
    return [dato for dato, estado in datos if estado.lower() != "success"]


def _resultado_toothcam(
    ctx: ToolContext, nombre: str, texto: str, datos: list, esperados: int, sin_salida: list | None = None, **extra,
) -> ToolResult:
    fallidos = _fallidos(datos)
    ultima = next((l.strip() for l in reversed(texto.splitlines()) if l.strip()), "")
    ctx.log(f"{nombre}: {len(datos)} procesados, {len(fallidos)} con falla")
    comunes = dict(
        log_file=nombre, checkLogResult={"log_file": nombre, "message": ultima}, log_texto=texto,
        exitosos=len(datos) - len(fallidos), fallidos=len(fallidos), sin_salida=sin_salida or [], **extra,
    )
    if fallidos:
        return ToolResult.err(f"{len(fallidos)} de {len(datos)} fallaron: {', '.join(fallidos)}", **comunes)
    if esperados and len(datos) != esperados:
        return ToolResult.err(
            f"ToothCAM terminó con {len(datos)} procesados y se esperaban {esperados}", **comunes,
        )
    if sin_salida:
        return ToolResult.err(
            f"el log da success pero no dejaron su archivo de salida: {', '.join(sin_salida)}", **comunes,
        )
    return ToolResult.ok(**comunes)


def _completo_sin_fin(texto: str) -> bool:
    """Sin 'esperados': la última línea tiene n == vistos. Puede cortar antes de tiempo."""
    lineas = _PATRON_TOOTHCAM.findall(texto)
    return bool(lineas) and lineas[-1][0] == lineas[-1][1]


def _es_finish(nombre: str) -> bool:
    return nombre.lower().rsplit(".", 1)[0] == "finish"


def _sin_salida(datos: list, salidas: list[str]) -> list[str]:
    """Los modelos con success en el log que no tienen un archivo de salida con su nombre."""
    nombres = [ruta.replace("\\", "/").rsplit("/", 1)[-1].lower() for ruta in salidas]
    return [
        dato for dato, estado in datos
        if estado.lower() == "success" and not any(n.startswith(dato.lower()) for n in nombres)
    ]


# Cuánto más se espera, con el log ya completo, a que aparezcan las salidas
# que faltan. En BY275 los .txt estaban antes que el log, así que una que
# falta a esa altura casi seguro no viene: es para no cortar por un archivo
# que se está copiando, no para esperar de verdad.
_GRACIA_SALIDAS = 30.0


def _salidas(fs, carpeta: str, patron) -> list[str]:
    """Los archivos de 'carpeta' (y subcarpetas) que matchean 'patron', o ninguno si no existe todavía."""
    if patron is None or not fs.exists(carpeta):
        return []
    return sorted(e.path for e in fs.walk(carpeta) if not e.is_dir and patron.search(e.name))


def _logs_si_hay(fs, carpeta: str) -> list:
    """Los .log de 'carpeta', o ninguno si ToothCAM todavía no la creó."""
    if not fs.exists(carpeta):
        return []
    return _logs(fs, carpeta)


def _misma_carpeta(a: str, b: str) -> bool:
    def normal(ruta: str) -> str:
        return ruta.replace("\\", "/").rstrip("/").lower()
    return normal(a) == normal(b)


def _toothcam_enviar(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    clock = ctx.port(port_names.CLOCK)
    carpeta = ctx.params.get("carpeta") or ""
    archivos = ctx.params.get("archivos") or []
    if carpeta and archivos:
        return ToolResult.err("hace falta 'carpeta' o 'archivos', no las dos")
    if not carpeta and not archivos:
        return ToolResult.err("hace falta 'carpeta' o 'archivos': no hay nada para enviar a ToothCAM")

    carpeta_watch = ctx.params["carpeta_watch"]
    carpeta_salida = ctx.params["carpeta_salida"]
    crear_watch = not fs.exists(carpeta_watch)
    arriba = fs.parent(carpeta_watch)
    if crear_watch and not (fs.exists(arriba) and fs.is_dir(arriba)):
        return ToolResult.err(
            f"no existe la carpeta vigilada por ToothCAM: {carpeta_watch} (ni {arriba}, "
            "que es donde se la crearía)"
        )
    if not crear_watch and not fs.is_dir(carpeta_watch):
        return ToolResult.err(f"la carpeta vigilada por ToothCAM no es una carpeta: {carpeta_watch}")
    if carpeta:
        if _misma_carpeta(carpeta, carpeta_watch):
            return ToolResult.err(f"'carpeta' es la misma carpeta vigilada por ToothCAM: {carpeta}")
        if not fs.exists(carpeta) or not fs.is_dir(carpeta):
            return ToolResult.err(f"no existe la carpeta con los archivos del caso: {carpeta}")
        try:
            archivos = sorted(e.path for e in fs.list_dir(carpeta) if not e.is_dir)
        except PortError as exc:
            return ToolResult.err(f"no se pudo listar {carpeta}: {exc}")
        if not archivos:
            return ToolResult.err(f"no hay ningún archivo en {carpeta}: nada para enviar a ToothCAM")
    faltantes = [a for a in archivos if not fs.exists(a)]
    if faltantes:
        return ToolResult.err(f"no existen estos archivos, no se movió nada: {', '.join(faltantes)}")

    esperados = int(ctx.params.get("esperados") or 0)
    patron_salida = None
    if (ctx.params.get("patron_salida") or "").strip():
        if not esperados:
            return ToolResult.err("'patron_salida' necesita 'esperados': sin el total no hay contra qué contar")
        try:
            patron_salida = re.compile(ctx.params["patron_salida"].strip(), re.I)
        except re.error as exc:
            return ToolResult.err(f"'patron_salida' no es un regex válido: {exc}")
    previos = {e.name: e.modified_at for e in _logs_si_hay(fs, carpeta_salida)}
    if crear_watch:
        fs.make_dirs(carpeta_watch)
        ctx.log(f"creada {carpeta_watch} para el caso")

    copiar = bool(ctx.params.get("copiar"))
    enviar = fs.copy_file if copiar else fs.move
    movidos = [enviar(origen, fs.join(carpeta_watch, fs.basename(origen))) for origen in archivos]
    ctx.log(
        f"{len(movidos)} archivo(s) {'copiados' if copiar else 'movidos'} a {carpeta_watch} "
        "(ToothCAM en modo watch)"
    )

    intervalo, timeout = ctx.params["intervalo"], ctx.params["timeout"]
    limite = clock.monotonic() + timeout
    sin_log = dict(
        log_file="", checkLogResult={"log_file": "", "message": ""}, exitosos=0, fallidos=0,
        log_texto="", archivos_movidos=movidos, sin_salida=[],
    )
    visto = None  # el último progreso anotado, para no repetirlo
    completo_en = None  # cuándo el log llegó a 'esperados', para la gracia de las salidas
    while True:
        # Nuevo es el que no estaba o el que cambió: ToothCAM le pone al log
        # el nombre del caso, así que en una segunda corrida es el mismo archivo.
        # "finish.log" no es el log del caso (ver _PATRON_TOOTHCAM).
        nuevos = [
            e for e in _logs_si_hay(fs, carpeta_salida)
            if previos.get(e.name) != e.modified_at and not _es_finish(e.name)
        ]
        leidos = [(e, fs.read_text(e.path, encoding="utf-8")) for e in sorted(nuevos, key=lambda e: e.modified_at, reverse=True)]
        de_toothcam = [(e, t, _datos_toothcam(t)) for e, t in leidos if _datos_toothcam(t) is not None]
        salidas = _salidas(fs, carpeta_salida, patron_salida)

        if de_toothcam:
            reciente, texto, datos = de_toothcam[0]
            fallidos = _fallidos(datos)
            faltan = _sin_salida(datos, salidas) if patron_salida is not None else []
            if esperados:
                # El log completo es el fin. Si falta alguna salida se le da
                # una gracia corta y después se informa, en vez de esperar
                # hasta el timeout por un archivo que no va a venir (BY275-L04-A).
                completo = len(datos) >= esperados
                if completo and completo_en is None:
                    completo_en = clock.monotonic()
                termino = completo and (
                    not faltan or clock.monotonic() - completo_en >= max(_GRACIA_SALIDAS, 3 * intervalo)
                )
            else:
                termino = _completo_sin_fin(texto)
            if termino:
                if patron_salida is not None:
                    ctx.log(
                        f"ToothCAM terminó: {len(datos)} de {esperados} en {reciente.name} y "
                        f"{len(salidas)} archivo(s) de salida"
                        + (f"; sin salida: {', '.join(faltan)}" if faltan else "")
                    )
                elif esperados:
                    ctx.log(f"ToothCAM terminó: {len(datos)} de {esperados} en {reciente.name}")
                return _resultado_toothcam(
                    ctx, reciente.name, texto, datos, esperados, sin_salida=faltan, archivos_movidos=movidos,
                )
            cuantos = f"{len(datos)} de {esperados}" if esperados else f"{len(datos)} procesados"
            de_salida = f", {len(salidas)} de salida" if patron_salida is not None else ""
            progreso = (len(datos), len(salidas))
            if progreso != visto:
                visto = progreso
                ctx.log(f"ToothCAM va {cuantos}{de_salida} ({datos[-1][0]}: {datos[-1][1]})")
            sin_log = dict(
                sin_log, log_file=reciente.name, log_texto=texto,
                checkLogResult={"log_file": reciente.name, "message": f"{cuantos}{de_salida}"},
                exitosos=len(datos) - len(fallidos), fallidos=len(fallidos),
            )
        elif leidos and not esperados:
            # Otro formato (el de ToothFORM): se lee como antes, de una. Con
            # 'esperados' no: el flujo sabe que es ToothCAM y un log ajeno no
            # puede darlo por terminado.
            reciente, texto = leidos[0]
            return _interpretar_log(ctx, reciente.name, texto, archivos_movidos=movidos)

        if ctx.cancelled:
            return ToolResult.err("cancelado mientras esperaba el log de ToothCAM", **sin_log)
        if clock.monotonic() >= limite:
            if sin_log["log_file"]:
                return ToolResult.err(
                    f"ToothCAM no terminó en {timeout:g} s: va "
                    f"{sin_log['checkLogResult']['message']}", **sin_log,
                )
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
