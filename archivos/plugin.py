"""
Plugin `archivos` — mover, copiar, eliminar y renombrar contra el port `fs`.

`renombrar_lote` renombra una lista entera (las `rutas` de `buscar`) con un
regex: saca grupos de cada nombre y arma el nuevo con ellos.

Vive fuera de `webapp/` y de `backend/` a propósito: es la prueba de que un
plugin de verdad externo (el que instalaría un tercero por entry point, no el
atajo `local_plugins` que usa `connections`) funciona sin tocar nada del
núcleo — se carga por ruta con `plugins: {"archivos": "plugins/archivos"}` en
cualquiera de las tools de este MCP (list_tools, check_flow, dry_run_flow,
run_action, run_flow).

`copiar` y `eliminar` no reciben un tipo: miran con `fs.is_dir(...)` si el
origen es una carpeta o un archivo y eligen `*_file`/`*_tree` solas — pedirle
al usuario del flujo que sepa de antemano qué es cada ruta sería repetir lo
que el propio filesystem ya sabe.
"""

from __future__ import annotations

import json
import posixpath
import re

from backend.core import ports as port_names
from backend.core.contract import (
    FunctionTool,
    Output,
    Param,
    ParamType,
    Plugin,
    PluginManifest,
    ToolContext,
    ToolManifest,
    ToolResult,
)
from backend.core.ports import PortError

MANIFEST = PluginManifest(
    name="archivos",
    label="Archivos",
    version="0.4.0",
    doc="Mover, copiar, eliminar y renombrar archivos o carpetas, contra el port fs.",
    ports=(port_names.FS,),
)

# ── mover ───────────────────────────────────────────────────────────────

MOVER = ToolManifest(
    id="archivos.mover",
    label="mover",
    category="ARCHIVOS",
    doc="Mueve un archivo o una carpeta entera a otra ruta.",
    params=(
        Param("origen", ParamType.PATH, required=True),
        Param("destino", ParamType.PATH, required=True, doc="Ruta final, no la carpeta contenedora."),
        Param(
            "si_existe", ParamType.ENUM, default="adentro", choices=("adentro", "reemplazar", "error"),
            doc="Qué hacer si 'destino' ya existe. 'adentro' (como siempre): si es una carpeta, lo "
            "deja adentro de ella. 'reemplazar': BORRA lo que haya en 'destino' y deja el origen en "
            "su lugar; para reprocesar un caso cuya salida anterior ya está. 'error': no mueve nada.",
        ),
    ),
    outputs=(Output("ruta", ParamType.PATH),),
)


def _misma_o_adentro(ruta: str, carpeta: str) -> bool:
    """
    Si `ruta` es `carpeta` o cuelga de ella, sin distinguir mayúsculas ni
    separador, y resolviendo los `..` (un flujo arma `{carpeta}/../{id}`).
    """
    r = posixpath.normpath(ruta.replace("\\", "/")).lower()
    c = posixpath.normpath(carpeta.replace("\\", "/")).lower()
    return r == c or r.startswith(c.rstrip("/") + "/")


def _mover(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    origen, destino = ctx.params["origen"], ctx.params["destino"]
    si_existe = ctx.params.get("si_existe") or "adentro"
    if not fs.exists(origen):
        return ToolResult.err(f"no existe: {origen}")

    # Existe por el reproceso de un caso en BOT.1: el export de ToothFORM se
    # mueve de la carpeta local a <caso>\<id>, y si esa carpeta ya estaba —de
    # la corrida anterior— el mover la metía ADENTRO (<caso>\<id>\<id>), que
    # es justo lo que después encontraba 'buscar', o fallaba la vez siguiente.
    if fs.exists(destino) and si_existe != "adentro":
        if si_existe == "error":
            return ToolResult.err(f"ya existe {destino}; no se movió nada", error_kind="destino_existe")
        if _misma_o_adentro(origen, destino):
            return ToolResult.err(f"'{origen}' está adentro de '{destino}': reemplazarlo lo borraría")
        try:
            if fs.is_dir(destino):
                fs.remove_tree(destino)
            else:
                fs.remove_file(destino)
        except PortError as exc:
            return ToolResult.err(f"no se pudo borrar {destino} para reemplazarlo: {exc}")
        ctx.log(f"se borró {destino} para reemplazarlo", "warning")

    final = fs.move(origen, destino)
    ctx.log(f"{origen} -> {final}")
    return ToolResult.ok(ruta=final)


# ── copiar ──────────────────────────────────────────────────────────────

COPIAR = ToolManifest(
    id="archivos.copiar",
    label="copiar",
    category="ARCHIVOS",
    doc="Copia un archivo o una carpeta entera. El origen queda intacto.",
    params=(
        Param("origen", ParamType.PATH, required=True),
        Param("destino", ParamType.PATH, required=True, doc="Ruta final, no la carpeta contenedora."),
    ),
    outputs=(Output("ruta", ParamType.PATH),),
)


def _copiar(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    origen = ctx.params["origen"]
    if not fs.exists(origen):
        return ToolResult.err(f"no existe: {origen}")
    destino = ctx.params["destino"]
    final = fs.copy_tree(origen, destino) if fs.is_dir(origen) else fs.copy_file(origen, destino)
    ctx.log(f"{origen} -> {final}")
    return ToolResult.ok(ruta=final)


# ── eliminar ────────────────────────────────────────────────────────────

ELIMINAR = ToolManifest(
    id="archivos.eliminar",
    label="eliminar",
    category="ARCHIVOS",
    doc="Borra un archivo o una carpeta entera, con todo lo que tenga adentro.",
    # A diferencia de mover/copiar/renombrar, esto no se deshace: lo que se
    # borra no queda en ningún lado. Un actor de tipo `agent` no puede
    # correrlo sin que quien opera la instalación se lo habilite.
    dangerous=True,
    params=(Param("ruta", ParamType.PATH, required=True),),
)


def _eliminar(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    ruta = ctx.params["ruta"]
    if not fs.exists(ruta):
        return ToolResult.err(f"no existe: {ruta}")
    if fs.is_dir(ruta):
        fs.remove_tree(ruta)
    else:
        fs.remove_file(ruta)
    ctx.log(f"eliminado {ruta}")
    return ToolResult.ok(f"eliminado {ruta}")


# ── renombrar ───────────────────────────────────────────────────────────

RENOMBRAR = ToolManifest(
    id="archivos.renombrar",
    label="renombrar",
    category="ARCHIVOS",
    doc="Cambia el nombre de un archivo o carpeta, sin sacarlo de su carpeta.",
    params=(
        Param("ruta", ParamType.PATH, required=True),
        Param("nombre_nuevo", required=True, doc="Sólo el nombre, no una ruta."),
    ),
    outputs=(Output("ruta", ParamType.PATH),),
)


def _renombrar(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    ruta = ctx.params["ruta"]
    if not fs.exists(ruta):
        return ToolResult.err(f"no existe: {ruta}")
    final = fs.rename(ruta, ctx.params["nombre_nuevo"])
    ctx.log(f"{ruta} -> {final}")
    return ToolResult.ok(ruta=final)


# ── renombrar_lote ──────────────────────────────────────────────────────

# `<grupo>` y no `{grupo}`: lo que va entre llaves lo resuelve el flujo contra
# el row antes de que el tool lo vea, así que `{arcada}` quedaría literal —o
# peor, tomaría el valor de una columna que se llame igual—. Los `<>` no pueden
# estar en un nombre de archivo de Windows, así que no chocan con nada, y dejan
# mezclar las dos cosas: `{id_externo}_<arcada>_<step:02>.stl`.
_GRUPO = re.compile(r"<(\w+)(?::(0\d+))?>")

RENOMBRAR_LOTE = ToolManifest(
    id="archivos.renombrar_lote",
    label="renombrar con patrón",
    category="ARCHIVOS",
    doc=(
        "Renombra una LISTA de archivos (ej. las 'rutas' de buscar): a cada nombre le pasa "
        "'patron', saca sus grupos y arma el nombre nuevo con 'nombre_nuevo'. Con 'destino' "
        "los deja en esa carpeta en vez de en la suya. Antes de tocar nada revisa todos: si "
        "alguno no coincide, si dos terminan con el mismo nombre o si ya existe el destino, "
        "no mueve ninguno."
    ),
    params=(
        Param(
            "rutas", ParamType.JSON, required=True,
            doc="La lista de rutas, normalmente {rutas} de 'buscar'. Una sola va entre corchetes: [\"C:/x/a.stl\"].",
        ),
        Param(
            "patron", required=True,
            doc="Expresión regular sobre el NOMBRE del archivo (sin carpeta), sin distinguir mayúsculas, "
            "con grupos con nombre. Ej.: ^Template\\d+(-0)?_(?P<arcada>lower|upper)_Stage\\d+_step"
            "(?P<step>\\d+)_(?P<caso>.+)\\.stl$ . En el .mmd, si lleva | o una coma va entre "
            "#quot;…#quot; (patron=#quot;…(lower|upper)…#quot;), y sin llaves de repetición ({2}): "
            "el flujo las lee como variable; repetí el \\d.",
        ),
        Param(
            "nombre_nuevo", required=True,
            doc="El nombre que queda, con los grupos entre <>: <caso>_<arcada>_<step>.stl . "
            "<step:02> rellena con ceros a 2 dígitos (1 queda 01). <1>, <2>… son los grupos por número. "
            "Las variables del flujo van como siempre: {id_externo}_<arcada>.stl",
        ),
        Param(
            "destino", ParamType.PATH,
            doc="Carpeta donde dejarlos. Vacío = cada uno en su carpeta (renombrar). Si no existe se crea.",
        ),
        Param(
            "modo", ParamType.ENUM, default="mover", choices=("mover", "copiar"),
            doc="'mover' saca el original (renombrar de verdad). 'copiar' deja el original como estaba.",
        ),
        Param(
            "saltear_sin_coincidencia", ParamType.BOOL, default=False,
            doc="Sin marcar, un archivo que no coincide con 'patron' frena todo. Marcado, se lo deja "
            "como está y se sigue con los demás (el log dice cuáles).",
        ),
        Param(
            "pisar", ParamType.BOOL, default=False,
            doc="Sin marcar, si ya existe un archivo con el nombre nuevo no se toca nada. Marcado, se lo reemplaza.",
        ),
    ),
    outputs=(
        Output("rutas", ParamType.JSON, doc="Las rutas nuevas, en el mismo orden."),
        Output("cantidad", ParamType.INT),
        Output(
            "variables", ParamType.JSON,
            doc="Por archivo, los grupos del patrón y dónde quedó: [{arcada, step, …, ruta, nombre, nuevo, ruta_nueva}].",
        ),
        Output("salteados", ParamType.JSON, doc="Las rutas que no coincidieron y se dejaron como estaban."),
        Output("carpeta", ParamType.PATH, doc="La carpeta donde quedó el primero."),
    ),
)


def _como_lista(valor) -> list[str]:
    """`rutas` como lista. Del flujo llega ya como lista (el núcleo convierte el
    param JSON), pero un llamado directo puede pasar una ruta suelta o el
    JSON de la lista como texto."""
    if isinstance(valor, str):
        texto = valor.strip()
        if texto.startswith("["):
            try:
                valor = json.loads(texto)
            except ValueError:
                return [texto]
        else:
            return [texto] if texto else []
    if isinstance(valor, (list, tuple)):
        return [str(v).strip() for v in valor if str(v or "").strip()]
    return []


def _armar_nombre(plantilla: str, coincidencia: re.Match) -> str:
    """El nombre nuevo, o `KeyError` con el grupo que no existe en el patrón."""
    grupos = coincidencia.groupdict()

    def poner(m: re.Match) -> str:
        nombre, relleno = m.group(1), m.group(2)
        if nombre.isdigit():
            indice = int(nombre)
            if indice > (coincidencia.re.groups or 0):
                raise KeyError(nombre)
            valor = coincidencia.group(indice)
        elif nombre in grupos:
            valor = grupos[nombre]
        else:
            raise KeyError(nombre)
        valor = valor or ""
        if relleno and valor.isdigit():
            valor = valor.zfill(int(relleno))
        return valor

    return _GRUPO.sub(poner, plantilla).strip()


def _renombrar_lote(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    rutas = _como_lista(ctx.params.get("rutas"))
    vacio = {"rutas": [], "cantidad": 0, "variables": [], "salteados": [], "carpeta": ""}
    if not rutas:
        return ToolResult.err("'rutas' está vacío: no hay nada que renombrar", **vacio)
    try:
        regex = re.compile(ctx.params["patron"], re.IGNORECASE)
    except re.error as exc:
        return ToolResult.err(f"'patron' no es una expresión regular válida: {exc}", **vacio)
    plantilla = ctx.params["nombre_nuevo"]
    destino = (ctx.params.get("destino") or "").strip()
    copiar = ctx.params.get("modo") == "copiar"
    saltear = bool(ctx.params.get("saltear_sin_coincidencia"))
    pisar = bool(ctx.params.get("pisar"))

    # Primero el plan entero, sin tocar el disco: con 10 archivos, fallar en el
    # sexto deja la carpeta mitad renombrada y el flujo no tiene cómo volver.
    plan, salteados, problemas = [], [], []
    for ruta in rutas:
        nombre = fs.basename(ruta)
        if not fs.exists(ruta) or fs.is_dir(ruta):
            problemas.append(f"no existe el archivo: {ruta}")
            continue
        coincidencia = regex.search(nombre)
        if coincidencia is None:
            if saltear:
                salteados.append(ruta)
            else:
                problemas.append(f"'{nombre}' no coincide con el patrón")
            continue
        try:
            nuevo = _armar_nombre(plantilla, coincidencia)
        except KeyError as exc:
            hay = ", ".join(f"<{g}>" for g in regex.groupindex) or "ninguno con nombre"
            return ToolResult.err(
                f"'nombre_nuevo' usa <{exc.args[0]}>, que el patrón no tiene. Grupos: {hay}", **vacio)
        if not nuevo or any(c in nuevo for c in '/\\:*?"<>|'):
            problemas.append(f"'{nombre}' quedaría con un nombre inválido: {nuevo!r}")
            continue
        final = fs.join(destino or fs.parent(ruta), nuevo)
        # Los grupos primero: uno que se llame 'ruta' o 'nombre' no puede
        # pisar los datos del archivo, que el resto del tool usa.
        plan.append({
            **{k: v or "" for k, v in coincidencia.groupdict().items()},
            "ruta": ruta, "nombre": nombre, "nuevo": nuevo, "final": final,
        })

    # Sin distinguir mayúsculas: en Windows A.stl y a.stl son el mismo archivo.
    vistos: dict[str, str] = {}
    for paso in plan:
        clave = paso["final"].replace("\\", "/").lower()
        if clave in vistos:
            problemas.append(f"'{vistos[clave]}' y '{paso['nombre']}' terminarían los dos como '{paso['nuevo']}'")
        vistos[clave] = paso["nombre"]
        mismo = clave == paso["ruta"].replace("\\", "/").lower()
        if mismo and copiar:
            problemas.append(f"'{paso['nombre']}' se copiaría sobre sí mismo: con 'copiar' hace falta otro nombre u otro 'destino'")
        elif not mismo and fs.exists(paso["final"]):
            if fs.is_dir(paso["final"]):
                problemas.append(f"ya hay una carpeta en {paso['final']}")
            elif not pisar:
                problemas.append(f"ya existe {paso['final']} (marcá 'pisar' para reemplazarlo)")

    if salteados:
        ctx.log(f"{len(salteados)} no coinciden y quedan como están: "
                + ", ".join(fs.basename(r) for r in salteados), "warning")
    if problemas:
        for p in problemas:
            ctx.log(p, "error")
        return ToolResult.err(
            f"no se renombró ninguno: {problemas[0]}"
            + (f" (y {len(problemas) - 1} problema(s) más, ver log)" if len(problemas) > 1 else ""),
            **vacio,
        )
    if not plan:
        return ToolResult.err("ningún archivo coincide con el patrón", **{**vacio, "salteados": salteados})

    if destino:
        fs.make_dirs(destino)
    hechos = []
    for paso in plan:
        origen, final = paso["ruta"], paso["final"]
        try:
            if origen.replace("\\", "/").lower() == final.replace("\\", "/").lower() and not copiar:
                # Mismo nombre, o sólo cambian mayúsculas: en Windows mover
                # sobre sí mismo es un no-op o un error, según el caso.
                if origen != final:
                    final = fs.rename(origen, paso["nuevo"])
            else:
                if pisar and fs.exists(final):
                    fs.remove_file(final)
                final = fs.copy_file(origen, final) if copiar else fs.move(origen, final)
        except PortError as exc:
            return ToolResult.err(
                f"falló en '{paso['nombre']}' después de {len(hechos)} de {len(plan)}: {exc}",
                rutas=[h["final"] for h in hechos], cantidad=len(hechos),
                variables=hechos, salteados=salteados, carpeta=fs.parent(hechos[0]["final"]) if hechos else "",
            )
        paso["final"] = final
        ctx.log(f"{paso['nombre']} -> {final}")
        hechos.append(paso)

    variables = [{**{k: v for k, v in p.items() if k != "final"}, "ruta_nueva": p["final"]} for p in hechos]
    return ToolResult.ok(
        f"{len(hechos)} {'copiado(s)' if copiar else 'renombrado(s)'}",
        rutas=[p["final"] for p in hechos], cantidad=len(hechos), variables=variables,
        salteados=salteados, carpeta=fs.parent(hechos[0]["final"]),
    )


# ── buscar ──────────────────────────────────────────────────────────────

# `buscar` y `comparar_conteo` filtran con el mismo criterio, así que el texto
# que lo explica y el código que lo aplica viven una sola vez: dos copias se
# separan en cuanto una de las dos cambie, y lo que un flujo escribe en
# 'patron' tiene que contar igual en las dos.
_DOC_ETIQUETA = "Substring del nombre, sin distinguir mayúsculas."
_DOC_PATRON = (
    "Expresión regular sobre el nombre del archivo (re.search, sin distinguir mayúsculas). "
    r"Ej. ^[A-Z]{2}\d{3}-[LU]\d{2}-[A-Z]\.stl$"
)


def _filtro(etiqueta: str, patron: str):
    """
    El par `(coincide, criterio)` que salen de una etiqueta y un patrón: el
    primero dice si un nombre entra, el segundo lo describe para el log. Con
    los dos hay que cumplir los dos; sin ninguno entra todo.

    Levanta `re.error` si el patrón no compila — cada tool decide qué mensaje
    dar, porque el parámetro no se llama igual en todas.
    """
    etiqueta = etiqueta or ""
    patron = patron or ""
    regex = re.compile(patron, re.IGNORECASE) if patron else None

    def coincide(nombre: str) -> bool:
        if etiqueta and etiqueta.lower() not in nombre.lower():
            return False
        return regex is None or regex.search(nombre) is not None

    criterio = " y ".join(c for c in (
        f"'{etiqueta}'" if etiqueta else "",
        f"/{patron}/" if patron else "",
    ) if c)
    return coincide, criterio or "todos los archivos"


BUSCAR = ToolManifest(
    id="archivos.buscar",
    label="buscar",
    category="ARCHIVOS",
    doc=(
        "Busca, recursivo, los archivos de una carpeta. Por 'etiqueta' (substring "
        "del nombre) o por 'patron' (expresión regular sobre el nombre, re.search); "
        "al menos uno de los dos. Con 'subcarpeta' baja primero a la subcarpeta que "
        "corresponda y busca sólo ahí adentro, en vez de recorrer todo el árbol."
    ),
    params=(
        Param("carpeta", ParamType.PATH, required=True),
        Param(
            "subcarpeta",
            doc="Ej.: {id_externo}. Baja primero a la subcarpeta DIRECTA de 'carpeta' cuyo nombre contenga esto "
            "—sin distinguir mayúsculas— y busca sólo ahí adentro. Con más de una, gana la "
            "modificada más recientemente, y el log dice cuál y cuáles dejó afuera. Sin esto se "
            "recorre el árbol entero de 'carpeta': contra una carpeta de casos terminados eso es "
            "visitar cada archivo de cada caso para llegar a uno.",
        ),
        Param("etiqueta", doc=_DOC_ETIQUETA),
        Param("patron", doc=_DOC_PATRON),
        Param(
            "excluir_carpeta",
            doc="Ej.: {id_externo}. Deja afuera lo que esté adentro de una carpeta cuyo nombre contenga "
            "esto —sin distinguir mayúsculas—, contando sólo las carpetas de abajo de donde se busca "
            "(la de 'subcarpeta' no cuenta). Para cuando lo que se busca y lo que un paso posterior "
            "deja en el mismo caso llevan el mismo nombre: sin esto, una segunda corrida encuentra "
            "la salida de la primera.",
        ),
    ),
    outputs=(
        Output("rutas", ParamType.JSON, doc="Lista de rutas encontradas."),
        Output("cantidad", ParamType.INT),
        Output("primera", ParamType.PATH, doc="La primera ruta encontrada, para usarla directo como {primera}."),
        Output(
            "carpeta", ParamType.PATH,
            doc="La carpeta que contiene la PRIMERA ruta encontrada, para los tools que piden una "
            "carpeta y no archivos sueltos (ej. 'exportar' de ToothFORM). Ojo: la búsqueda es "
            "recursiva, así que si los resultados están repartidos ésta es la de uno solo — "
            "mirá 'carpetas' para saber si hay más de una.",
        ),
        Output(
            "carpetas", ParamType.JSON,
            doc="Las carpetas distintas donde cayeron los resultados, ordenadas. Con una sola entrada, "
            "'carpeta' las representa a todas; con más de una, el resultado está repartido y elegir "
            "una sola deja las otras afuera.",
        ),
    ),
)


_SIN_NADA = {"rutas": [], "cantidad": 0, "primera": "", "carpeta": "", "carpetas": []}


def _hijos(ctx: ToolContext, fs, carpeta: str) -> list:
    """
    Las entradas que cuelgan directo de `carpeta`, sin bajar más.

    `walk(max_depth=1)` es lo que hace falta y lo que menos cuesta: emite las
    carpetas SIN statearlas (core#33, núcleo v0.3.1-beta.12). Un núcleo anterior
    no conoce el argumento, y ahí `list_dir` da lo mismo pero pagando un stat por
    entrada: sobre el share de casos terminados —49.684 en el primer nivel— son
    ~80s contra ~1,4s, y de esos 80 casi todos son stats que se tiran.

    Se degrada, no se rompe: `archivos` tiene que poder instalarse en un Bot que
    todavía no actualizó el núcleo. El TypeError se atrapa en la llamada y no
    alrededor del consumo, para no confundirlo con uno de adentro del walk.
    """
    try:
        entradas = fs.walk(carpeta, max_depth=1)
    except TypeError:
        ctx.log(
            "este núcleo no tiene walk(max_depth=...) (core#33, v0.3.1-beta.12): "
            "se listan los hijos con stat, que sobre una carpeta grande es mucho más lento",
            "warning",
        )
        return list(fs.list_dir(carpeta))
    return list(entradas)


def _bajar_a(ctx: ToolContext, fs, carpeta: str, etiqueta: str):
    """
    La subcarpeta DIRECTA de `carpeta` que contiene `etiqueta` en el nombre; con
    más de una, la modificada más recientemente. O un ToolResult de error.

    Directa y no recursiva a propósito: la suposición real es "la carpeta del
    caso cuelga de acá", que es fácil de verificar, y no "los candidatos salen
    seguidos si ordeno", que depende del nivel en que estén y de que la etiqueta
    sea prefijo.

    Hacen falta los nombres de acá abajo y el mtime de las dos o tres que
    matcheen; ver `_hijos` para lo que cuesta cada forma de conseguirlos.
    """
    baja = etiqueta.lower()
    candidatas = [e for e in _hijos(ctx, fs, carpeta) if e.is_dir and baja in e.name.lower()]
    if not candidatas:
        return ToolResult.err(
            f"ninguna subcarpeta de {carpeta} tiene '{etiqueta}' en el nombre", **_SIN_NADA)

    # `walk` no statea las carpetas —de eso se trata—, así que el mtime se pide
    # aparte, y sólo el de las candidatas. Una que no se pueda statear va al
    # fondo en vez de tumbar la elección: que sobre una es motivo de warning,
    # no de que el flujo se caiga.
    fechas = {}
    for e in candidatas:
        try:
            fechas[e.path] = fs.stat(e.path).modified_at
        except PortError:
            fechas[e.path] = 0.0
    # El nombre desempata, para que dos con la misma fecha no dependan del
    # orden en que las devolvió el filesystem.
    candidatas.sort(key=lambda e: (-fechas[e.path], e.name))
    elegida = candidatas[0]
    if len(candidatas) > 1:
        # Warning y no error: con un id repetido —un caso rehecho, un apellido
        # mal tipeado— hay una respuesta razonable, pero que se elija sola sin
        # que quede escrito sería decidir por el operador en silencio.
        otras = ", ".join(e.name for e in candidatas[1:])
        ctx.log(
            f"{len(candidatas)} subcarpetas con '{etiqueta}': va '{elegida.name}' "
            f"por ser la más nueva; deja afuera {otras}",
            "warning",
        )
    return elegida.path


def _buscar(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    carpeta = ctx.params["carpeta"]
    if not fs.exists(carpeta) or not fs.is_dir(carpeta):
        return ToolResult.err(f"no existe la carpeta: {carpeta}")

    if not ctx.params.get("etiqueta") and not ctx.params.get("patron"):
        return ToolResult.err("hace falta 'etiqueta' o 'patron'")

    if subcarpeta := (ctx.params.get("subcarpeta") or "").strip():
        elegida = _bajar_a(ctx, fs, carpeta, subcarpeta)
        if isinstance(elegida, ToolResult):
            return elegida
        carpeta = elegida

    try:
        coincide, criterio = _filtro(ctx.params.get("etiqueta"), ctx.params.get("patron"))
    except re.error as exc:
        return ToolResult.err(f"'patron' no es una expresión regular válida: {exc}")

    rutas = [e.path for e in fs.walk(carpeta) if not e.is_dir and coincide(e.name)]
    ctx.log(f"{len(rutas)} archivo(s) con {criterio} en {carpeta}")

    # Existe por ToothFORM: el STL que exporta se llama igual que el original
    # y queda en <caso>\<id>, así que una segunda corrida sobre el mismo caso
    # encontraba su propia salida y se la daba de entrada —y ToothFORM se cae
    # con un STL ya exportado (0xC0000005, sin log)—. Medido el 13/10/2026 en
    # BOT.1: BZ219 daba 76 archivos y 'carpeta' = BZ219\BZ219; con esto, los
    # 44 originales en su carpeta.
    excluidos = 0
    if excluir := (ctx.params.get("excluir_carpeta") or "").strip().lower():
        base = carpeta.replace("\\", "/").rstrip("/").lower()

        def afuera(ruta: str) -> bool:
            # Sólo las carpetas de abajo de donde se busca: la del caso suele
            # llevar el mismo texto y excluirla sería excluir todo.
            relativa = fs.parent(ruta).replace("\\", "/")
            if relativa.lower().startswith(base):
                relativa = relativa[len(base):]
            return any(excluir in parte.lower() for parte in relativa.split("/") if parte)

        quedan = [r for r in rutas if not afuera(r)]
        excluidos = len(rutas) - len(quedan)
        if excluidos:
            ctx.log(f"{excluidos} quedaron afuera por estar en una carpeta con '{excluir}'")
        rutas = quedan

    if not rutas:
        motivo = f" ({excluidos} quedaron afuera por 'excluir_carpeta')" if excluidos else ""
        return ToolResult.err(f"ningún archivo con {criterio} en {carpeta}{motivo}", **_SIN_NADA)

    # La carpeta de cada resultado, no la que se buscó: `walk` es recursivo, así
    # que los archivos pueden estar en una subcarpeta —o en varias—. Un tool que
    # pide una carpeta (el 'exportar' de ToothFORM) necesita la que los
    # contiene, y encadenar la que se buscó le daría la de más arriba.
    carpetas = sorted({fs.parent(r) for r in rutas})
    if len(carpetas) > 1:
        ctx.log(
            f"están repartidos en {len(carpetas)} carpetas; 'carpeta' es la de la primera "
            f"({fs.parent(rutas[0])}) y deja las otras afuera",
            "warning",
        )
    return ToolResult.ok(
        rutas=rutas, cantidad=len(rutas), primera=rutas[0],
        carpeta=fs.parent(rutas[0]), carpetas=carpetas,
    )


# ── comparar_conteo ───────────────────────────────────────────────────────

def _doc_patron(lado: str) -> str:
    # `_DOC_PATRON` trae el ejemplo con llaves ({2}, {3}), así que se concatena:
    # un .format() sobre ese texto las leería como campos y reventaría.
    return (
        _DOC_PATRON + f" Filtra sólo 'carpeta_{lado}'; vacío la cuenta entera. El patrón va "
        "por lado porque las dos carpetas pueden guardar el mismo entregable rodeado de "
        "cosas distintas —la salida del 4in1 trae además los intermedios (-att, -gum, "
        "-tooth, -MatA) que la carpeta exportada no tiene—, así que el filtro se pone donde "
        "hace falta y no hay que acomodar las carpetas para que la filtrable caiga en un "
        "lado fijo. Ojo: filtrando uno solo, cualquier archivo suelto del otro mueve el "
        "conteo; el mismo patrón de los dos lados es el control más firme."
    )


COMPARAR_CONTEO = ToolManifest(
    id="archivos.comparar_conteo",
    label="comparar conteo",
    category="ARCHIVOS",
    doc=(
        "Cuenta, recursivo, los archivos de dos carpetas y compara: 'ok' si coinciden, "
        "'err' si no. Cada carpeta lleva su propio 'patron'; la que lo deja vacío se "
        "cuenta entera. Sin ningún filtro es carpeta contra carpeta."
    ),
    params=(
        Param("carpeta_a", ParamType.PATH, required=True),
        Param("patron_a", doc=_doc_patron("a")),
        Param("carpeta_b", ParamType.PATH, required=True),
        Param("patron_b", doc=_doc_patron("b")),
        Param(
            "etiqueta",
            doc=_DOC_ETIQUETA + " Va sobre las dos carpetas, no por lado: identifica el "
            "caso, que es el mismo de los dos lados. Lo que cambia de un lado al otro es "
            "la forma del archivo, y de eso se ocupan 'patron_a' y 'patron_b'.",
        ),
    ),
    outputs=(Output("cantidad_a", ParamType.INT), Output("cantidad_b", ParamType.INT)),
)


def _comparar_conteo(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    carpeta_a, carpeta_b = ctx.params["carpeta_a"], ctx.params["carpeta_b"]
    # Una carpeta que no está contaba 0, así que dos rutas mal escritas daban
    # 0 vs 0 → 'ok': el control más tranquilizador era el que no miraba nada. Con
    # un patrón es peor, porque ahí el 0 también es el resultado legítimo de uno
    # que no matchea y deja de distinguirse de la ruta equivocada. Que falle.
    for carpeta in (carpeta_a, carpeta_b):
        if not fs.exists(carpeta) or not fs.is_dir(carpeta):
            return ToolResult.err(f"no existe la carpeta: {carpeta}")

    etiqueta = ctx.params.get("etiqueta")
    filtros = {}
    for lado in ("a", "b"):
        try:
            filtros[lado] = _filtro(etiqueta, ctx.params.get(f"patron_{lado}"))
        except re.error as exc:
            return ToolResult.err(f"'patron_{lado}' no es una expresión regular válida: {exc}")
    (coincide_a, criterio_a), (coincide_b, criterio_b) = filtros["a"], filtros["b"]

    def contar(carpeta: str, coincide) -> int:
        return sum(1 for e in fs.walk(carpeta) if not e.is_dir and coincide(e.name))

    cantidad_a, cantidad_b = contar(carpeta_a, coincide_a), contar(carpeta_b, coincide_b)
    # Los dos criterios en el log, no sólo los números: con un lado filtrado y el
    # otro no, un 3 vs 3 sale de dos preguntas distintas y conviene que se vea.
    ctx.log(
        f"{carpeta_a} ({criterio_a}): {cantidad_a} · "
        f"{carpeta_b} ({criterio_b}): {cantidad_b}"
    )
    if cantidad_a != cantidad_b:
        return ToolResult.err(
            f"no coinciden: {cantidad_a} vs {cantidad_b}", cantidad_a=cantidad_a, cantidad_b=cantidad_b,
        )
    return ToolResult.ok(cantidad_a=cantidad_a, cantidad_b=cantidad_b)


_TOOLS = (
    (MOVER, _mover),
    (COPIAR, _copiar),
    (ELIMINAR, _eliminar),
    (RENOMBRAR, _renombrar),
    (RENOMBRAR_LOTE, _renombrar_lote),
    (BUSCAR, _buscar),
    (COMPARAR_CONTEO, _comparar_conteo),
)


def build_plugin() -> Plugin:
    return Plugin(manifest=MANIFEST, tools=[FunctionTool(manifest=m, fn=f) for m, f in _TOOLS])


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
