"""
Plugin `keep-form` — la salida de ToothFORM, sin ToothFORM.

Por cada modelo arma lo que exporta ToothFORM: la pieza hueca para imprimir
con las referencias del robot (agujeros, llave, placa), el QR en relieve y el
texto calado, más `-gum.stl` y `-tooth.ply` para la curva de corte, en
`<salida>\\<ID>`, y un `<fecha>.<hora>.log` con el formato de ToothFORM.

La geometría es la de Enkukador (OperacionesMejoras/Enkukador-keepsmiling,
`--producto toothform`), copiada en `keep_form/enkukador/` porque en la
máquina que lo usa no se puede instalar ese programa; sí las librerías, que
este plugin declara en su requirements.txt. Corre adentro del Bot, un modelo
detrás de otro.

Las salidas de `exportar` llevan los mismos nombres que las de
`toothform.exportar`: en un flujo se cambia ese nodo por éste y el resto
sigue igual. Es un plugin aparte y no un tool de `toothform` por las
librerías: `toothform` no pide ninguna, y sumárselas haría pasar por pip a
las instalaciones que sólo corren Toothform.exe.
"""

from __future__ import annotations

import concurrent.futures
import io
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
    ToolContext,
    ToolManifest,
    ToolResult,
)
from backend.core.ports import PortError

MANIFEST = PluginManifest(
    name="keep-form",
    label="Keep Form",
    version="0.1.8",
    doc="Exportar como ToothFORM sin ToothFORM: pieza hueca con referencias del robot, QR y texto, -gum.stl, -tooth.ply y el log, armados adentro del Bot.",
    ports=(port_names.FS,),
    # Las mismas de requirements.txt (que es lo que se instala, con hash), para
    # que la pantalla de plugins diga qué falta antes de correr nada.
    requires=(
        "numpy==2.5.3", "scipy==1.18.1", "rtree==1.4.1", "trimesh==5.1.0",
        "shapely==2.2.0", "networkx==3.7", "manifold3d==3.5.4", "segno==1.6.6",
        "scikit-image==0.26.0", "imageio==2.38.1", "tifffile==2026.9.20", "lazy-loader==0.6",
        "packaging==26.3", "pillow==12.3.0", "fonttools==4.67.0",
    ),
)


# Los de ParameterSettings que usa la reconstrucción (enkukador.toothform.Parametros).
_PARAMETROS_TOOTHFORM = (
    "ShellThickness", "BottomPlaneThickness", "LimitR", "LimitH", "QrSize", "Radius1", "Radius2",
    "LimitX",
)
_EXT_MODELOS = (".stl", ".obj", ".ply", ".off")
# Lo que ToothFORM deja en <modelo>-MatA.txt: dos identidades 4x4 por filas,
# CRLF. Revisados los 1492 de "Casos Terminados 2026" (14/10/2026): todos
# iguales byte a byte, sin importar caso, maxilar ni A/B, y no describen la
# transformación que aplica. Se escribe igual para que lo que venga después
# en el flujo encuentre lo mismo que con ToothFORM.
_MATA = ("1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1\r\n" * 2).encode("ascii")

EXPORTAR = ToolManifest(
    id="keep_form.exportar",
    label="exportar como ToothFORM",
    category="KEEP FORM",
    doc=(
        "Hace lo que hace ToothFORM, sin ToothFORM: por cada modelo, la pieza hueca para "
        "imprimir con las referencias del robot (agujeros, llave, placa), el QR en relieve y "
        "el texto calado, más -gum.stl, -tooth.ply y -MatA.txt, en <salida>\\<ID>, y un log "
        "<fecha>.<hora>.log con el formato de ToothFORM. Las salidas tienen los mismos nombres "
        "que las de 'toothform.exportar', así que en un flujo se cambia ese nodo por éste. "
        "Corre adentro del Bot, un modelo detrás de otro (unos segundos cada uno). Los "
        "parámetros de ParameterSettings van como params extra (ej. ShellThickness=2.0). "
        "Es la parte de ToothFORM de Enkukador (OperacionesMejoras/Enkukador-keepsmiling); "
        "algunas medidas están marcadas ahí como provisorias."
    ),
    params=(
        Param("carpeta", ParamType.PATH, default="", doc="Carpeta con los modelos: TODOS los .stl/.obj/.ply/.off de adentro. Alternativa a 'archivos'."),
        Param("archivos", ParamType.JSON, default=[], doc="Alternativa a 'carpeta': sólo estos archivos (ej. {rutas} de 'buscar')."),
        Param("salida", ParamType.PATH, required=True, doc="Carpeta de salida: ahí caen el log y <ID>\\ con el export."),
        Param(
            "paralelo", ParamType.INT, default=1,
            doc="Cuántos modelos a la vez (hasta 8). Con 2 es ~1,35 veces más rápido; cada uno "
            "suma memoria (~3 GB uno solo en un caso real). 1 = uno detrás de otro.",
        ),
    ),
    extra_params=True,
    outputs=(
        Output("log_file", ParamType.STR, doc="Nombre del log que dejó."),
        Output("checkLogResult", ParamType.JSON, doc="{log_file, message}, como 'toothform.exportar'."),
        Output("log_texto", ParamType.STR),
        Output("hubo_log", ParamType.STR, doc="'si' si llegó a escribir el log."),
        Output("exitosos", ParamType.INT),
        Output("fallidos", ParamType.INT),
        Output("exportados", ParamType.JSON, doc="Los modelos que exportaron, por nombre."),
        Output("modelos_fallidos", ParamType.JSON, doc="[{modelo, motivo}] de los que no exportaron."),
        Output("carpeta_export", ParamType.PATH, doc="<salida>\\<ID> del primer modelo exportado."),
        Output("modelos", ParamType.JSON, doc="Los modelos que se pidieron, por nombre."),
        Output("esperados", ParamType.INT),
        Output("escritos", ParamType.INT, doc="Cuántos dejaron su <modelo>.stl en la salida."),
        Output("faltantes", ParamType.JSON, doc="Los modelos pedidos que no tienen su <modelo>.stl."),
    ),
)


def _rtree_listo(ctx: ToolContext) -> ToolResult | None:
    """
    rtree (trimesh lo usa para ordenar los contornos de cada corte) carga una
    DLL que necesita MSVCP140.DLL, del Visual C++ Redistributable x64. Sin él
    el import se cae con OSError, no con ImportError, y cada modelo fallaba
    por separado con "Failed to hollow": BOT.1, 15/10/2026, 44 de 44. Se
    prueba una vez acá y se corta con el motivo.

    Y si alguna vez falló, trimesh se quedó con ese error: `trimesh.base` y
    `trimesh.path.polygons` importan `rtree.index.Index` una sola vez y, si no
    carga, guardan un `ExceptionWrapper` que sigue fallando aunque después se
    arregle la DLL, hasta reiniciar el Bot. Si rtree ya carga, se les devuelve
    la clase buena.
    """
    try:
        from rtree.index import Index
    except (ImportError, OSError) as exc:
        return ToolResult.err(
            f"la librería rtree no carga ({type(exc).__name__}: {exc}). En Windows casi siempre es "
            "que falta MSVCP140.DLL: instalar el Microsoft Visual C++ Redistributable x64 en esta "
            "máquina (o un instalador del Bot que la traiga) y reiniciar el Bot",
            error_kind="librerias",
        )
    import trimesh.base
    import trimesh.path.polygons
    from trimesh.exceptions import ExceptionWrapper

    for modulo in (trimesh.base, trimesh.path.polygons):
        if isinstance(getattr(modulo, "Index", None), ExceptionWrapper):
            modulo.Index = Index
            ctx.log(f"{modulo.__name__} tenía guardado un error viejo de rtree: se lo reemplazó", "warning")
    return None


def _una_pieza(ctx: ToolContext, stem: str, pieza):
    """
    La pieza más grande, sin los pedacitos sueltos que a veces dejan las
    booleanas (de 4 a 58 triángulos, en 9 de los 44 modelos de BZ219): una
    pieza para imprimir es un solo cuerpo, y ToothFORM da uno en 43 de 44.
    """
    partes = pieza.split(only_watertight=False)
    if len(partes) <= 1:
        return pieza
    partes = sorted(partes, key=lambda q: len(q.faces), reverse=True)
    sueltas = sum(len(q.faces) for q in partes[1:])
    ctx.log(f"{stem}: se sacaron {len(partes) - 1} parte(s) suelta(s), {sueltas} triángulos", "warning")
    return partes[0]


def _exportar(ctx: ToolContext) -> ToolResult:
    try:
        return _exportar_en_disco(ctx)
    except PortError as exc:
        return ToolResult.err(f"ruta o permiso: {exc}", error_kind="ruta")


def _exportar_en_disco(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    carpeta, salida = ctx.params["carpeta"], ctx.params["salida"]
    archivos = ctx.params.get("archivos") or []

    desconocidos = sorted(set(ctx.extras) - set(_PARAMETROS_TOOTHFORM))
    if desconocidos:
        return ToolResult.err(
            f"parámetros que no se usan: {', '.join(desconocidos)}. Válidos: {', '.join(_PARAMETROS_TOOTHFORM)}"
        )
    if bool(carpeta) == bool(archivos):
        return ToolResult.err(
            "hace falta 'carpeta' (todos los modelos de adentro) o 'archivos' (sólo ésos), no las dos "
            "ni ninguna" if carpeta else "hace falta 'carpeta' o 'archivos'"
        )

    numeros = {}
    for clave, valor in ctx.extras.items():
        try:
            numeros[clave] = float(valor)
        except (TypeError, ValueError):
            return ToolResult.err(f"'{clave}' tiene que ser un número, no '{valor}'")

    if archivos:
        faltan = [a for a in archivos if not fs.exists(a) or fs.is_dir(a)]
        if faltan:
            return ToolResult.err(f"no existen estos archivos: {', '.join(faltan)}")
        rutas = list(archivos)
    else:
        if not fs.exists(carpeta) or not fs.is_dir(carpeta):
            return ToolResult.err(f"no existe la carpeta con los modelos: {carpeta}")
        rutas = [
            e.path for e in fs.list_dir(carpeta)
            if not e.is_dir and e.name.lower().endswith(_EXT_MODELOS)
        ]
        if not rutas:
            return ToolResult.err(f"no hay ningún modelo (.stl, .obj, .ply, .off) en {carpeta}")
    # Una carpeta con -gum.stl es una salida, no la original: el STL exportado
    # se llama igual que el de origen y una búsqueda recursiva la encuentra.
    gum = [fs.basename(r) for r in rutas if fs.basename(r).lower().endswith("-gum.stl")]
    if gum:
        return ToolResult.err(
            f"{carpeta or 'la entrada'} ya es una salida de ToothFORM (tiene {len(gum)} '-gum.stl', ej. "
            f"{gum[0]}): hay que darle los STL originales",
            error_kind="entrada_exportada",
        )

    # Perezoso: sin estas librerías el plugin tiene que cargar igual (y verse
    # en la pantalla de plugins) y sólo el tool falla, diciendo qué falta.
    try:
        import trimesh

        from .enkukador import toothform as tf
    except ImportError as exc:
        return ToolResult.err(
            f"falta la librería '{exc.name}' para exportar como ToothFORM: se instala con el "
            "requirements.txt de keep-form (reinstalar el plugin)",
            error_kind="librerias",
        )
    if (sin_rtree := _rtree_listo(ctx)) is not None:
        return sin_rtree
    p = tf.Parametros(**numeros)

    fs.make_dirs(salida)
    paralelo = max(1, min(int(ctx.params.get("paralelo") or 1), 8))
    hechos = [0]
    candado = threading.Lock()

    def uno(ruta: str) -> tuple[str, str, str, str]:
        """(stem, caso, estado, motivo) de un modelo; deja sus archivos en la salida."""
        nombre = fs.basename(ruta)
        stem, _, ext = nombre.rpartition(".")
        caso, _, modelo = stem.partition("-")
        if ctx.cancelled:
            return stem, caso, "Cancelled", "se canceló el run antes de procesarlo"
        motivo, holgura = "", None
        try:
            datos_stl = fs.read_bytes(ruta)
            try:
                malla = trimesh.load(io.BytesIO(datos_stl), file_type=ext.lower(), force="mesh")
            except Exception as exc:  # noqa: BLE001
                raise tf.FallaToothform(tf.FALLO_PREPROCESO, f"no se pudo leer: {type(exc).__name__}: {exc}") from exc
            if not isinstance(malla, trimesh.Trimesh) or len(malla.faces) == 0:
                raise tf.FallaToothform(tf.FALLO_PREPROCESO, "no es una malla o está vacía")
            resultado = tf.generar(malla, caso, modelo or stem, p)
            estado, holgura = resultado.estado, resultado.holgura_qr
            if estado in tf.EXPORTADOS:
                resultado.pieza = _una_pieza(ctx, stem, resultado.pieza)
            destino = fs.join(salida, caso)
            fs.make_dirs(destino)
            for archivo, datos in tf.exportar(resultado, stem).items():
                fs.write_bytes(fs.join(destino, archivo), datos)
            if estado in tf.EXPORTADOS:
                fs.write_bytes(fs.join(destino, f"{stem}-MatA.txt"), _MATA)
            for aviso in resultado.avisos:
                ctx.log(f"{stem}: {aviso}", "warning")
        except PortError:
            raise
        except tf.FallaToothform as exc:  # con el nombre que usa ToothFORM en su log
            estado, motivo = exc.estado, exc.detalle
            ctx.log(f"{stem}: {estado}: {motivo}", "error")
        except Exception as exc:  # un modelo roto no frena el caso, como en ToothFORM
            estado, motivo = tf.FALLO_VACIADO, f"{type(exc).__name__}: {exc}"
            ctx.log(f"{stem}: {motivo}", "error")
        with candado:
            hechos[0] += 1
            # La holgura del QR va siempre: con ella se calibra LimitX contra ToothFORM.
            qr = f" (pasillo QR {holgura:.2f} mm)" if holgura is not None and holgura != float("inf") else ""
            ctx.log(f"{hechos[0]}/{len(rutas)} {stem}: {estado}{qr}")
        return stem, caso, estado, motivo

    if paralelo > 1:
        # Hilos y no procesos: un plugin no lanza procesos por fuera del port
        # 'process'. numpy, scipy, manifold3d y scikit-image sueltan el GIL en
        # lo pesado: con 2 a la vez, 4 modelos tardaron 11,5 s contra 15,5 s
        # uno detrás de otro, y con 4, 10,7 s. La memoria crece con cada uno.
        ctx.log(f"{len(rutas)} modelos, {paralelo} a la vez")
        with concurrent.futures.ThreadPoolExecutor(paralelo) as ejecutor:
            hechos_por_modelo = list(ejecutor.map(uno, rutas))
    else:
        hechos_por_modelo = [uno(r) for r in rutas]
    if ctx.cancelled:
        return ToolResult.err(f"cancelado después de {hechos[0]} de {len(rutas)} modelos")

    exportados_ok = tf.EXPORTADOS  # "changed to solid" también sale, como en ToothFORM
    modelos, lineas, exportados, fallidos, carpeta_export = [], [], [], [], ""
    for stem, caso, estado, motivo in hechos_por_modelo:  # en el orden de la entrada
        modelos.append(stem)
        lineas.append(f"{stem}    {estado}")
        if estado in exportados_ok:
            exportados.append(stem)
            carpeta_export = carpeta_export or fs.join(salida, caso)
        else:
            fallidos.append({"modelo": stem, "motivo": f"{estado} ({motivo})" if motivo else estado})

    total = (
        f"Total {len(rutas)} models, of which {len(exportados)} succeeded and {len(fallidos)} failed"
    )
    texto = "\n".join([*lineas, "", total]) + "\n"
    # El nombre que pone ToothFORM: <fecha>.<hh>.<mm>.<ss>.log
    log_file = time.strftime("%Y%m%d.%H.%M.%S.log")
    fs.write_text(fs.join(salida, log_file), texto)
    ctx.log(f"{log_file}: {total}")

    salidas = dict(
        log_file=log_file, checkLogResult={"log_file": log_file, "message": lineas[0] if lineas else ""},
        log_texto=texto, hubo_log="si", exitosos=len(exportados), fallidos=len(fallidos),
        exportados=exportados, modelos_fallidos=fallidos, carpeta_export=carpeta_export,
        modelos=modelos, esperados=len(modelos), escritos=len(exportados),
        faltantes=[m for m in modelos if m not in set(exportados)],
    )
    if fallidos:
        return ToolResult.err(f"{len(fallidos)} de {len(rutas)} fallaron", error_kind="modelo_fallido", **salidas)
    return ToolResult.ok(**salidas)


def build_plugin() -> Plugin:
    return Plugin(manifest=MANIFEST, tools=[FunctionTool(manifest=EXPORTAR, fn=_exportar)])


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
