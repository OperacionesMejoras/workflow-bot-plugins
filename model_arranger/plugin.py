"""
Plugin `model-arranger` — manejar el taller de model-arranger desde un flujo:
ingresar órdenes desde una carpeta de STL, agregarles piezas, validarlas,
abrir un nest en una impresora, anidar, cerrarlo y leer cómo quedó cada cosa.

Por qué es un cliente HTTP y no el motor: el nesting (sparrow, un binario
Rust, más numpy/scipy/trimesh) vive en su propio software, `model-arranger`
(repo aparte, OperacionesMejoras), que ya corre como servidor
(`python taller_server.py --port 8780`) con su base `data/arranger.db`. Meter
el motor acá haría pesada cada instalación del Bot y duplicaría la lógica
del taller (hash de cada STL, chequeos de salud, eventos, el estado de la
orden, una tarea por vez porque sparrow usa todos los núcleos). Así el
plugin pide sólo `http` y `clock`, y el taller sigue siendo dueño de su base:
todo lo que escribe va por su API, nunca directo al SQLite.

El contrato es la API 1.x del taller (`GET /api/version`; el mayor sube si
rompe clientes). Lo que importa de él para un flujo:

- Validar, anidar y cerrar son tareas largas: el POST devuelve un
  `tarea_id` y el resultado se lee en `GET /api/tarea?id=`. Por defecto cada
  tool espera a que termine (con `clock`, cancelable) y devuelve lo que
  quedó; con `esperar=false` sale enseguida con el `tarea_id`, para
  `model_arranger.esperar_tarea` más adelante.
- Una tarea por vez: si hay otra corriendo, el taller contesta 409 con el id
  de esa. El plugin espera a que termine y reintenta una vez, en vez de
  fallar: dos flujos que anidan a la vez se ponen en fila solos.
- El historial de tareas vive en memoria del taller: si se reinició, un
  `tarea_id` viejo da 400 "tarea desconocida". Eso no es un error del flujo:
  se relee la orden o el nest, que sí están en la base, y se informa lo que
  dicen (un cierre que no terminó es `err`: el nest sigue abierto).
- Los errores de negocio ("pieza en un nest cerrado", "orden inexistente")
  son 400 con `{error}`: salen tal cual en el `err` del tool.
- Los archivos van por ruta de carpeta: el Bot y el taller en la misma PC, o
  una carpeta compartida que los dos ven con la misma ruta.

Las órdenes, piezas y nests se pueden ver en la grilla del panel principal
sin escribir nada a mano: el botón "Crear fuentes en Conexiones" deja las
sources `http` de `connections` apuntando a los listados del taller, por la
API local del Bot (el mismo camino que usa `oauth` para crear las de Gmail).

Borrar no está expuesto: el taller lo permite, pero desde un flujo es fácil
borrar lo que no era. Si hace falta, va como tool `dangerous`.
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import quote, urlencode

from backend.core import ports as port_names
from backend.core.contract import (
    Action,
    FunctionAction,
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

URL = "url"
TOKEN = "token"
TIMEOUT = "timeout"
ESPERA_MAX = "espera_max"
CADA = "cada"
MI_DIRECCION = "direccion_bot"

URL_POR_DEFECTO = "http://127.0.0.1:8780"
# El mayor de la API del taller con el que este plugin sabe hablar.
API_MAYOR = 1
CATEGORIA = "MODEL ARRANGER"
ARRANCAR_TALLER = (
    "El taller de model-arranger tiene que estar corriendo aparte: en su carpeta, "
    "'python taller_server.py --port 8780'."
)

# Las acciones de la pantalla del plugin (botones), no nodos de flujo.
PROBAR = Action("probar", "Probar el taller", doc="Revisa que el taller responda y muestra qué tiene.")


CREAR_FUENTES = Action(
    "crear_fuentes", "Crear fuentes en Conexiones",
    doc="Crea en Conexiones las fuentes de la grilla del panel principal (órdenes, piezas, piezas con falla, "
    "nests) apuntando a la dirección del taller de Config. Las que ya existen no se tocan.",
    params=(Param("pisar", ParamType.BOOL, default=False, doc="Reemplazar las que ya existen con el mismo nombre."),),
)


MANIFEST = PluginManifest(
    name="model-arranger",
    label="Model Arranger",
    version="0.1.1",
    doc=(
        "Órdenes, piezas y nests del taller de model-arranger desde un flujo: ingresar una carpeta de "
        "STL, validar, anidar en la cama de una impresora, cerrar y leer el resultado. "
        + ARRANCAR_TALLER
    ),
    ports=(port_names.HTTP, port_names.CLOCK),
    actions=(PROBAR, CREAR_FUENTES),
    settings=(
        Setting(
            URL, ParamType.STR, label="Dirección del taller", default=URL_POR_DEFECTO,
            doc="Donde corre taller_server.py: http://<PC>:<puerto>. " + ARRANCAR_TALLER,
        ),
        Setting(
            TOKEN, ParamType.STR, label="Token", secret=True,
            doc="Si el taller pide token, va como 'Authorization: Bearer'. Vacío si no pide.",
        ),
        Setting(
            TIMEOUT, ParamType.INT, label="Segundos por pedido", default=30,
            doc="Cuánto esperar cada respuesta del taller. Las tareas largas no cuentan: esas se esperan aparte.",
        ),
        Setting(
            ESPERA_MAX, ParamType.INT, label="Minutos máximos por tarea", default=30,
            doc="Cuánto esperar a que termine una validación, un anidado o un cierre antes de dar err. "
            "La tarea sigue en el taller: se puede retomar con 'esperar tarea'.",
        ),
        Setting(
            CADA, ParamType.INT, label="Segundos entre consultas", default=3,
            doc="Cada cuánto preguntarle al taller cómo va una tarea.",
        ),
        Setting(
            MI_DIRECCION, ParamType.STR, label="Dirección de este Bot",
            doc="Normalmente VACÍA: sola resuelve a http://127.0.0.1:<puerto de este Bot>. Sólo la usa "
            "el botón que crea las fuentes en Conexiones.",
        ),
    ),
)


# ── El taller, por HTTP ───────────────────────────────────────────────────

class _Falla(Exception):
    """Un pedido al taller que no salió: el mensaje ya es el del `err` del tool."""

    def __init__(self, mensaje: str, *, ocupado_por: int | None = None):
        super().__init__(mensaje)
        self.ocupado_por = ocupado_por


def _base(ctx: ToolContext) -> str:
    return str(ctx.config(URL) or URL_POR_DEFECTO).strip().rstrip("/")


def _pedir(ctx: ToolContext, metodo: str, camino: str, payload: dict | None = None, query: dict | None = None):
    """El JSON de un pedido al taller, o `_Falla` con un mensaje que se entiende."""
    url = f"{_base(ctx)}{camino}"
    if query:
        url += "?" + urlencode({k: v for k, v in query.items() if v not in (None, "")})
    headers = {"Accept": "application/json"}
    cuerpo = None
    if payload is not None:
        cuerpo = json.dumps(payload, ensure_ascii=False)
        headers["Content-Type"] = "application/json"
    token = str(ctx.config(TOKEN) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        respuesta = ctx.port(port_names.HTTP).request(
            url, method=metodo, headers=headers, body=cuerpo, timeout=float(ctx.config(TIMEOUT) or 30),
        )
    except PortError as exc:
        raise _Falla(f"el taller no responde en {_base(ctx)} ({exc}). {ARRANCAR_TALLER}") from None
    datos = respuesta.json(default=None)
    if respuesta.status == 409 and isinstance(datos, dict) and datos.get("ocupado"):
        raise _Falla(str(datos.get("error") or "el taller está ocupado"), ocupado_por=datos.get("tarea_id"))
    if respuesta.status in (401, 403):
        raise _Falla(f"el taller rechazó el pedido ({respuesta.status}): revisá el token en Config")
    if not respuesta.ok:
        detalle = datos.get("error") if isinstance(datos, dict) else respuesta.text[:200]
        raise _Falla(f"el taller respondió {respuesta.status}: {detalle}")
    if datos is None:
        raise _Falla(f"respuesta que no es JSON desde {url}: ¿es el taller de model-arranger?")
    return datos


def _esperar(ctx: ToolContext, tarea_id: int) -> dict:
    """
    El estado final de una tarea del taller (`GET /api/tarea`), esperando con
    el reloj del núcleo: cancelar la corrida corta la espera, no la tarea.
    """
    reloj = ctx.port(port_names.CLOCK)
    cada = max(1.0, float(ctx.config(CADA) or 3))
    limite = reloj.monotonic() + 60 * float(ctx.config(ESPERA_MAX) or 30)
    ultima = ""
    while True:
        try:
            estado = _pedir(ctx, "GET", "/api/tarea", query={"id": tarea_id})
        except _Falla as falla:
            # El historial de tareas vive en memoria del taller: si se reinició,
            # la tarea ya no está. No hay resultado que leer; quien llama relee
            # la orden o el nest, que sí están en la base.
            if "tarea desconocida" not in str(falla):
                raise
            ctx.log(f"el taller ya no conoce la tarea {tarea_id} (¿se reinició?): releo el estado")
            return {"tarea_id": tarea_id, "tarea": "", "corriendo": False, "error": "",
                    "resultado": None, "perdida": True}
        if not estado.get("corriendo"):
            if estado.get("error"):
                raise _Falla(f"la tarea {tarea_id} ({estado.get('tarea')}) falló en el taller: {estado['error']}")
            return estado
        etapa = f"{estado.get('etapa') or ''} {estado.get('hecho') or 0}/{estado.get('total') or 0}".strip()
        if etapa != ultima:
            ctx.log(f"tarea {tarea_id} ({estado.get('tarea')}): {etapa}")
            ultima = etapa
        if ctx.cancelled:
            raise _Falla(f"corrida cancelada; la tarea {tarea_id} sigue en el taller")
        if reloj.monotonic() >= limite:
            raise _Falla(
                f"la tarea {tarea_id} sigue corriendo después de {ctx.config(ESPERA_MAX) or 30} min; "
                "se puede retomar con 'esperar tarea'"
            )
        reloj.sleep(cada, ctx.is_cancelled_check)


def _lanzar(ctx: ToolContext, camino: str, payload: dict) -> dict:
    """
    Un POST que arranca una tarea. Si el taller está ocupado con otra, espera
    a que esa termine y reintenta una vez: una sola tarea a la vez es una
    regla del taller (sparrow usa todos los núcleos), no un error del flujo.
    """
    try:
        return _pedir(ctx, "POST", camino, payload)
    except _Falla as falla:
        if not falla.ocupado_por:
            raise
        ctx.log(f"el taller está con la tarea {falla.ocupado_por}: espero a que termine")
        try:
            _esperar(ctx, int(falla.ocupado_por))
        except _Falla as otra:
            # Que la tarea ajena haya fallado no es asunto de esta: se reintenta igual.
            if "falló en el taller" not in str(otra):
                raise
        return _pedir(ctx, "POST", camino, payload)


def _con_espera(ctx: ToolContext, lanzado: dict) -> dict | None:
    """El estado final de la tarea de `lanzado` si el nodo pidió esperar; si no, None."""
    if not ctx.params.get("esperar", True) or not lanzado.get("tarea_id"):
        return None
    return _esperar(ctx, int(lanzado["tarea_id"]))


def _herramienta(fn):
    """Convierte un `_Falla` en el `err` del tool, para no repetir el try en cada uno."""

    def envuelta(ctx: ToolContext) -> ToolResult:
        try:
            return fn(ctx)
        except _Falla as falla:
            return ToolResult.err(str(falla))

    return envuelta


_CODIGO = re.compile(r"^\s*(?:([OPN])-)?0*(\d+)\s*$", re.IGNORECASE)


def _id(valor, prefijo: str, que: str) -> int:
    """
    El id numérico de una orden (O), pieza (P) o nest (N). Acepta el id o el
    código que muestra el taller (`O-0003`, `P-000041`): el código es el id
    con prefijo y ceros (`db.insertar` del taller), así que no hace falta
    preguntarle a nadie.
    """
    m = _CODIGO.match(str(valor if valor is not None else ""))
    if not m or (m.group(1) and m.group(1).upper() != prefijo):
        raise _Falla(f"'{valor}' no es un id ni un código de {que} ({prefijo}-0001)")
    return int(m.group(2))


# ── Salud ─────────────────────────────────────────────────────────────────

def _salud(ctx: ToolContext) -> dict:
    """Versión, si está ocupado y las cuentas por estado. `_Falla` si no sirve."""
    version = _pedir(ctx, "GET", "/api/version")
    texto = str(version.get("version") or "")
    try:
        mayor = int(texto.split(".")[0])
    except ValueError:
        raise _Falla(f"{_base(ctx)} no dice qué versión de API habla: ¿es el taller de model-arranger?") from None
    if mayor != API_MAYOR:
        raise _Falla(f"el taller habla la API {texto} y este plugin la {API_MAYOR}.x: actualizá uno de los dos")
    progreso = _pedir(ctx, "GET", "/api/progreso")
    resumen = _pedir(ctx, "GET", "/api/resumen")

    def cuentas(filas) -> dict:
        return {f["estado"]: f["n"] for f in filas or [] if isinstance(f, dict) and "estado" in f}

    return {
        "version": texto,
        "ocupado": bool(progreso.get("corriendo")),
        "tarea": progreso.get("tarea") if progreso.get("corriendo") else "",
        "ordenes": cuentas(resumen.get("ordenes")),
        "piezas": cuentas(resumen.get("piezas")),
        "nests": cuentas(resumen.get("nests")),
    }


def _resumen_salud(s: dict) -> str:
    estado = f"ocupado con {s['tarea']}" if s["ocupado"] else "libre"
    return (
        f"taller API {s['version']}, {estado}; órdenes {sum(s['ordenes'].values())}, "
        f"piezas {sum(s['piezas'].values())}, nests {sum(s['nests'].values())}"
    )


@_herramienta
def _tool_salud(ctx: ToolContext) -> ToolResult:
    s = _salud(ctx)
    return ToolResult.ok(_resumen_salud(s), **s)


# ── Órdenes ───────────────────────────────────────────────────────────────

def _orden(ctx: ToolContext, oid: int) -> dict:
    datos = _pedir(ctx, "GET", "/api/orden", query={"id": oid})
    orden = dict(datos.get("orden") or {})
    orden["cuentas"] = datos.get("cuentas") or {}
    orden["nests"] = datos.get("nests") or []
    return orden


def _ordenes_tras(ctx: ToolContext, ids: list[int]) -> dict:
    """Las órdenes como quedaron, y si todas quedaron `lista` (para un nodo de decisión)."""
    ordenes = [_orden(ctx, i) for i in ids]
    return {
        "ordenes": ordenes,
        "todas_listas": bool(ordenes) and all(o.get("estado") == "lista" for o in ordenes),
        "con_falla": [o.get("codigo") for o in ordenes if o.get("estado") == "con_falla"],
    }


@_herramienta
def _ver_carpeta(ctx: ToolContext) -> ToolResult:
    datos = _pedir(ctx, "POST", "/api/ordenes", {
        "carpeta": ctx.params["carpeta"], "regex": ctx.params.get("regex") or None, "solo_ver": True,
    })
    ordenes, fuera = datos.get("ordenes") or [], datos.get("fuera") or []
    return ToolResult.ok(
        f"{len(ordenes)} orden(es) saldrían de la carpeta; {len(fuera)} archivo(s) quedan afuera",
        ordenes=ordenes, fuera=fuera, cantidad=len(ordenes),
    )


@_herramienta
def _ingresar(ctx: ToolContext) -> ToolResult:
    lanzado = _lanzar(ctx, "/api/ordenes", {
        "carpeta": ctx.params["carpeta"], "regex": ctx.params.get("regex") or None,
    })
    ids = [int(i) for i in lanzado.get("ids") or []]
    if not ids:
        return ToolResult.err("la carpeta no tiene STL que matcheen el patrón del taller", ids=[], tarea_id=0)
    final = _con_espera(ctx, lanzado)
    salida = {"ids": ids, "tarea_id": lanzado.get("tarea_id") or 0}
    if final is None:
        return ToolResult.ok(f"{len(ids)} orden(es) ingresada(s), validando (tarea {salida['tarea_id']})", **salida)
    salida.update(_ordenes_tras(ctx, ids))
    return ToolResult.ok(_resumen_ordenes(salida), **salida)


def _resumen_ordenes(salida: dict) -> str:
    codigos = ", ".join(f"{o.get('codigo')} {o.get('estado')}" for o in salida["ordenes"])
    return f"{len(salida['ordenes'])} orden(es): {codigos}"


@_herramienta
def _agregar_piezas(ctx: ToolContext) -> ToolResult:
    oid = _id(ctx.params["orden"], "O", "orden")
    lanzado = _lanzar(ctx, "/api/orden/agregar", {
        "id": oid, "carpeta": ctx.params["carpeta"], "regex": ctx.params.get("regex") or None,
    })
    final = _con_espera(ctx, lanzado)
    salida = {"ids": [oid], "tarea_id": lanzado.get("tarea_id") or 0}
    if final is None:
        return ToolResult.ok(f"piezas agregadas a la orden {oid}, validando (tarea {salida['tarea_id']})", **salida)
    salida.update(_ordenes_tras(ctx, [oid]))
    return ToolResult.ok(_resumen_ordenes(salida), **salida)


@_herramienta
def _validar(ctx: ToolContext) -> ToolResult:
    oid = _id(ctx.params["orden"], "O", "orden")
    lanzado = _lanzar(ctx, "/api/orden/validar", {"id": oid})
    final = _con_espera(ctx, lanzado)
    salida = {"ids": [oid], "tarea_id": lanzado.get("tarea_id") or 0}
    if final is None:
        return ToolResult.ok(f"validando la orden {oid} (tarea {salida['tarea_id']})", **salida)
    salida.update(_ordenes_tras(ctx, [oid]))
    return ToolResult.ok(_resumen_ordenes(salida), **salida)


@_herramienta
def _prioridad(ctx: ToolContext) -> ToolResult:
    oid = _id(ctx.params["orden"], "O", "orden")
    _pedir(ctx, "POST", "/api/orden/prioridad", {"id": oid, "prioridad": int(ctx.params["prioridad"])})
    return ToolResult.ok(f"orden {oid} con prioridad {ctx.params['prioridad']}")


@_herramienta
def _ver_orden(ctx: ToolContext) -> ToolResult:
    orden = _orden(ctx, _id(ctx.params["orden"], "O", "orden"))
    return ToolResult.ok(f"{orden.get('codigo')}: {orden.get('estado')}", orden=orden, estado=orden.get("estado") or "")


@_herramienta
def _ver_pieza(ctx: ToolContext) -> ToolResult:
    pieza = _pedir(ctx, "GET", "/api/pieza", query={"id": _id(ctx.params["pieza"], "P", "pieza")})
    return ToolResult.ok(f"{pieza.get('codigo')}: {pieza.get('estado')}", pieza=pieza, estado=pieza.get("estado") or "")


# ── Nests ─────────────────────────────────────────────────────────────────

def _impresora(ctx: ToolContext, valor) -> int:
    """El id de una impresora por id o por nombre (sin distinguir mayúsculas)."""
    texto = str(valor or "").strip()
    if texto.isdigit():
        return int(texto)
    impresoras = _pedir(ctx, "GET", "/api/impresoras")
    for imp in impresoras if isinstance(impresoras, list) else []:
        if str(imp.get("nombre") or "").strip().lower() == texto.lower():
            return int(imp["id"])
    nombres = ", ".join(str(i.get("nombre")) for i in impresoras or []) or "ninguna"
    raise _Falla(f"no hay una impresora '{texto}' en el taller (hay: {nombres})")


def _nest(ctx: ToolContext, nid: int) -> dict:
    datos = _pedir(ctx, "GET", "/api/nest", query={"id": nid})
    nest = dict(datos.get("nest") or {})
    nest["cuentas"] = datos.get("cuentas") or {}
    nest["ordenes"] = datos.get("ordenes") or []
    return nest


def _salida_nest(nest: dict) -> dict:
    return {
        "nest": nest,
        "estado": nest.get("estado") or "",
        "densidad": nest.get("densidad") or 0.0,
        "stl": nest.get("stl") or "",
    }


@_herramienta
def _abrir_nest(ctx: ToolContext) -> ToolResult:
    impresora = _impresora(ctx, ctx.params["impresora"])
    datos = _pedir(ctx, "POST", "/api/nests", {"impresora_id": impresora, "modo": ctx.params.get("modo") or "aprovechamiento"})
    nest = _nest(ctx, int(datos["id"]))
    return ToolResult.ok(f"nest {nest.get('codigo')} abierto en {nest.get('impresora')}", nest_id=int(datos["id"]),
                         **_salida_nest(nest))


@_herramienta
def _anidar(ctx: ToolContext) -> ToolResult:
    nid = _id(ctx.params["nest"], "N", "nest")
    lanzado = _lanzar(ctx, "/api/nest/anidar", {"id": nid})
    final = _con_espera(ctx, lanzado)
    if final is None:
        return ToolResult.ok(f"anidando el nest {nid} (tarea {lanzado.get('tarea_id')})", tarea_id=lanzado.get("tarea_id") or 0)
    probadas = (final.get("resultado") or {}).get("probadas") or []
    entraron = [p["orden_id"] for p in probadas if p.get("entro")]
    nest = _nest(ctx, nid)
    if final.get("perdida"):
        # Sin el resultado de la tarea no se sabe qué órdenes probó: sólo cómo quedó el nest.
        cuantas = "no se sabe cuáles órdenes entraron (el taller perdió la tarea)"
    else:
        cuantas = f"entraron {len(entraron)} de {len(probadas)} orden(es)"
    return ToolResult.ok(
        f"{nest.get('codigo')}: {cuantas}, densidad {nest.get('densidad') or 0}, {nest.get('estado')}",
        tarea_id=lanzado.get("tarea_id") or 0, probadas=probadas, entraron=entraron, **_salida_nest(nest),
    )


@_herramienta
def _cerrar_nest(ctx: ToolContext) -> ToolResult:
    nid = _id(ctx.params["nest"], "N", "nest")
    lanzado = _lanzar(ctx, "/api/nest/cerrar", {"id": nid})
    final = _con_espera(ctx, lanzado)
    if final is None:
        return ToolResult.ok(f"cerrando el nest {nid} (tarea {lanzado.get('tarea_id')})", tarea_id=lanzado.get("tarea_id") or 0)
    nest = _nest(ctx, nid)
    salida = _salida_nest(nest)
    salida["stl"] = (final.get("resultado") or {}).get("stl") or salida["stl"]
    if salida["estado"] != "cerrado":
        # Pasa si el taller se reinició a mitad del cierre: la tarea se perdió y el nest sigue abierto.
        return ToolResult.err(f"{nest.get('codigo')} sigue {salida['estado'] or 'sin estado'}: el cierre no terminó",
                              tarea_id=lanzado.get("tarea_id") or 0, **salida)
    return ToolResult.ok(f"{nest.get('codigo')} cerrado: {salida['stl']}", tarea_id=lanzado.get("tarea_id") or 0, **salida)


@_herramienta
def _ver_nest(ctx: ToolContext) -> ToolResult:
    nest = _nest(ctx, _id(ctx.params["nest"], "N", "nest"))
    return ToolResult.ok(f"{nest.get('codigo')}: {nest.get('estado')}", **_salida_nest(nest))


@_herramienta
def _esperar_tarea(ctx: ToolContext) -> ToolResult:
    final = _esperar(ctx, int(ctx.params["tarea_id"]))
    if final.get("perdida"):
        return ToolResult.err(
            f"el taller ya no conoce la tarea {final['tarea_id']} (se reinició o salió del historial): "
            "leé la orden o el nest para ver cómo quedó", tarea="", resultado=None,
        )
    return ToolResult.ok(f"tarea {final.get('tarea_id')} ({final.get('tarea')}) terminada",
                         tarea=final.get("tarea") or "", resultado=final.get("resultado"))


# ── Manifests de los tools ────────────────────────────────────────────────

_ESPERAR = Param(
    "esperar", ParamType.BOOL, default=True,
    doc="Esperar a que el taller termine y devolver cómo quedó. false: sale enseguida con {tarea_id}.",
)
_ORDEN = Param("orden", required=True, doc="Id o código de la orden (ej. 3 u O-0003).")
_NEST = Param("nest", required=True, doc="Id o código del nest (ej. 2 o N-0002).")
_CARPETA = Param("carpeta", ParamType.PATH, required=True,
                 doc="Carpeta con los STL tal como la ve la PC del taller, que puede no ser la del Bot "
                      "(ej. /home/.../CASOS TERMINADOS/AB123 si el taller corre en Linux).")
_REGEX = Param("regex", doc="Vacío: el patrón de archivos guardado en el taller.")
_SALIDA_ORDENES = (
    Output("ids", ParamType.JSON, doc="Ids de las órdenes."),
    Output("tarea_id", ParamType.INT, doc="La tarea de validación en el taller."),
    Output("ordenes", ParamType.JSON, doc="Cada orden como quedó (con esperar): codigo, estado, cuentas."),
    Output("todas_listas", ParamType.BOOL, doc="true si todas quedaron 'lista' para anidar."),
    Output("con_falla", ParamType.JSON, doc="Códigos de las que quedaron con falla de salud."),
)
_SALIDA_NEST = (
    Output("nest", ParamType.JSON, doc="El nest: codigo, estado, modo, densidad, stl, renders, cuentas."),
    Output("estado", ParamType.STR, doc="'abierto' o 'cerrado'."),
    Output("densidad", ParamType.FLOAT),
    Output("stl", ParamType.STR, doc="Ruta del STL de la cama, cuando está cerrado, en la PC del taller."),
)


def _tool(id_: str, label: str, doc: str, params=(), outputs=(), lee=False) -> ToolManifest:
    extra = {"dry_run": "run"} if lee else {}
    return ToolManifest(id=f"model_arranger.{id_}", label=label, category=CATEGORIA, doc=doc,
                        params=tuple(params), outputs=tuple(outputs), **extra)


SALUD = _tool(
    "salud", "salud del taller",
    "Revisa que el taller de model-arranger responda y hable una API compatible. ok con la versión, si está "
    "ocupado y las cuentas por estado; err si no responde o es incompatible. " + ARRANCAR_TALLER,
    outputs=(
        Output("version", ParamType.STR), Output("ocupado", ParamType.BOOL),
        Output("tarea", ParamType.STR, doc="La tarea que está corriendo, si está ocupado."),
        Output("ordenes", ParamType.JSON, doc="{estado: cantidad}"), Output("piezas", ParamType.JSON),
        Output("nests", ParamType.JSON),
    ),
    lee=True,
)

TOOLS = (
    (SALUD, _tool_salud),
    (_tool("ver_carpeta", "ver qué órdenes saldrían",
           "Sin crear nada: qué órdenes saldrían de la carpeta y qué archivos quedan afuera del patrón.",
           (_CARPETA, _REGEX),
           (Output("ordenes", ParamType.JSON, doc="[{id_externo, archivos}]"), Output("fuera", ParamType.JSON),
            Output("cantidad", ParamType.INT)), lee=True), _ver_carpeta),
    (_tool("ingresar", "ingresar órdenes",
           "Crea una orden por cada id externo de la carpeta, copia sus STL al taller y los valida.",
           (_CARPETA, _REGEX, _ESPERAR), _SALIDA_ORDENES), _ingresar),
    (_tool("agregar_piezas", "agregar piezas a una orden",
           "Suma a una orden existente los STL de una carpeta y la vuelve a validar.",
           (_ORDEN, _CARPETA, _REGEX, _ESPERAR), _SALIDA_ORDENES), _agregar_piezas),
    (_tool("validar", "validar orden", "Vuelve a correr los chequeos de salud de las piezas de una orden.",
           (_ORDEN, _ESPERAR), _SALIDA_ORDENES), _validar),
    (_tool("prioridad", "prioridad de orden",
           "Cambia la prioridad de una orden: al anidar entran primero las de prioridad más alta.",
           (_ORDEN, Param("prioridad", ParamType.INT, required=True))), _prioridad),
    (_tool("orden", "ver orden", "Lee una orden: estado, cuentas de piezas por estado y nests en que está.",
           (_ORDEN,), (Output("orden", ParamType.JSON), Output("estado", ParamType.STR)), lee=True), _ver_orden),
    (_tool("pieza", "ver pieza",
           "Lee una pieza: estado, chequeos de salud, métricas, renders y si está repetida en otra orden.",
           (Param("pieza", required=True, doc="Id o código de la pieza (ej. 41 o P-000041)."),),
           (Output("pieza", ParamType.JSON), Output("estado", ParamType.STR)), lee=True), _ver_pieza),
    (_tool("abrir_nest", "abrir nest",
           "Abre un nest en una impresora. 'unico': se cierra solo al entrar la orden; 'aprovechamiento': "
           "sigue sumando órdenes 'lista' hasta que ninguna entra, y se cierra con 'cerrar nest'.",
           (Param("impresora", required=True, doc="Id o nombre de la impresora del taller."),
            Param("modo", ParamType.ENUM, default="aprovechamiento", choices=("aprovechamiento", "unico"))),
           (Output("nest_id", ParamType.INT),) + _SALIDA_NEST), _abrir_nest),
    (_tool("anidar", "anidar",
           "Prueba las órdenes 'lista' en el nest, por prioridad: una orden entra entera o espera.",
           (_NEST, _ESPERAR),
           (Output("tarea_id", ParamType.INT),
            Output("probadas", ParamType.JSON, doc="[{orden_id, entro}]"),
            Output("entraron", ParamType.JSON, doc="Ids de las órdenes que entraron.")) + _SALIDA_NEST), _anidar),
    (_tool("cerrar_nest", "cerrar nest", "Cierra el nest y exporta el STL de la cama.",
           (_NEST, _ESPERAR), (Output("tarea_id", ParamType.INT),) + _SALIDA_NEST), _cerrar_nest),
    (_tool("nest", "ver nest", "Lee un nest: estado, densidad, STL, renders y órdenes que tiene.",
           (_NEST,), _SALIDA_NEST, lee=True), _ver_nest),
    (_tool("esperar_tarea", "esperar tarea",
           "Espera a que termine una tarea del taller lanzada con esperar=false.",
           (Param("tarea_id", ParamType.INT, required=True),),
           (Output("tarea", ParamType.STR), Output("resultado", ParamType.JSON)), lee=True), _esperar_tarea),
)


# ── Acciones de la pantalla del plugin ────────────────────────────────────

@_herramienta
def _probar(ctx: ToolContext) -> ToolResult:
    s = _salud(ctx)
    return ToolResult.ok(_resumen_salud(s), **s)


def _fuentes(base: str) -> list[dict]:
    """
    Los sources `http` de `connections` para los listados del taller. Paginan
    del lado del taller (`pagina`/`por_pagina`, tope 500) y la clave de cada
    fila es el código (O-0001…). Lo que no sirve leer en una grilla (hash,
    matriz, json de salud, rutas internas) se oculta, pero sigue llegando al
    flujo de la fila.
    """

    def fuente(nombre, camino, ocultas):
        return {
            "name": f"Model Arranger - {nombre}", "kind": "http", "method": "GET", "url": f"{base}{camino}",
            "headers": {"Accept": "application/json"}, "payload": {}, "results_path": "filas",
            "key_field": "codigo", "page_param": "pagina", "page_size_param": "por_pagina", "page_size": 50,
            "total_path": "total", "columnas_ocultas": ocultas,
        }

    ocultas_pieza = ["hash", "salud", "matriz", "bbox", "archivo", "render_iso", "render_top", "render_base"]
    return [
        fuente("órdenes", "/api/ordenes", ["carpeta_origen"]),
        fuente("piezas", "/api/piezas", ocultas_pieza),
        fuente("piezas con falla", "/api/piezas?estado=salud_falla", ocultas_pieza),
        fuente("nests", "/api/nests", ["stl", "render_iso", "render_top"]),
    ]


def _este_bot(ctx: ToolContext) -> str:
    # Mismo criterio que `oauth`/`bots`: BOT_PORT lo deja la app al arrancar.
    puerto = (os.environ.get("BOT_PORT") or "8000").strip()
    return str(ctx.config(MI_DIRECCION) or f"http://127.0.0.1:{puerto}").strip().rstrip("/")


def _crear_fuentes(ctx: ToolContext) -> ToolResult:
    http = ctx.port(port_names.HTTP)
    pisar = bool(ctx.params.get("pisar"))
    creadas, salteadas = [], []
    for fuente in _fuentes(_base(ctx)):
        camino = f"{_este_bot(ctx)}/api/core/resources/connections/sources/{quote(fuente['name'], safe='')}"
        try:
            if not pisar and http.request(camino, method="GET", timeout=20.0).ok:
                salteadas.append(fuente["name"])
                continue
            respuesta = http.request(
                camino, method="PUT", headers={"Content-Type": "application/json"},
                body=json.dumps({"item": fuente}, ensure_ascii=False), timeout=20.0,
            )
        except PortError as exc:
            return ToolResult.err(f"no se pudo escribir en {_este_bot(ctx)}: {exc}", creadas=creadas, salteadas=salteadas)
        if not respuesta.ok:
            datos = respuesta.json(default=None)
            detalle = datos.get("detail") if isinstance(datos, dict) else respuesta.text[:200]
            return ToolResult.err(f"'{fuente['name']}': este Bot respondió {respuesta.status}: {detalle}",
                                  creadas=creadas, salteadas=salteadas)
        creadas.append(fuente["name"])
    mensaje = f"{len(creadas)} fuente(s) creada(s)" + (f"; ya existían: {', '.join(salteadas)}" if salteadas else "")
    return ToolResult.ok(mensaje, creadas=creadas, salteadas=salteadas)


def build_plugin() -> Plugin:
    return Plugin(
        manifest=MANIFEST,
        tools=[FunctionTool(manifest=m, fn=fn) for m, fn in TOOLS],
        actions=[FunctionAction(action=PROBAR, fn=_probar), FunctionAction(action=CREAR_FUENTES, fn=_crear_fuentes)],
    )


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
