"""
Plugin `bots` — hablar con **otros Bots** de la red desde un flujo.

Cada Bot expone una API HTTP (la misma que usa su navegador). Este plugin la
usa para que un flujo pueda preguntar qué está haciendo otro Bot, elegir uno
libre, mandarle un caso y esperar el resultado. Con eso se arma un Bot padre
que reparte trabajo entre hijos, o Bots que se coordinan mirando lo que hizo
el otro — todo escrito en Mermaid, sin código.

Quien opera nunca ve una URL en un flujo: los Bots se dan de alta una vez en
la colección **Bots conocidos** (nombre, dirección, nota) y los nodos los
nombran por su nombre. La misma colección tiene la acción **Probar**, que
dice si el otro responde y cuántos runs tiene en vuelo.

Qué hace cada paso, en el idioma de la API del otro Bot:

- `bots.estado`        → `GET /runs/en-vuelo` y `GET /runs?limit=1`
- `bots.elegir_libre`  → lo anterior sobre varios; el que menos tiene en vuelo
- `bots.correr`        → `POST /runs` (sin esperar): vuelve un **ticket**
- `bots.esperar`       → `GET /runs/ticket/<ticket>` hasta que termine

`bots.correr` no bloquea al padre mientras el hijo trabaja: devuelve el ticket
en el acto y `bots.esperar` es el que aguarda, sondeando cada tanto y dejando
en el log del padre en qué paso va el hijo. Así un padre puede mandar a tres
hijos y después esperar a los tres.

Lo que este plugin NO resuelve, a propósito: la carrera entre dos Bots que
miran la misma lista y quieren el mismo caso. Sondear "qué hace el otro" no
alcanza para eso; hace falta un reclamo atómico en la fuente de datos (un
campo "asignado a" que se escribe una sola vez). Ver el README.
"""

from __future__ import annotations

import ast
import json
import re

from backend.core import ports as port_names
from backend.core.contract import (
    Action,
    Field,
    FunctionAction,
    FunctionTool,
    Output,
    Param,
    ParamType,
    Plugin,
    PluginManifest,
    Resource,
    Setting,
    ToolContext,
    ToolManifest,
    ToolResult,
)
from backend.core.ports import PortError

TIMEOUT = "botsTimeout"
PREFIJO_API = "/api/core"
_URL_VALIDA = re.compile(r"^https?://[^\s/]+(:\d+)?$")

BOTS = Resource(
    name="bots",
    label="Bots conocidos",
    item_label="Bot",
    key_field="nombre",
    doc=(
        "Los otros Bots de la red a los que un flujo puede pedirle algo. La "
        "dirección es la que muestra el ícono de la bandeja del otro Bot en "
        "'Copiar dirección para otras PCs' (ej. http://192.168.9.41:8000)."
    ),
    fields=(
        Field("nombre", ParamType.STR, label="Nombre", required=True, doc="Cómo lo nombran los flujos, ej. 'Impresión 2'."),
        Field("url", ParamType.STR, label="Dirección", required=True, doc="http://ip:puerto, sin nada después."),
        Field("nota", ParamType.STR, label="Nota", doc="Qué hace ese Bot, dónde está."),
    ),
)

MANIFEST = PluginManifest(
    name="bots",
    label="Bots",
    version="0.1.0",
    doc="Hablar con otros Bots de la red desde un flujo: qué hacen, mandarles un caso, esperar el resultado.",
    ports=(port_names.HTTP, port_names.CLOCK),
    settings=(
        Setting(
            TIMEOUT, ParamType.INT, label="Segundos antes de dar por caído a un Bot", default=15,
            doc="Cuánto esperar cada respuesta HTTP del otro Bot.",
        ),
    ),
    resources=(BOTS,),
    actions=(
        Action(
            "probar", "Probar",
            doc="Le pregunta al Bot si responde y cuántos runs tiene en vuelo.",
            resource="bots",
            params=(Param("nombre", required=True),),
        ),
    ),
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _bot(ctx: ToolContext, nombre: str):
    """El item de la colección, o un ToolResult de error que quien llama devuelve tal cual."""
    nombre = (nombre or "").strip()
    bot = ctx.resource("bots", nombre, key_field="nombre") if nombre else None
    if bot is None:
        hay = ", ".join(ctx.resource_keys("bots", key_field="nombre")) or "ninguno"
        return ToolResult.err(f"no existe el Bot '{nombre}'. Conocidos: {hay}")
    url = (bot.get("url") or "").strip().rstrip("/")
    if not _URL_VALIDA.match(url):
        return ToolResult.err(f"la dirección de '{nombre}' no es http://ip:puerto: {url!r}")
    return {**bot, "url": url}


def _como_objeto(valor) -> dict | None:
    """
    `row` como dict. Vacío es {}. Si viene como texto —un `row={variable}` que
    el núcleo interpoló: str(dict), con comillas simples de Python, o JSON—
    se parsea. None si no es un objeto.
    """
    if valor is None or valor == "":
        return {}
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        for parser in (json.loads, ast.literal_eval):
            try:
                parseado = parser(valor)
            except (ValueError, SyntaxError):
                continue
            if isinstance(parseado, dict):
                return parseado
    return None


def _timeout(ctx: ToolContext) -> float:
    return float(ctx.config(TIMEOUT) or 15)


def _pedir(ctx: ToolContext, url: str, camino: str, *, method: str = "GET", payload: dict | None = None):
    """Un request a la API del otro Bot. Devuelve (respuesta, json) o levanta PortError."""
    cuerpo = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"} if cuerpo else {}
    respuesta = ctx.port(port_names.HTTP).request(
        f"{url}{PREFIJO_API}{camino}", method=method, headers=headers, body=cuerpo, timeout=_timeout(ctx),
    )
    return respuesta, respuesta.json(default=None)


def _estado_de(ctx: ToolContext, bot: dict) -> dict | ToolResult:
    """Cuántos runs tiene en vuelo y cómo terminó el último. Err si no responde."""
    try:
        resp_vivos, vivos = _pedir(ctx, bot["url"], "/runs/en-vuelo")
        resp_ultimo, ultimos = _pedir(ctx, bot["url"], "/runs?limit=1&include_dry=false")
    except PortError as exc:
        return ToolResult.err(f"'{bot['nombre']}' no responde en {bot['url']}: {exc}")
    if not resp_vivos.ok or not isinstance(vivos, list):
        return ToolResult.err(f"'{bot['nombre']}' respondió {resp_vivos.status} a /runs/en-vuelo: ¿es un Bot?")
    ultimo = ultimos[0] if resp_ultimo.ok and isinstance(ultimos, list) and ultimos else {}
    return {
        "bot": bot["nombre"],
        "url": bot["url"],
        "corriendo": len(vivos),
        "libre": "si" if not vivos else "no",
        "en_vuelo": vivos,
        "ultimo_estado": ultimo.get("status") or "",
        "ultimo_flujo": ultimo.get("flow") or "",
        "ultimo_caso": ultimo.get("case_id") or "",
    }


# ── bots.estado ───────────────────────────────────────────────────────────

ESTADO = ToolManifest(
    id="bots.estado",
    label="estado de un Bot",
    category="BOTS",
    doc=(
        "Qué está haciendo otro Bot: cuántos runs tiene en vuelo, si está libre, "
        "y cómo terminó su último run. Err si no responde. Para ramificar: "
        "{libre} vale 'si' o 'no'."
    ),
    params=(Param("bot", required=True, doc="Nombre en Bots conocidos."),),
    outputs=(
        Output("corriendo", ParamType.INT),
        Output("libre", ParamType.STR, doc="'si' o 'no'."),
        Output("en_vuelo", ParamType.JSON, doc="Los runs en vuelo del otro, con su paso si lo sabe."),
        Output("ultimo_estado", ParamType.STR, doc="ok / err del último run terminado."),
        Output("ultimo_flujo", ParamType.STR),
        Output("ultimo_caso", ParamType.STR),
    ),
)


def _estado(ctx: ToolContext) -> ToolResult:
    bot = _bot(ctx, ctx.params["bot"])
    if isinstance(bot, ToolResult):
        return bot
    estado = _estado_de(ctx, bot)
    if isinstance(estado, ToolResult):
        return estado
    ctx.log(f"{bot['nombre']}: {estado['corriendo']} en vuelo · último {estado['ultimo_estado'] or '—'}")
    return ToolResult.ok(**{k: v for k, v in estado.items() if k not in ("bot", "url")})


# ── bots.elegir_libre ─────────────────────────────────────────────────────

ELEGIR_LIBRE = ToolManifest(
    id="bots.elegir_libre",
    label="elegir el Bot más libre",
    category="BOTS",
    doc=(
        "De una lista de Bots conocidos, el que menos runs tiene en vuelo (en "
        "empate, el primero de la lista). Los que no responden se saltean; err "
        "si ninguno responde. Deja {bot} para usarlo en bots.correr."
    ),
    params=(Param("bots", required=True, doc="Nombres separados por coma, ej. 'Impresión 2, Impresión 3'."),),
    outputs=(
        Output("bot", ParamType.STR),
        Output("corriendo", ParamType.INT, doc="Cuántos tenía en vuelo el elegido."),
        Output("caidos", ParamType.JSON, doc="Los que no respondieron."),
    ),
)


def _elegir_libre(ctx: ToolContext) -> ToolResult:
    nombres = [n.strip() for n in str(ctx.params["bots"]).split(",") if n.strip()]
    if not nombres:
        return ToolResult.err("'bots' vacío: hace falta al menos un nombre")
    mejor, caidos = None, []
    for nombre in nombres:
        bot = _bot(ctx, nombre)
        if isinstance(bot, ToolResult):
            return bot
        estado = _estado_de(ctx, bot)
        if isinstance(estado, ToolResult):
            ctx.log(estado.message, level="warning")
            caidos.append(nombre)
            continue
        if mejor is None or estado["corriendo"] < mejor["corriendo"]:
            mejor = estado
    if mejor is None:
        return ToolResult.err(f"ningún Bot respondió: {', '.join(caidos)}", caidos=caidos)
    ctx.log(f"elegido {mejor['bot']} con {mejor['corriendo']} en vuelo")
    return ToolResult.ok(bot=mejor["bot"], corriendo=mejor["corriendo"], caidos=caidos)


# ── bots.correr ───────────────────────────────────────────────────────────

CORRER = ToolManifest(
    id="bots.correr",
    label="correr un flujo en otro Bot",
    category="BOTS",
    doc=(
        "Le pide a otro Bot que corra un flujo suyo sobre un caso, sin esperar: "
        "vuelve en el acto con un {ticket} para bots.esperar. La fila que recibe "
        "el hijo es 'row' si se da, o {id_externo: case_id} si no — lo que un "
        "flujo que arranca por 'refrescar row' necesita."
    ),
    params=(
        Param("bot", required=True, doc="Nombre en Bots conocidos."),
        Param("flujo", required=True, doc="Nombre del flujo **en el otro Bot**."),
        Param("case_id", required=True),
        Param("row", ParamType.JSON, default={}, doc="La fila para el hijo. Vacío: {id_externo: case_id}."),
        Param("actor", default="", doc="Con qué actor corre en el otro Bot. Vacío: el suyo por defecto."),
    ),
    outputs=(
        Output("ticket", ParamType.STR),
        Output("bot", ParamType.STR),
    ),
)


def _correr(ctx: ToolContext) -> ToolResult:
    bot = _bot(ctx, ctx.params["bot"])
    if isinstance(bot, ToolResult):
        return bot
    case_id = str(ctx.params["case_id"]).strip()
    row = _como_objeto(ctx.params.get("row"))
    if row is None:
        return ToolResult.err("'row' tiene que ser un objeto JSON (o una {variable} que contenga uno)")
    cuerpo = {
        "flow": ctx.params["flujo"], "case_id": case_id,
        "row": row or {"id_externo": case_id},
        "source": f"bot:{ctx.case_id}" if getattr(ctx, "case_id", None) else "bot",
    }
    if ctx.params.get("actor"):
        cuerpo["actor"] = ctx.params["actor"]
    try:
        respuesta, datos = _pedir(ctx, bot["url"], "/runs", method="POST", payload=cuerpo)
    except PortError as exc:
        return ToolResult.err(f"'{bot['nombre']}' no responde en {bot['url']}: {exc}")
    if not respuesta.ok or not isinstance(datos, dict) or not datos.get("ticket"):
        detalle = datos.get("detail") if isinstance(datos, dict) else respuesta.text[:200]
        if isinstance(detalle, dict):
            detalle = detalle.get("message") or json.dumps(detalle, ensure_ascii=False)
        return ToolResult.err(f"'{bot['nombre']}' no aceptó el flujo '{ctx.params['flujo']}' ({respuesta.status}): {detalle}")
    ctx.log(f"{bot['nombre']} ← {ctx.params['flujo']} / {case_id} · ticket {datos['ticket']}")
    return ToolResult.ok(ticket=datos["ticket"], bot=bot["nombre"])


# ── bots.esperar ──────────────────────────────────────────────────────────

ESPERAR = ToolManifest(
    id="bots.esperar",
    label="esperar a otro Bot",
    category="BOTS",
    doc=(
        "Espera a que termine un run pedido con bots.correr, sondeando cada "
        "'cada' segundos y anotando en el log en qué paso va el hijo. ok si el "
        "hijo terminó ok; err si terminó err, si el ticket no existe o si se "
        "agotó 'timeout' (el hijo sigue corriendo: no se lo cancela)."
    ),
    params=(
        Param("bot", required=True),
        Param("ticket", required=True, doc="El {ticket} que devolvió bots.correr."),
        Param("timeout", ParamType.INT, default=900, doc="Segundos máximos de espera."),
        Param("cada", ParamType.INT, default=5, doc="Cada cuántos segundos preguntar."),
    ),
    outputs=(
        Output("estado_hijo", ParamType.STR, doc="ok / err / en_vuelo (si venció el timeout)."),
        Output("run_id", ParamType.STR, doc="El run en el otro Bot, para mirar su traza allá."),
        Output("mensaje", ParamType.STR, doc="El mensaje con el que terminó el hijo."),
        Output("hijo", ParamType.JSON, doc="El resultado completo del run del hijo."),
    ),
    concurrency="concurrent",
)


def _esperar(ctx: ToolContext) -> ToolResult:
    bot = _bot(ctx, ctx.params["bot"])
    if isinstance(bot, ToolResult):
        return bot
    ticket = str(ctx.params["ticket"]).strip()
    limite = max(1, int(ctx.params.get("timeout") or 900))
    cada = max(1, int(ctx.params.get("cada") or 5))
    reloj = ctx.port(port_names.CLOCK)
    inicio = reloj.monotonic()
    ultimo_paso = None
    while True:
        try:
            respuesta, datos = _pedir(ctx, bot["url"], f"/runs/ticket/{ticket}")
        except PortError as exc:
            return ToolResult.err(f"'{bot['nombre']}' dejó de responder: {exc}", estado_hijo="", run_id="", mensaje="", hijo={})
        if not respuesta.ok or not isinstance(datos, dict):
            return ToolResult.err(f"'{bot['nombre']}' respondió {respuesta.status} al ticket {ticket}", estado_hijo="", run_id="", mensaje="", hijo={})
        estado = datos.get("estado")
        if estado == "terminado":
            run = datos.get("run") or {}
            salida = {
                "estado_hijo": run.get("status") or "", "run_id": run.get("run_id") or "",
                "mensaje": run.get("message") or "", "hijo": run,
            }
            if run.get("status") == "ok":
                ctx.log(f"{bot['nombre']} terminó ok · {run.get('run_id') or ''}")
                return ToolResult.ok(**salida)
            return ToolResult.err(f"{bot['nombre']} terminó {run.get('status')}: {run.get('message') or 'sin mensaje'}", **salida)
        if estado == "desconocido":
            return ToolResult.err(f"'{bot['nombre']}' no conoce el ticket {ticket} (¿se reinició?)", estado_hijo="", run_id="", mensaje="", hijo={})
        vivo = datos.get("vivo") or {}
        paso = f"{vivo.get('hechos') or 0}/{vivo.get('total') or '?'} · {vivo.get('paso') or ('en cola' if estado == 'en_cola' else 'en curso')}"
        if paso != ultimo_paso:
            ctx.log(f"{bot['nombre']}: {paso}")
            ultimo_paso = paso
        if reloj.monotonic() - inicio >= limite:
            return ToolResult.err(
                f"{bot['nombre']} sigue corriendo después de {limite} s ({paso}); el run sigue allá",
                estado_hijo="en_vuelo", run_id="", mensaje="", hijo=vivo,
            )
        reloj.sleep(cada)


# ── Action: probar un Bot conocido ────────────────────────────────────────


def _probar(ctx: ToolContext) -> ToolResult:
    bot = _bot(ctx, ctx.params["nombre"])
    if isinstance(bot, ToolResult):
        return bot
    estado = _estado_de(ctx, bot)
    if isinstance(estado, ToolResult):
        return estado
    return ToolResult.ok(
        f"responde · {estado['corriendo']} en vuelo · último run {estado['ultimo_estado'] or 'ninguno'}",
        **{k: v for k, v in estado.items() if k != "url"},
    )


def build_plugin() -> Plugin:
    return Plugin(
        manifest=MANIFEST,
        tools=[
            FunctionTool(manifest=ESTADO, fn=_estado),
            FunctionTool(manifest=ELEGIR_LIBRE, fn=_elegir_libre),
            FunctionTool(manifest=CORRER, fn=_correr),
            FunctionTool(manifest=ESPERAR, fn=_esperar),
        ],
        actions=[FunctionAction(action=MANIFEST.action("probar"), fn=_probar)],
    )


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
