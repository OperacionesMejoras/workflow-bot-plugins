"""
Plugin `convertidor` — reescribir nombres de archivo entre convenciones,
portado de `Convertidor de archivos` (mapeo STL/PTS de ToothFORM).

El problema real: hay más de una convención de nombre en uso a la vez
(toothform actual, legacy, y lo que venga) y un flujo necesita pasar de una a
otra sin que el plugin sepa de antemano cuáles son. Por eso, a diferencia de
`toothform.check_log` (que interpreta un formato fijo del fabricante), acá los
regex y el template **no** van hardcodeados: los define quien arma el flujo,
como parámetro JSON, y el plugin sólo sabe probar patrones con named groups y
aplicar un template sobre lo que salga.

`parsear_nombre` y `generar_nombre` están separadas a propósito, no
combinadas: una sólo extrae variables, la otra sólo las aplica a un template.
Entre medio el flujo puede usar esas variables para lo que haga falta (decidir
carpeta destino, validar un campo) antes de armar el nombre final.

`reescribir_archivo` es el camino corto para el caso típico —un archivo real
entra, sale copiado con nombre (y opcionalmente carpeta) nuevos— y sí necesita
`fs`. Comparte con las dos anteriores los mismos helpers de parseo/template,
para no tener dos implementaciones de "probar patrones" o "aplicar template"
que puedan divergir.

`parsear_pts` y `corregir_puntos` son el mapeo geométrico de
`utils/geometry.py` y `utils/parser.py` (parse_pts_file) del Convertidor
original: un `.pts` trae, por sección "<maxilla> <movimiento>", los puntos que
después hay que pegar a la superficie de un STL. `corregir_puntos` importa
`trimesh`/`numpy` de forma perezosa (adentro de la función, no al tope del
módulo): son dependencias pesadas que el instalador de plugins **no** resuelve
solas (copia archivos, no corre `pip install`), así que sin ellas el plugin
entero tiene que poder cargar igual —sólo ese tool falla, con un mensaje
claro— en vez de tumbar `parsear_nombre`/`generar_nombre`/`reescribir_archivo`
que no las necesitan.

Esto es un parche a propósito, no la forma final: un plugin no debería
importar una librería externa (`backend/README.md` del core lo dice
explícito), pero hoy no existe el port que lo evite. Ver
https://github.com/EasyIndustry/workflow-bot-core/issues/19 — cuando el core
sume el port `geometry` (con `numpy` como dependencia propia y un adapter
"curado" para trimesh detrás de `available`), este tool pasa a pedir
`ctx.port("geometry")` en vez de importar trimesh directo, y el import
perezoso de acá se saca.

`aplicar_expresiones` porta el lenguaje del "Gestor de Expresiones" original
(`utils/expressions.py`, `ExpressionEvaluator`) — IF/IFS/SET/UPPER/LOWER sobre
variables `{var}` — sin la parte de UI (era un editor Tkinter con un botón
"Probar Reglas"; acá "probar" es correr el tool). Puro cómputo sobre un dict,
sin ports. A diferencia del original, que tragaba errores con un `print` y
seguía (`evaluate_list`), acá una expresión que no se puede evaluar se
reporta en 'errores' y el tool vuelve `err` —pero sigue evaluando las
demás—: la regla del core es que nada falla en silencio, y un `print` en una
GUI que nadie mira en producción es exactamente eso.

El `Resource` `plantillas` es para no repetir el mismo JSON de 'patrones' /
'template' / 'expresiones' en cada nodo de cada flujo: se guarda una vez con
un nombre, y `parsear_nombre`, `generar_nombre`, `reescribir_archivo` y
`aplicar_expresiones` aceptan un param `plantilla` que rellena lo que no vino
explícito en sus propios params (`_de_plantilla`). Cambiar el regex o el
template de una convención pasa a ser editar un registro, no salir a buscar y
reemplazar el JSON en todos los `.mmd` que lo usan — y un param explícito
sigue ganando, así que ningún flujo existente se rompe por esto.
"""

from __future__ import annotations

import io
import os
import re

from backend.core import ports as port_names
from backend.core.contract import (
    Field,
    FunctionTool,
    Output,
    Param,
    ParamType,
    Plugin,
    PluginManifest,
    Resource,
    ToolContext,
    ToolManifest,
    ToolResult,
)

PLANTILLAS = Resource(
    name="plantillas",
    label="Plantillas",
    item_label="Plantilla",
    key_field="nombre",
    doc=(
        "Un conjunto reusable de patrones/templates/expresiones, para no "
        "repetirlos en cada nodo de cada flujo. Un tool que recibe 'plantilla' "
        "usa los campos de acá para lo que no vino explícito en sus propios "
        "params — un param explícito siempre gana."
    ),
    fields=(
        Field("nombre", ParamType.STR, label="Nombre", required=True, doc="Como la referencian los flujos, ej. 'toothform-legacy'."),
        Field("patrones", ParamType.JSON, label="Patrones", doc="Igual que el param 'patrones' de 'parsear nombre' / 'reescribir archivo'."),
        Field("template_nombre", ParamType.STR, label="Template de nombre", doc="Igual que 'template' de 'generar nombre' o 'template_nombre' de 'reescribir archivo'."),
        Field("template_carpeta", ParamType.STR, label="Template de carpeta", doc="Igual que 'template_carpeta' de 'reescribir archivo'."),
        Field("expresiones", ParamType.JSON, label="Expresiones", doc="Igual que el param 'expresiones' de 'aplicar expresiones'."),
    ),
)

MANIFEST = PluginManifest(
    name="convertidor",
    label="Convertidor",
    version="0.2.0",
    doc="Reescribir nombres de archivo entre convenciones: probar patrones regex con named groups y aplicar un template con lo extraído.",
    ports=(port_names.FS,),
    resources=(PLANTILLAS,),
)


# ── helpers compartidos ──────────────────────────────────────────────────

_PARAM_PLANTILLA = Param(
    "plantilla", default="",
    doc="Opcional: nombre de una 'plantilla' guardada. Sus campos rellenan lo que no venga en los params de esta tool.",
)


def _de_plantilla(ctx: ToolContext, valor, campo: str):
    """
    'valor' tal cual si no está vacío; si no, y hay 'plantilla', ese campo de
    ahí. (resultado, error): error es un mensaje listo para ToolResult.err.
    """
    if valor:
        return valor, None
    nombre_plantilla = (ctx.params.get("plantilla") or "").strip()
    if not nombre_plantilla:
        return valor, None
    item = ctx.resource("plantillas", nombre_plantilla, key_field="nombre")
    if item is None:
        conocidas = ", ".join(ctx.resource_keys("plantillas", key_field="nombre")) or "ninguna"
        return None, f"no existe la plantilla '{nombre_plantilla}'. Conocidas: {conocidas}"
    return item.get(campo), None


def _probar_patrones(base: str, patrones: dict) -> tuple[str, dict] | None:
    """Primer (clave, regex) de 'patrones' que matchea 'base' con re.match, o None."""
    for clave, regex in patrones.items():
        try:
            coincidencia = re.match(regex, base, re.IGNORECASE)
        except re.error as exc:
            raise re.error(f"'{clave}': {exc}") from exc
        if coincidencia is not None:
            return clave, coincidencia.groupdict()
    return None


def _aplicar_template(template: str, variables: dict) -> str:
    """Puede levantar KeyError (falta una variable) o IndexError/ValueError (template roto)."""
    return template.format(**variables)

# ── parsear_nombre ──────────────────────────────────────────────────────

PARSEAR_NOMBRE = ToolManifest(
    id="convertidor.parsear_nombre",
    label="parsear nombre",
    category="CONVERTIDOR",
    doc=(
        "Prueba, en orden, cada regex de 'patrones' (re.match, sin distinguir "
        "mayúsculas) contra 'nombre' sin su extensión. El primero que matchea "
        "gana; sus named groups son las 'variables'. err si ninguno matchea. "
        "Sin 'patrones', los toma de 'plantilla' si se dio una."
    ),
    params=(
        Param("nombre", required=True, doc="Nombre del archivo, con o sin ruta (la ruta se ignora, sólo importa el nombre)."),
        Param(
            "patrones", ParamType.JSON, default={},
            doc='Diccionario {clave: regex}, probados en ese orden. Ej. {"standard": "(?P<id>\\\\S+)\\\\s+(?P<maxilla>Inf|Sup)"}',
        ),
        _PARAM_PLANTILLA,
    ),
    outputs=(
        Output("patron", ParamType.STR, doc="Clave de 'patrones' que matcheó."),
        Output("variables", ParamType.JSON, doc="Named groups del regex que matcheó."),
        Output("extension", ParamType.STR, doc="Extensión original, sin el punto (vacía si no tenía)."),
    ),
)


def _parsear_nombre(ctx: ToolContext) -> ToolResult:
    nombre_original = ctx.params["nombre"].strip()
    if not nombre_original:
        return ToolResult.err("'nombre' vacío")
    patrones, error = _de_plantilla(ctx, ctx.params["patrones"], "patrones")
    if error:
        return ToolResult.err(error)
    if not patrones:
        return ToolResult.err("'patrones' vacío: no hay nada para probar (ni param ni 'plantilla')")

    base, ext = os.path.splitext(os.path.basename(nombre_original))
    try:
        resultado = _probar_patrones(base, patrones)
    except re.error as exc:
        return ToolResult.err(f"regex inválida en 'patrones' ({exc})")
    if resultado is None:
        return ToolResult.err(
            f"ningún patrón de {', '.join(patrones)} matchea '{base}'",
            patron="", variables={}, extension=ext.lstrip("."),
        )
    patron, variables = resultado
    ctx.log(f"{nombre_original}: patrón '{patron}' → {variables}")
    return ToolResult.ok(patron=patron, variables=variables, extension=ext.lstrip("."))


# ── generar_nombre ──────────────────────────────────────────────────────

GENERAR_NOMBRE = ToolManifest(
    id="convertidor.generar_nombre",
    label="generar nombre",
    category="CONVERTIDOR",
    doc=(
        "Aplica 'template' (str.format) sobre 'variables' —típicamente el "
        "output de 'parsear nombre'— para armar un nombre nuevo. Si se da "
        "'extension', se agrega al final como '.<extension>'. Sin 'template', "
        "lo toma de 'plantilla' (campo 'template_nombre') si se dio una."
    ),
    params=(
        Param("variables", ParamType.JSON, required=True, doc="Dict con los valores para el template, ej. {external_id, maxilla, movement}."),
        Param("template", default="", doc="Ej. '{external_id}-{maxilla}{movement}-{type}'."),
        Param("extension", default="", doc="Sin el punto, ej. 'stl'. Vacío: el nombre nuevo no lleva extensión."),
        _PARAM_PLANTILLA,
    ),
    outputs=(Output("nombre_nuevo", ParamType.STR),),
)


def _generar_nombre(ctx: ToolContext) -> ToolResult:
    variables = ctx.params["variables"]
    template, error = _de_plantilla(ctx, ctx.params["template"].strip(), "template_nombre")
    if error:
        return ToolResult.err(error)
    if not template:
        return ToolResult.err("'template' vacío (ni param ni 'plantilla')")

    try:
        nombre = _aplicar_template(template, variables)
    except KeyError as exc:
        return ToolResult.err(f"el template pide {exc}, que no está en 'variables' ({', '.join(variables)})")
    except (IndexError, ValueError) as exc:
        return ToolResult.err(f"'template' inválido: {exc}")

    extension = ctx.params["extension"].strip().lstrip(".")
    if extension:
        nombre = f"{nombre}.{extension}"
    ctx.log(f"{template} + {variables} → {nombre}")
    return ToolResult.ok(nombre_nuevo=nombre)


# ── reescribir_archivo ──────────────────────────────────────────────────

REESCRIBIR_ARCHIVO = ToolManifest(
    id="convertidor.reescribir_archivo",
    label="reescribir archivo",
    category="CONVERTIDOR",
    doc=(
        "Copia 'origen' a 'carpeta_salida' con nombre nuevo: prueba 'patrones' "
        "contra el nombre de 'origen' (misma lógica que 'parsear nombre'), y con "
        "las variables que salgan arma el nombre con 'template_nombre' (la "
        "extensión se conserva) y, si se da 'template_carpeta', una subcarpeta "
        "dentro de 'carpeta_salida'. El origen queda intacto. Lo que no venga "
        "explícito ('patrones', 'template_nombre', 'template_carpeta') se toma "
        "de 'plantilla' si se dio una."
    ),
    params=(
        Param("origen", ParamType.PATH, required=True),
        Param("carpeta_salida", ParamType.PATH, required=True),
        Param("patrones", ParamType.JSON, default={}, doc="Igual que en 'parsear nombre': {clave: regex}, probados en orden."),
        Param("template_nombre", default="", doc="Ej. '{id}-{maxilla}{movement}-{type}', sin extensión."),
        Param("template_carpeta", default="", doc="Opcional: subcarpeta dentro de 'carpeta_salida', armada con el mismo template."),
        _PARAM_PLANTILLA,
    ),
    outputs=(
        Output("ruta", ParamType.PATH, doc="Ruta final del archivo copiado."),
        Output("patron", ParamType.STR),
        Output("variables", ParamType.JSON),
        Output("nombre_nuevo", ParamType.STR),
    ),
)


def _reescribir_archivo(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    origen = ctx.params["origen"]
    if not fs.exists(origen) or fs.is_dir(origen):
        return ToolResult.err(f"no existe el archivo: {origen}")
    patrones, error = _de_plantilla(ctx, ctx.params["patrones"], "patrones")
    if error:
        return ToolResult.err(error)
    if not patrones:
        return ToolResult.err("'patrones' vacío: no hay nada para probar (ni param ni 'plantilla')")

    base, ext = os.path.splitext(fs.basename(origen))
    try:
        resultado = _probar_patrones(base, patrones)
    except re.error as exc:
        return ToolResult.err(f"regex inválida en 'patrones' ({exc})")
    if resultado is None:
        return ToolResult.err(f"ningún patrón de {', '.join(patrones)} matchea '{base}'")
    patron, variables = resultado

    template_nombre, error = _de_plantilla(ctx, ctx.params["template_nombre"].strip(), "template_nombre")
    if error:
        return ToolResult.err(error)
    if not template_nombre:
        return ToolResult.err("'template_nombre' vacío (ni param ni 'plantilla')")
    try:
        nombre_nuevo = _aplicar_template(template_nombre, variables) + ext
    except KeyError as exc:
        return ToolResult.err(f"'template_nombre' pide {exc}, ausente en las variables extraídas ({', '.join(variables)})")
    except (IndexError, ValueError) as exc:
        return ToolResult.err(f"'template_nombre' inválido: {exc}")

    carpeta_destino = ctx.params["carpeta_salida"]
    template_carpeta, error = _de_plantilla(ctx, ctx.params["template_carpeta"].strip(), "template_carpeta")
    if error:
        return ToolResult.err(error)
    if template_carpeta:
        try:
            subcarpeta = _aplicar_template(template_carpeta, variables)
        except KeyError as exc:
            return ToolResult.err(f"'template_carpeta' pide {exc}, ausente en las variables extraídas ({', '.join(variables)})")
        except (IndexError, ValueError) as exc:
            return ToolResult.err(f"'template_carpeta' inválido: {exc}")
        carpeta_destino = fs.join(carpeta_destino, subcarpeta)

    fs.make_dirs(carpeta_destino)
    ruta = fs.copy_file(origen, fs.join(carpeta_destino, nombre_nuevo))
    ctx.log(f"{origen} -> {ruta} (patrón '{patron}')")
    return ToolResult.ok(ruta=ruta, patron=patron, variables=variables, nombre_nuevo=nombre_nuevo)


# ── parsear_pts ─────────────────────────────────────────────────────────

_PATRON_SECCION = re.compile(r"^(inf|sup)\s+(\d+)$", re.IGNORECASE)

PARSEAR_PTS = ToolManifest(
    id="convertidor.parsear_pts",
    label="parsear .pts",
    category="CONVERTIDOR",
    doc=(
        "Lee un archivo .pts con secciones '<maxilla> <movimiento>' (ej. 'inf "
        "00') seguidas de líneas de puntos, y devuelve 'secciones': "
        "{'<maxilla>_<movimiento>': [líneas]} en minúsculas."
    ),
    params=(Param("ruta", ParamType.PATH, required=True),),
    outputs=(
        Output("secciones", ParamType.JSON),
        Output("cantidad", ParamType.INT, doc="Cuántas secciones se encontraron."),
    ),
)


def _parsear_pts(ctx: ToolContext) -> ToolResult:
    fs = ctx.port(port_names.FS)
    ruta = ctx.params["ruta"]
    if not fs.exists(ruta) or fs.is_dir(ruta):
        return ToolResult.err(f"no existe el archivo: {ruta}")

    try:
        texto = fs.read_text(ruta, encoding="utf-8")
    except UnicodeDecodeError:
        texto = fs.read_text(ruta, encoding="latin-1")

    secciones: dict[str, list[str]] = {}
    clave_actual: str | None = None
    datos: list[str] = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        coincidencia = _PATRON_SECCION.match(linea)
        if coincidencia is not None:
            if clave_actual is not None:
                secciones[clave_actual] = datos
            clave_actual = f"{coincidencia.group(1).lower()}_{coincidencia.group(2)}"
            datos = []
        elif clave_actual is not None:
            datos.append(linea)
    if clave_actual is not None:
        secciones[clave_actual] = datos

    if not secciones:
        return ToolResult.err(
            f"'{ruta}' no tiene ninguna sección reconocible (esperado '<inf|sup> <número>')",
            secciones={}, cantidad=0,
        )
    ctx.log(f"{ruta}: {len(secciones)} sección(es) — {', '.join(secciones)}")
    return ToolResult.ok(secciones=secciones, cantidad=len(secciones))


# ── corregir_puntos ─────────────────────────────────────────────────────

CORREGIR_PUNTOS = ToolManifest(
    id="convertidor.corregir_puntos",
    label="corregir puntos",
    category="CONVERTIDOR",
    doc=(
        "Carga la malla de 'stl' (su submalla con más caras, salvo "
        "'toda_la_malla'), calcula el punto más cercano en superficie para "
        "cada línea de 'puntos' ('x y z ...') y reemplaza los que superan "
        "'tolerancia' por ese punto más cercano. Requiere 'trimesh' y "
        "'numpy' instalados en el entorno de bot-core (no los instala el "
        "plugin ni el instalador)."
    ),
    params=(
        Param("stl", ParamType.PATH, required=True),
        Param("puntos", ParamType.JSON, required=True, doc="Lista de líneas 'x y z' (típicamente una sección de 'parsear .pts')."),
        Param("tolerancia", ParamType.FLOAT, default=0.5, doc="Distancia máxima (unidades del STL) antes de reemplazar el punto."),
        Param("toda_la_malla", ParamType.BOOL, default=False, doc="True: usa la malla completa. False (default): sólo la submalla con más caras, igual que el Convertidor original."),
    ),
    outputs=(
        Output("puntos", ParamType.JSON, doc="Las mismas líneas, con las que superaban la tolerancia reemplazadas."),
        Output("reemplazados", ParamType.INT),
        Output("distancia_promedio", ParamType.FLOAT),
        Output("distancia_maxima", ParamType.FLOAT),
    ),
)


def _corregir_puntos(ctx: ToolContext) -> ToolResult:
    try:
        import numpy as np
        import trimesh
    except ImportError as exc:
        return ToolResult.err(
            f"falta instalar 'trimesh' y 'numpy' en el entorno de bot-core para usar este tool ({exc})"
        )

    fs = ctx.port(port_names.FS)
    ruta_stl = ctx.params["stl"]
    if not fs.exists(ruta_stl) or fs.is_dir(ruta_stl):
        return ToolResult.err(f"no existe el archivo: {ruta_stl}")
    puntos = ctx.params["puntos"]
    if not puntos:
        return ToolResult.err("'puntos' vacío")

    try:
        malla = trimesh.load(io.BytesIO(fs.read_bytes(ruta_stl)), file_type="stl", force="mesh")
    except Exception as exc:
        return ToolResult.err(f"no se pudo leer la malla de '{ruta_stl}': {exc}")

    if not ctx.params["toda_la_malla"]:
        partes = malla.split(only_watertight=False)
        if len(partes):
            malla = max(partes, key=lambda m: len(m.faces))

    coords = []
    indices = []
    for i, linea in enumerate(puntos):
        campos = linea.split()
        if len(campos) >= 3:
            coords.append([float(campos[0]), float(campos[1]), float(campos[2])])
            indices.append(i)
    if not coords:
        return ToolResult.err("ninguna línea de 'puntos' tiene al menos 3 valores numéricos (x y z)")

    cercanos, distancias, _ = malla.nearest.on_surface(np.array(coords))

    tolerancia = ctx.params["tolerancia"]
    nuevas = list(puntos)
    reemplazados = 0
    for pos, indice in enumerate(indices):
        if float(distancias[pos]) > tolerancia:
            punto = cercanos[pos]
            resto = puntos[indice].split()[3:]
            nueva_linea = f"{punto[0]:.6f} {punto[1]:.6f} {punto[2]:.6f}"
            if resto:
                nueva_linea += " " + " ".join(resto)
            nuevas[indice] = nueva_linea
            reemplazados += 1

    ctx.log(f"{ruta_stl}: {reemplazados}/{len(coords)} punto(s) corregido(s) (tolerancia {tolerancia:g})")
    return ToolResult.ok(
        puntos=nuevas, reemplazados=reemplazados,
        distancia_promedio=float(distancias.mean()), distancia_maxima=float(distancias.max()),
    )


# ── aplicar_expresiones ─────────────────────────────────────────────────

def _dividir_por_comas(contenido: str) -> list[str]:
    """Separa por comas, respetando comillas y paréntesis anidados (para IF/IFS)."""
    partes = []
    actual: list[str] = []
    profundidad = 0
    entre_comillas = False
    i = 0
    while i < len(contenido):
        caracter = contenido[i]
        if caracter == '"' and (i == 0 or contenido[i - 1] != "\\"):
            entre_comillas = not entre_comillas
        elif not entre_comillas:
            if caracter == "(":
                profundidad += 1
            elif caracter == ")":
                profundidad -= 1
            elif caracter == "," and profundidad == 0:
                partes.append("".join(actual).strip())
                actual = []
                i += 1
                continue
        actual.append(caracter)
        i += 1
    if actual:
        partes.append("".join(actual).strip())
    return partes


def _valor_de_token(token: str, variables: dict) -> str:
    """'{var}' / 'LOWER({var})' / 'UPPER({var})' / literal '"val"' → su valor como string."""
    token = token.strip()

    coincidencia = re.match(r"LOWER\s*\(\s*\{(\w+)\}\s*\)", token, re.IGNORECASE)
    if coincidencia is not None:
        return str(variables.get(coincidencia.group(1), "")).lower()

    coincidencia = re.match(r"UPPER\s*\(\s*\{(\w+)\}\s*\)", token, re.IGNORECASE)
    if coincidencia is not None:
        return str(variables.get(coincidencia.group(1), "")).upper()

    coincidencia = re.match(r"\{(\w+)\}", token)
    if coincidencia is not None:
        return str(variables.get(coincidencia.group(1), ""))

    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]

    return token


def _ejecutar_accion(accion: str, variables: dict) -> None:
    """SET({target}, "val") / {target}:"val" / UPPER({target}) / LOWER({target}). ValueError si no reconoce nada."""
    accion = accion.strip()

    coincidencia = re.match(r'SET\s*\(\s*\{(\w+)\}\s*,\s*"([^"]*)"\s*\)', accion, re.IGNORECASE)
    if coincidencia is not None:
        objetivo, valor = coincidencia.groups()
        variables[objetivo] = valor
        return

    coincidencia = re.match(r'\{(\w+)\}\s*:\s*"([^"]*)"', accion)
    if coincidencia is not None:
        objetivo, valor = coincidencia.groups()
        variables[objetivo] = valor
        return

    coincidencia = re.match(r"(UPPER|LOWER)\s*\(\s*\{(\w+)\}\s*\)", accion, re.IGNORECASE)
    if coincidencia is not None:
        funcion, objetivo = coincidencia.groups()
        if objetivo in variables:
            variables[objetivo] = str(variables[objetivo]).upper() if funcion.upper() == "UPPER" else str(variables[objetivo]).lower()
        return

    raise ValueError(f"acción no reconocida: '{accion}'")


def _evaluar_condicion(condicion: str, variables: dict) -> bool:
    """'LOWER({var})="val"'. Sin '=' es False (no error): permite un token vacío como placeholder."""
    condicion = condicion.strip()
    if "=" not in condicion:
        return False
    izquierda, derecha = condicion.split("=", 1)
    return _valor_de_token(izquierda, variables) == _valor_de_token(derecha, variables)


def _evaluar_expresion(expresion: str, variables: dict) -> None:
    """IFS(...) / IF(cond, si, no) / una acción directa. ValueError si está mal formada."""
    expr = expresion.strip()
    if not expr.endswith(")"):
        raise ValueError("expresión sin paréntesis de cierre")

    if expr.upper().startswith("IFS("):
        tokens = _dividir_por_comas(expr[4:-1].strip())
        for i in range(0, len(tokens) - 1, 2):
            if _evaluar_condicion(tokens[i], variables):
                _ejecutar_accion(tokens[i + 1], variables)
                return
        if len(tokens) % 2 != 0:
            _ejecutar_accion(tokens[-1], variables)
        return

    if expr.upper().startswith("IF("):
        tokens = _dividir_por_comas(expr[3:-1].strip())
        if len(tokens) < 3:
            raise ValueError("IF(...) necesita 3 argumentos: condición, acción si, acción no")
        condicion, accion_si, accion_no = tokens[0], tokens[1], tokens[2]
        _ejecutar_accion(accion_si if _evaluar_condicion(condicion, variables) else accion_no, variables)
        return

    _ejecutar_accion(expr, variables)


APLICAR_EXPRESIONES = ToolManifest(
    id="convertidor.aplicar_expresiones",
    label="aplicar expresiones",
    category="CONVERTIDOR",
    doc=(
        "Aplica, en orden, cada expresión de 'expresiones' sobre 'variables' — "
        "mismo lenguaje que el Gestor de Expresiones del Convertidor original: "
        "IF(condición, acción_si, acción_no), IFS(cond1, acc1, ..., default), "
        "SET({var}, \"valor\"), {var}:\"valor\", UPPER({var}), LOWER({var}), y "
        "condiciones tipo LOWER({movement})=\"00\". Una expresión mal formada se "
        "reporta en 'errores' pero no corta las siguientes. Sin 'expresiones', "
        "las toma de 'plantilla' si se dio una."
    ),
    params=(
        Param("variables", ParamType.JSON, required=True, doc="Contexto inicial, ej. el output de 'parsear nombre'."),
        Param(
            "expresiones", ParamType.JSON, default=[],
            doc='Lista de expresiones, ej. ["IF({movement}=\\"00\\", {type}:\\"B\\", {type}:\\"A\\")"].',
        ),
        _PARAM_PLANTILLA,
    ),
    outputs=(
        Output("variables", ParamType.JSON, doc="El contexto resultante, con las expresiones aplicadas."),
        Output("errores", ParamType.JSON, doc="Expresiones que no se pudieron evaluar, con su motivo."),
    ),
)


def _aplicar_expresiones(ctx: ToolContext) -> ToolResult:
    variables = dict(ctx.params["variables"])
    expresiones, error = _de_plantilla(ctx, ctx.params["expresiones"], "expresiones")
    if error:
        return ToolResult.err(error)
    expresiones = expresiones or []

    errores = []
    for expresion in expresiones:
        try:
            _evaluar_expresion(expresion, variables)
        except Exception as exc:  # una regla rota no debería tumbar las demás
            errores.append(f"{expresion}: {exc}")
            ctx.log(f"expresión inválida, se salta: {expresion} ({exc})", "warning")

    if errores:
        return ToolResult.err(f"{len(errores)} expresión(es) no se pudieron evaluar", variables=variables, errores=errores)
    ctx.log(f"{len(expresiones)} expresión(es) aplicadas → {variables}")
    return ToolResult.ok(variables=variables, errores=[])


_TOOLS = (
    (PARSEAR_NOMBRE, _parsear_nombre),
    (GENERAR_NOMBRE, _generar_nombre),
    (REESCRIBIR_ARCHIVO, _reescribir_archivo),
    (PARSEAR_PTS, _parsear_pts),
    (CORREGIR_PUNTOS, _corregir_puntos),
    (APLICAR_EXPRESIONES, _aplicar_expresiones),
)


def build_plugin() -> Plugin:
    return Plugin(manifest=MANIFEST, tools=[FunctionTool(manifest=m, fn=f) for m, f in _TOOLS])


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
