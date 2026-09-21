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

`bots.comparar` es de otra familia: no reparte trabajo, compara **contenido**
entre dos Bots —los flujos que tiene cada uno, o los items de una colección de
un plugin— y devuelve qué está sólo en uno, sólo en el otro, y qué difiere.
Existe porque mover un flujo o una plantilla de un Bot a otro hoy se hace a
mano, y lo primero que hace falta para hacerlo sin pisar nada es ver qué
cambia. Sólo lee: no copia nada.

Lo que `comparar` NO puede ver, y por qué importa: los campos que una colección
declaró `secret` vuelven tapados, así que dos items cuyos campos visibles son
todos iguales pueden diferir justo en el secreto. Por eso hay un estado
`indeterminado` además de `igual`: decir "igual" ahí sería afirmar algo que no
se puede ver. La migración completa —elegir qué pisar, y los secretos— es una
pantalla de la app, no un flujo: un plugin no puede pedirle nada a la base
(`storage` y `crypto` son ports del núcleo) ni dibujar una pantalla (sólo
declara colecciones y botones).

Tampoco asume que del otro lado haya un plugin instalado, sólo que hay un Bot:
si el destino no tiene la colección que se le pide, eso es un resultado —todo
lo de acá queda como "sólo en el origen"— y no una falla. Es el caso de un Bot
nuevo de la flota, que todavía no tiene nada.

Lo que este plugin NO resuelve, a propósito: la carrera entre dos Bots que
miran la misma lista y quieren el mismo caso. Sondear "qué hace el otro" no
alcanza para eso; hace falta un reclamo atómico en la fuente de datos (un
campo "asignado a" que se escribe una sola vez). Ver el README.
"""

from __future__ import annotations

import ast
import json
import re
from urllib.parse import quote

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
MI_DIRECCION = "botsMiDireccion"
PREFIJO_API = "/api/core"
_URL_VALIDA = re.compile(r"^https?://[^\s/]+(:\d+)?$")

# De un flujo, lo que significa algo al compararlo. `updated_at` queda afuera:
# difiere siempre —son dos bases distintas— y no dice nada del contenido.
_CAMPOS_FLUJO = ("content", "folder", "state", "description")
# De un item de colección, lo que NO es del plugin sino del núcleo.
_CAMPOS_DEL_NUCLEO = ("_updated_at", "_error")

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
    version="0.2.0",
    doc="Hablar con otros Bots de la red desde un flujo: qué hacen, mandarles un caso, esperar el resultado.",
    ports=(port_names.HTTP, port_names.CLOCK),
    settings=(
        Setting(
            TIMEOUT, ParamType.INT, label="Segundos antes de dar por caído a un Bot", default=15,
            doc="Cuánto esperar cada respuesta HTTP del otro Bot.",
        ),
        Setting(
            MI_DIRECCION, ParamType.STR, label="Dirección de este Bot", default="http://127.0.0.1:8000",
            doc="La usa 'comparar' cuando no se le da un 'origen': es este Bot. Cambiala sólo si esta "
            "instalación no escucha en el puerto 8000.",
        ),
    ),
    resources=(BOTS,),
    actions=(
        Action(
            "probar", "Probar",
            doc="Le pregunta al Bot si responde y cuántos runs tiene en vuelo.",
            resource="bots",
            params=(Param("nombre", required=True, options_from="bots"),),
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


def _este_bot(ctx: ToolContext) -> dict | ToolResult:
    """Este Bot como si fuera uno de la colección, para poder comparar contra otro."""
    url = str(ctx.config(MI_DIRECCION) or "http://127.0.0.1:8000").strip().rstrip("/")
    if not _URL_VALIDA.match(url):
        return ToolResult.err(
            f"la configuración '{MI_DIRECCION}' no es http://ip:puerto: {url!r}"
        )
    return {"nombre": "este Bot", "url": url}


def _flujos_de(ctx: ToolContext, bot: dict) -> dict | ToolResult:
    """
    {nombre: {content, folder, state, description}} de un Bot.

    Son N+1 requests porque `GET /workflows` no trae el contenido
    (`Workflow.to_dict(with_content=False)`), y sin contenido no hay con qué
    comparar dos flujos que se llaman igual.
    """
    try:
        respuesta, lista = _pedir(ctx, bot["url"], "/workflows")
    except PortError as exc:
        return ToolResult.err(f"'{bot['nombre']}' no responde en {bot['url']}: {exc}")
    if not respuesta.ok or not isinstance(lista, list):
        return ToolResult.err(f"'{bot['nombre']}' respondió {respuesta.status} a /workflows: ¿es un Bot?")

    flujos = {}
    for entrada in lista:
        nombre = (entrada or {}).get("name") if isinstance(entrada, dict) else None
        if not nombre:
            continue
        try:
            resp_uno, uno = _pedir(ctx, bot["url"], f"/workflows/{quote(str(nombre), safe='')}")
        except PortError as exc:
            return ToolResult.err(f"'{bot['nombre']}' dejó de responder leyendo '{nombre}': {exc}")
        if not resp_uno.ok or not isinstance(uno, dict):
            ctx.log(f"{bot['nombre']}: no se pudo leer el flujo '{nombre}' ({resp_uno.status})", level="warning")
            continue
        flujos[str(nombre)] = {campo: uno.get(campo) or "" for campo in _CAMPOS_FLUJO}
    return flujos


def _items_de(
    ctx: ToolContext, bot: dict, plugin: str, coleccion: str, *, exigir: bool,
) -> tuple[dict, list, str] | ToolResult:
    """
    ({clave: item}, campos secretos, campo clave) de una colección de un Bot.

    Los campos `secret` salen de la definición que la propia respuesta trae, no
    de una lista escrita acá: cualquier plugin instalado en el otro Bot los
    declara en su manifest y el endpoint los publica.

    `exigir` distingue los dos lados, y no es simetría mal hecha:

    - En el **origen** va True: pedir una colección que este Bot no tiene es,
      casi siempre, un nombre mal escrito, y además no hay nada que comparar
      *desde*. Vale más un error que un informe vacío que parece una respuesta.
    - En el **destino** va False: que el otro Bot no tenga el plugin es un
      resultado, no una falla —"no tiene ninguno de estos, todos habría que
      copiarlos"—, y es justo el caso de un Bot nuevo de la flota, que no tiene
      nada instalado todavía. Un tool que se cae porque allá falta un plugin
      rompe por lo que no está, que es lo que no tiene que pasar.
    """
    camino = f"/resources/{quote(plugin, safe='')}/{quote(coleccion, safe='')}"
    try:
        respuesta, datos = _pedir(ctx, bot["url"], camino)
    except PortError as exc:
        return ToolResult.err(f"'{bot['nombre']}' no responde en {bot['url']}: {exc}")
    if not respuesta.ok or not isinstance(datos, dict):
        detalle = datos.get("detail") if isinstance(datos, dict) else respuesta.text[:200]
        if exigir:
            return ToolResult.err(
                f"'{bot['nombre']}' respondió {respuesta.status} a {camino}: {detalle or 'sin detalle'}. "
                f"¿Tiene instalado el plugin '{plugin}' con la colección '{coleccion}'?"
            )
        ctx.log(
            f"{bot['nombre']} no tiene la colección '{plugin}/{coleccion}' "
            f"({respuesta.status}): todo lo de acá queda como 'sólo en el origen'",
            level="warning",
        )
        return {}, [], ""

    definicion = datos.get("resource") or {}
    campos = definicion.get("fields") or []
    secretos = [c.get("name") for c in campos if isinstance(c, dict) and c.get("secret")]
    clave = definicion.get("key_field") or "name"
    items = {}
    for item in datos.get("items") or []:
        if not isinstance(item, dict):
            continue
        valor_clave = item.get(clave)
        if valor_clave in (None, ""):
            continue
        items[str(valor_clave)] = {
            k: v for k, v in item.items() if k not in _CAMPOS_DEL_NUCLEO and k != clave
        }
    return items, [s for s in secretos if s], str(clave)


def _diferencias(origen: dict, destino: dict, secretos: list, detalle: bool) -> tuple[list, dict]:
    """
    El diff entre dos mapas {clave: {campo: valor}}: (items, resumen).

    Es la única función que decide qué significa "distinto", a propósito: si el
    cálculo se muda a un endpoint de la app —para que la pantalla de migración y
    este tool no tengan dos implementaciones que divergen— se reemplaza esto y
    el resto del tool queda igual.

    `indeterminado` es el estado de un item cuyos campos visibles son todos
    iguales pero cuya colección declara campos `secret`: no salen por la API, así
    que podría diferir justo ahí. Decir `igual` sería afirmar algo que no se
    puede ver.
    """
    items = []
    for clave in sorted(set(origen) | set(destino)):
        aca, alla = origen.get(clave), destino.get(clave)
        if alla is None:
            items.append({"clave": clave, "estado": "solo_origen", "campos": []})
            continue
        if aca is None:
            items.append({"clave": clave, "estado": "solo_destino", "campos": []})
            continue

        distintos = sorted(
            campo for campo in set(aca) | set(alla)
            if campo not in secretos and aca.get(campo) != alla.get(campo)
        )
        if distintos:
            entrada = {"clave": clave, "estado": "distinto", "campos": distintos}
            if detalle:
                entrada["origen"] = {c: aca.get(c) for c in distintos}
                entrada["destino"] = {c: alla.get(c) for c in distintos}
            items.append(entrada)
        else:
            items.append({
                "clave": clave,
                "estado": "indeterminado" if secretos else "igual",
                "campos": [],
            })

    resumen = {estado: 0 for estado in ("igual", "distinto", "solo_origen", "solo_destino", "indeterminado")}
    for item in items:
        resumen[item["estado"]] += 1
    return items, resumen


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
    params=(Param("bot", required=True, options_from="bots", doc="Nombre en Bots conocidos."),),
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
    params=(Param("bots", required=True, options_from="bots", doc="Nombres separados por coma, ej. 'Impresión 2, Impresión 3'."),),
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
        Param("bot", required=True, options_from="bots", doc="Nombre en Bots conocidos."),
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
        Param("bot", required=True, options_from="bots"),
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


# ── bots.comparar ─────────────────────────────────────────────────────────

COMPARAR = ToolManifest(
    id="bots.comparar",
    label="comparar contenido con otro Bot",
    category="BOTS",
    doc=(
        "Qué tiene distinto otro Bot: sus flujos, o los items de una colección "
        "de un plugin. Sólo lee, no copia nada. Cada clave queda en uno de "
        "cinco estados — igual, distinto (con qué campos), solo_origen, "
        "solo_destino, o indeterminado (los campos visibles coinciden pero la "
        "colección tiene campos secretos, que no salen por la API: podría "
        "diferir justo ahí). 'origen' vacío es este Bot. Con 'detalle', cada "
        "item distinto trae además los valores de los dos lados — puede ser "
        "mucho texto si son flujos."
    ),
    params=(
        Param("destino", required=True, options_from="bots", doc="Nombre en Bots conocidos: contra quién comparar."),
        Param("origen", default="", options_from="bots", doc="Vacío: este Bot (ver la configuración 'Dirección de este Bot')."),
        Param("que", ParamType.ENUM, default="flujos", choices=("flujos", "registros"), doc="'flujos' o 'registros' (los items de una colección)."),
        Param("plugin", default="", doc="Sólo con que=registros: de qué plugin es la colección, ej. 'convertidor'."),
        Param("coleccion", default="", doc="Sólo con que=registros: qué colección, ej. 'plantillas'."),
        Param("detalle", ParamType.BOOL, default=False, doc="Agregar a cada item distinto los valores de origen y destino."),
    ),
    outputs=(
        Output("diff", ParamType.JSON, doc="El informe completo: {origen, destino, que, coleccion, campos_comparados, campos_secretos, items, resumen}."),
        Output("hay_diferencias", ParamType.STR, doc="'si' o 'no', para ramificar."),
        Output("distintos", ParamType.JSON, doc="Claves que existen en los dos pero difieren."),
        Output("solo_origen", ParamType.JSON, doc="Claves que están sólo en el origen (las que habría que copiar)."),
        Output("solo_destino", ParamType.JSON, doc="Claves que están sólo en el destino."),
        Output("iguales", ParamType.JSON),
        Output("indeterminados", ParamType.JSON, doc="Claves que no se pueden comparar del todo por tener campos secretos."),
    ),
)


def _comparar(ctx: ToolContext) -> ToolResult:
    destino = _bot(ctx, ctx.params["destino"])
    if isinstance(destino, ToolResult):
        return destino
    nombre_origen = str(ctx.params["origen"]).strip()
    origen = _bot(ctx, nombre_origen) if nombre_origen else _este_bot(ctx)
    if isinstance(origen, ToolResult):
        return origen
    if origen["url"] == destino["url"]:
        return ToolResult.err(f"'{origen['nombre']}' y '{destino['nombre']}' son el mismo Bot ({origen['url']})")

    que = ctx.params["que"]
    plugin = str(ctx.params["plugin"]).strip()
    coleccion = str(ctx.params["coleccion"]).strip()
    secretos: list = []
    destino_sin_coleccion = False

    if que == "flujos":
        datos_origen = _flujos_de(ctx, origen)
        if isinstance(datos_origen, ToolResult):
            return datos_origen
        datos_destino = _flujos_de(ctx, destino)
        if isinstance(datos_destino, ToolResult):
            return datos_destino
        comparados = list(_CAMPOS_FLUJO)
    else:
        if not plugin or not coleccion:
            return ToolResult.err("con que=registros hacen falta 'plugin' y 'coleccion'")
        lectura_origen = _items_de(ctx, origen, plugin, coleccion, exigir=True)
        if isinstance(lectura_origen, ToolResult):
            return lectura_origen
        lectura_destino = _items_de(ctx, destino, plugin, coleccion, exigir=False)
        if isinstance(lectura_destino, ToolResult):
            return lectura_destino
        datos_origen, secretos, _ = lectura_origen
        datos_destino, secretos_destino, clave_destino = lectura_destino
        # Sin campo clave: el destino no tiene la colección (ver `_items_de`).
        destino_sin_coleccion = not clave_destino
        # La unión: si un lado declara un campo secreto que el otro no, igual no
        # se puede comparar. Pasa con dos versiones distintas del mismo plugin.
        secretos = sorted(set(secretos) | set(secretos_destino))
        comparados = sorted(
            {c for item in (*datos_origen.values(), *datos_destino.values()) for c in item} - set(secretos)
        )

    items, resumen = _diferencias(datos_origen, datos_destino, secretos, bool(ctx.params["detalle"]))
    por_estado = {
        estado: [i["clave"] for i in items if i["estado"] == estado]
        for estado in ("igual", "distinto", "solo_origen", "solo_destino", "indeterminado")
    }

    diff = {
        "origen": {"bot": origen["nombre"], "url": origen["url"]},
        "destino": {"bot": destino["nombre"], "url": destino["url"]},
        "que": que,
        "coleccion": f"{plugin}/{coleccion}" if que == "registros" else "",
        # Siempre presente, como `campos_secretos`: quien lo renderiza no tiene
        # que distinguir "no pasó" de "no me lo dijeron". True = el destino no
        # tiene ese plugin/colección, así que el vacío de allá no significa
        # "colección vacía" sino "ni siquiera la tiene".
        "destino_sin_coleccion": destino_sin_coleccion,
        "campos_comparados": comparados,
        "campos_secretos": secretos,
        "items": items,
        "resumen": resumen,
    }
    hay = resumen["distinto"] + resumen["solo_origen"] + resumen["solo_destino"]
    ctx.log(
        f"{origen['nombre']} vs {destino['nombre']} ({que}): {resumen['igual']} iguales, "
        f"{resumen['distinto']} distintos, {resumen['solo_origen']} sólo acá, "
        f"{resumen['solo_destino']} sólo allá, {resumen['indeterminado']} indeterminados"
    )
    return ToolResult.ok(
        diff=diff,
        hay_diferencias="si" if hay else "no",
        distintos=por_estado["distinto"],
        solo_origen=por_estado["solo_origen"],
        solo_destino=por_estado["solo_destino"],
        iguales=por_estado["igual"],
        indeterminados=por_estado["indeterminado"],
    )


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
            FunctionTool(manifest=COMPARAR, fn=_comparar),
        ],
        actions=[FunctionAction(action=MANIFEST.action("probar"), fn=_probar)],
    )


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
