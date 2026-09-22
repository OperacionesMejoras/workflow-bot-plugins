"""
Plugin `archivos` — mover, copiar, eliminar y renombrar contra el port `fs`.

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

MANIFEST = PluginManifest(
    name="archivos",
    label="Archivos",
    version="0.1.0",
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
    ),
    outputs=(Output("ruta", ParamType.PATH),),
)


def _mover(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    origen = ctx.params["origen"]
    if not fs.exists(origen):
        return ToolResult.err(f"no existe: {origen}")
    final = fs.move(origen, ctx.params["destino"])
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


# ── buscar ──────────────────────────────────────────────────────────────

BUSCAR = ToolManifest(
    id="archivos.buscar",
    label="buscar",
    category="ARCHIVOS",
    doc=(
        "Busca, recursivo, los archivos de una carpeta. Por 'etiqueta' (substring "
        "del nombre) o por 'patron' (expresión regular sobre el nombre, re.search); "
        "al menos uno de los dos."
    ),
    params=(
        Param("carpeta", ParamType.PATH, required=True),
        Param("etiqueta", doc="Substring del nombre, sin distinguir mayúsculas."),
        Param(
            "patron",
            doc="Expresión regular sobre el nombre del archivo (re.search, sin distinguir mayúsculas). "
            r"Ej. ^[A-Z]{2}\d{3}-[LU]\d{2}-[A-Z]\.stl$",
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


def _buscar(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    carpeta = ctx.params["carpeta"]
    if not fs.exists(carpeta) or not fs.is_dir(carpeta):
        return ToolResult.err(f"no existe la carpeta: {carpeta}")

    etiqueta = (ctx.params.get("etiqueta") or "").lower()
    patron = ctx.params.get("patron") or ""
    if not etiqueta and not patron:
        return ToolResult.err("hace falta 'etiqueta' o 'patron'")
    try:
        regex = re.compile(patron, re.IGNORECASE) if patron else None
    except re.error as exc:
        return ToolResult.err(f"'patron' no es una expresión regular válida: {exc}")

    def coincide(nombre: str) -> bool:
        if etiqueta and etiqueta not in nombre.lower():
            return False
        return regex is None or regex.search(nombre) is not None

    rutas = [e.path for e in fs.walk(carpeta) if not e.is_dir and coincide(e.name)]
    criterio = " y ".join(c for c in (f"'{ctx.params.get('etiqueta')}'" if etiqueta else "", f"/{patron}/" if patron else "") if c)
    ctx.log(f"{len(rutas)} archivo(s) con {criterio} en {carpeta}")
    if not rutas:
        return ToolResult.err(
            f"ningún archivo con {criterio} en {carpeta}",
            rutas=[], cantidad=0, primera="", carpeta="", carpetas=[],
        )

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

COMPARAR_CONTEO = ToolManifest(
    id="archivos.comparar_conteo",
    label="comparar conteo",
    category="ARCHIVOS",
    doc=(
        "Cuenta los archivos de dos carpetas (opcionalmente filtrando por "
        "etiqueta en el nombre) y compara: 'ok' si coinciden, 'err' si no."
    ),
    params=(
        Param("carpeta_a", ParamType.PATH, required=True),
        Param("carpeta_b", ParamType.PATH, required=True),
        Param("etiqueta", doc="Si se da, sólo cuenta archivos cuyo nombre la contenga."),
    ),
    outputs=(Output("cantidad_a", ParamType.INT), Output("cantidad_b", ParamType.INT)),
)


def _contar(fs, carpeta: str, etiqueta: str) -> int:
    if not fs.exists(carpeta) or not fs.is_dir(carpeta):
        return 0
    entradas = (e for e in fs.walk(carpeta) if not e.is_dir)
    if etiqueta:
        entradas = (e for e in entradas if etiqueta.lower() in e.name.lower())
    return sum(1 for _ in entradas)


def _comparar_conteo(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    etiqueta = ctx.params.get("etiqueta") or ""
    carpeta_a, carpeta_b = ctx.params["carpeta_a"], ctx.params["carpeta_b"]
    cantidad_a = _contar(fs, carpeta_a, etiqueta)
    cantidad_b = _contar(fs, carpeta_b, etiqueta)
    ctx.log(f"{carpeta_a}: {cantidad_a} · {carpeta_b}: {cantidad_b}")
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
    (BUSCAR, _buscar),
    (COMPARAR_CONTEO, _comparar_conteo),
)


def build_plugin() -> Plugin:
    return Plugin(manifest=MANIFEST, tools=[FunctionTool(manifest=m, fn=f) for m, f in _TOOLS])


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
