"""
Tests del plugin `model-arranger`, sin taller ni red: `Taller` es un fake del
port `http` que imita el contrato 1.x de `taller_server.py` (model-arranger,
commit 7592a90): tareas con `tarea_id` que tardan N consultas en terminar,
409 con `ocupado` cuando hay otra corriendo y 400 con `{error}`. El reloj es
el `FakeClock` del núcleo, así que esperar una tarea no duerme.

Necesita el núcleo (`backend/`, >= v0.4.0-beta.1) del repo de la app: se
busca en la variable `WORKFLOW_BOT_APP` o en `../workflow-bot-app`, al lado
de este catálogo. Corre con `python -m pytest model_arranger` desde la raíz.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from urllib.parse import parse_qs, urlparse

CATALOGO = pathlib.Path(__file__).resolve().parents[2]
APP = pathlib.Path(os.environ.get("WORKFLOW_BOT_APP") or CATALOGO.parent / "workflow-bot-app")
sys.path.insert(0, str(CATALOGO))
sys.path.insert(0, str(APP))

from backend.core.contract import ToolContext  # noqa: E402
from backend.core.ports import HttpResponse, PortError  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeClock  # noqa: E402

from model_arranger.plugin import MANIFEST, PLUGIN, _fuentes, build_plugin  # noqa: E402

BASE = "http://127.0.0.1:8780"
BOT = "http://127.0.0.1:8000/api/core"


def _json(datos, status=200):
    return HttpResponse(status=status, text=json.dumps(datos), headers={"content-type": "application/json"})


class Taller:
    """El taller de model-arranger, en memoria y con lo justo de su API."""

    def __init__(self, *, version="1.0", vueltas=2, ocupado_por=None, caido=False):
        self.version = version
        self.vueltas = vueltas  # consultas a /api/tarea hasta que una tarea termina
        self.caido = caido
        self.calls: list[dict] = []
        self.tareas: dict[int, dict] = {}
        self.ordenes = {1: {"id": 1, "codigo": "O-0001", "id_externo": "AB123", "estado": "recibida"}}
        self.nests = {}
        self.impresoras = [{"id": 1, "nombre": "Form 4"}]
        self.fuentes: dict[str, dict] = {}
        self.bot_status = 200
        if ocupado_por:
            self.tareas[ocupado_por] = {"tarea": "anidar", "faltan": vueltas, "resultado": None, "error": ""}

    # ── la red ──

    def request(self, url, *, method="GET", headers=None, body=None, timeout=30.0):
        self.calls.append({"url": url, "method": method, "headers": dict(headers or {}), "body": body})
        if url.startswith(BOT):
            return self._bot(url, method, body)
        if self.caido:
            raise PortError("connection refused")
        u = urlparse(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        b = json.loads(body) if body else {}
        try:
            return self._api(method, u.path, q, b)
        except ValueError as e:
            return _json({"error": str(e)}, 400)

    def _bot(self, url, method, body):
        nombre = url.rsplit("/", 1)[1]
        if method == "GET":
            return _json({}, 200) if nombre in self.fuentes else _json({"detail": "no"}, 404)
        if self.bot_status != 200:
            return _json({"detail": "rechazado"}, self.bot_status)
        self.fuentes[nombre] = json.loads(body)["item"]
        return _json({"ok": True})

    # ── tareas ──

    def _corriendo(self):
        return next((i for i, t in self.tareas.items() if t["faltan"] > 0), None)

    def _tarea(self, nombre, resultado=None, error="", al_terminar=None):
        if (otra := self._corriendo()) is not None:
            return None, _json({"error": f"ya hay una tarea corriendo (id {otra})", "ocupado": True, "tarea_id": otra}, 409)
        tid = max(self.tareas, default=0) + 1
        self.tareas[tid] = {"tarea": nombre, "faltan": self.vueltas, "resultado": resultado, "error": error,
                            "al_terminar": al_terminar}
        return tid, None

    def _estado(self, tid):
        t = self.tareas[tid]
        return {"tarea": t["tarea"], "tarea_id": tid, "etapa": "trabajando" if t["faltan"] else "listo",
                "hecho": 0, "total": 1, "corriendo": t["faltan"] > 0, "error": "" if t["faltan"] else t["error"],
                "resultado": None if t["faltan"] else t["resultado"]}

    # ── la API ──

    def _api(self, method, path, q, b):
        if method == "GET":
            if path == "/api/version":
                return _json({"version": self.version, "nombre": "model-arranger taller"})
            if path == "/api/progreso":
                tid = self._corriendo()
                return _json(self._estado(tid) if tid else {"tarea": "", "tarea_id": 0, "corriendo": False})
            if path == "/api/tarea":
                tid = int(q["id"])
                if tid not in self.tareas:
                    raise ValueError("tarea desconocida (o ya salio del historial)")
                t = self.tareas[tid]
                if t["faltan"]:
                    t["faltan"] -= 1
                    if not t["faltan"] and t.get("al_terminar"):
                        t["al_terminar"]()
                return _json(self._estado(tid))
            if path == "/api/resumen":
                return _json({"ordenes": [{"estado": "lista", "n": 3}, {"estado": "con_falla", "n": 1}],
                              "piezas": [{"estado": "salud_ok", "n": 40}], "nests": []})
            if path == "/api/orden":
                o = self.ordenes.get(int(q["id"]))
                if not o:
                    raise ValueError("orden inexistente")
                return _json({"orden": o, "cuentas": {"salud_ok": 2, "total": 2}, "nests": [], "eventos": []})
            if path == "/api/pieza":
                return _json({"id": int(q["id"]), "codigo": f"P-{int(q['id']):06d}", "estado": "salud_ok", "salud": []})
            if path == "/api/impresoras":
                return _json(self.impresoras)
            if path == "/api/nest":
                n = self.nests.get(int(q["id"]))
                if not n:
                    raise ValueError("nest inexistente")
                return _json({"nest": n, "cuentas": {}, "ordenes": [], "eventos": []})
        if path == "/api/ordenes":
            if b.get("solo_ver"):
                return _json({"ordenes": [{"id_externo": "AB123", "archivos": ["AB123-L01-A.stl"]}], "fuera": ["gum.stl"]})
            if b["carpeta"] == "/vacia":
                return _json({"ids": [], "tarea_id": None})
            tid, ocupado = self._tarea("validar", al_terminar=lambda: self.ordenes[1].update(estado="lista"))
            return ocupado or _json({"ids": [1], "tarea_id": tid})
        if path == "/api/orden/validar":
            tid, ocupado = self._tarea("validar", al_terminar=lambda: self.ordenes[1].update(estado="con_falla"))
            return ocupado or _json({"ok": True, "tarea_id": tid})
        if path == "/api/orden/prioridad":
            self.ordenes[int(b["id"])]["prioridad"] = b["prioridad"]
            return _json({"ok": True})
        if path == "/api/nests":
            nid = len(self.nests) + 1
            self.nests[nid] = {"id": nid, "codigo": f"N-{nid:04d}", "estado": "abierto", "modo": b["modo"],
                               "impresora": "Form 4", "densidad": None, "stl": None}
            return _json({"id": nid})
        if path == "/api/nest/anidar":
            nid = int(b["id"])

            def termina():
                self.nests[nid].update(densidad=0.62)

            tid, ocupado = self._tarea("anidar", {"probadas": [{"orden_id": 1, "entro": True},
                                                               {"orden_id": 2, "entro": False}]}, al_terminar=termina)
            return ocupado or _json({"ok": True, "tarea_id": tid})
        if path == "/api/nest/cerrar":
            nid = int(b["id"])
            stl = f"camas/N-{nid:04d}.stl"
            tid, ocupado = self._tarea("cerrar", {"stl": stl},
                                       al_terminar=lambda: self.nests[nid].update(estado="cerrado", stl=stl))
            return ocupado or _json({"ok": True, "tarea_id": tid})
        if path == "/api/nest/falla":
            tid, ocupado = self._tarea("cerrar", error="sparrow se cayó")
            return ocupado or _json({"ok": True, "tarea_id": tid})
        raise ValueError("no existe")

    def posts(self, camino):
        return [c for c in self.calls if c["method"] == "POST" and urlparse(c["url"]).path == camino]


def _ctx(params, config=None, cancelado=lambda: False):
    def factory(declaracion, ports=None):
        if hasattr(declaracion, "split_params"):
            d, e = declaracion.split_params(params, {})
        else:
            d, e = {p.name: params.get(p.name, p.default) for p in declaracion.params}, {}
        return ToolContext(
            run_id="run-test", case_id="C1", params=d, extras=e, config=dict(config or {}), context={},
            log=lambda m, level="info": None, ports=ports or {}, is_cancelled=cancelado,
        )

    return factory


def _reg(taller, clock):
    reg = ToolRegistry(adapters={"http": taller, "clock": clock})
    reg._add_plugin("model-arranger", "model_arranger:PLUGIN", build_plugin())
    return reg


def _correr(tool, params, taller=None, config=None, clock=None, cancelado=lambda: False):
    taller = taller or Taller()
    clock = clock or FakeClock()
    return _reg(taller, clock).execute(f"model_arranger.{tool}", _ctx(params, config, cancelado)), taller, clock


# ── manifest ──────────────────────────────────────────────────────────────

def test_manifest_pide_solo_http_y_clock():
    assert MANIFEST.name == "model-arranger"
    assert set(MANIFEST.ports) == {"http", "clock"}
    assert MANIFEST.requires == ()


def test_los_tools_que_leen_corren_en_seco_y_los_que_escriben_no():
    por_id = {t.manifest.id: t.manifest for t in PLUGIN.tools}
    assert por_id["model_arranger.salud"].dry_run == "run"
    assert por_id["model_arranger.nest"].dry_run == "run"
    assert por_id["model_arranger.anidar"].dry_run != "run"
    assert por_id["model_arranger.ingresar"].dry_run != "run"


# ── salud ─────────────────────────────────────────────────────────────────

def test_salud_ok_con_version_y_cuentas():
    r, _, _ = _correr("salud", {})
    assert r.status == "ok", r.message
    assert r.outputs["version"] == "1.0"
    assert r.outputs["ocupado"] is False
    assert r.outputs["ordenes"] == {"lista": 3, "con_falla": 1}


def test_salud_dice_que_esta_ocupado():
    r, _, _ = _correr("salud", {}, Taller(ocupado_por=7))
    assert r.status == "ok"
    assert r.outputs["ocupado"] is True and r.outputs["tarea"] == "anidar"


def test_salud_err_si_el_taller_no_responde():
    r, _, _ = _correr("salud", {}, Taller(caido=True))
    assert r.status == "err"
    assert "no responde" in r.message and "taller_server.py" in r.message


def test_salud_err_si_la_api_es_de_otro_mayor():
    r, _, _ = _correr("salud", {}, Taller(version="2.0"))
    assert r.status == "err" and "2.0" in r.message


def test_token_va_como_bearer():
    _, taller, _ = _correr("salud", {}, config={"token": "s3cr3to"})
    assert taller.calls[0]["headers"]["Authorization"] == "Bearer s3cr3to"


def test_la_direccion_sale_de_config():
    taller = Taller()
    _correr("salud", {}, taller, config={"url": "http://taller:9000/"})
    # El fake contesta a cualquier host; lo que importa es a dónde se pidió.
    assert taller.calls[0]["url"] == "http://taller:9000/api/version"


# ── órdenes ───────────────────────────────────────────────────────────────

def test_ingresar_espera_la_validacion_y_devuelve_como_quedo():
    r, taller, clock = _correr("ingresar", {"carpeta": "/stl/lote1"})
    assert r.status == "ok", r.message
    assert r.outputs["ids"] == [1]
    assert r.outputs["todas_listas"] is True
    assert r.outputs["ordenes"][0]["codigo"] == "O-0001"
    assert json.loads(taller.posts("/api/ordenes")[0]["body"])["carpeta"] == "/stl/lote1"
    assert clock.slept == [3.0]  # dos consultas: corriendo, terminada


def test_ingresar_sin_esperar_sale_con_el_tarea_id():
    r, _, clock = _correr("ingresar", {"carpeta": "/stl/lote1", "esperar": False})
    assert r.status == "ok"
    assert r.outputs["tarea_id"] == 1 and "ordenes" not in r.outputs
    assert clock.slept == []


def test_ingresar_una_carpeta_sin_stl_es_err():
    r, _, _ = _correr("ingresar", {"carpeta": "/vacia"})
    assert r.status == "err" and "patrón" in r.message


def test_validar_acepta_el_codigo_y_marca_las_que_fallan():
    r, taller, _ = _correr("validar", {"orden": "O-0001"})
    assert r.status == "ok"
    assert json.loads(taller.posts("/api/orden/validar")[0]["body"]) == {"id": 1}
    assert r.outputs["todas_listas"] is False and r.outputs["con_falla"] == ["O-0001"]


def test_un_codigo_de_otra_cosa_es_err_sin_llamar_al_taller():
    r, taller, _ = _correr("validar", {"orden": "N-0001"})
    assert r.status == "err" and "orden" in r.message
    assert taller.calls == []


def test_un_400_del_taller_sale_con_su_mensaje():
    r, _, _ = _correr("orden", {"orden": 99})
    assert r.status == "err" and "orden inexistente" in r.message


def test_ver_carpeta_no_crea_nada():
    r, taller, _ = _correr("ver_carpeta", {"carpeta": "/stl/lote1"})
    assert r.status == "ok" and r.outputs["cantidad"] == 1 and r.outputs["fuera"] == ["gum.stl"]
    assert json.loads(taller.posts("/api/ordenes")[0]["body"])["solo_ver"] is True
    assert taller.tareas == {}


def test_prioridad():
    r, taller, _ = _correr("prioridad", {"orden": "1", "prioridad": 5})
    assert r.status == "ok" and taller.ordenes[1]["prioridad"] == 5


# ── nests ─────────────────────────────────────────────────────────────────

def test_abrir_nest_por_nombre_de_impresora():
    r, taller, _ = _correr("abrir_nest", {"impresora": "form 4", "modo": "unico"})
    assert r.status == "ok", r.message
    assert r.outputs["nest_id"] == 1 and r.outputs["estado"] == "abierto"
    assert json.loads(taller.posts("/api/nests")[0]["body"]) == {"impresora_id": 1, "modo": "unico"}


def test_abrir_nest_con_impresora_que_no_existe():
    r, _, _ = _correr("abrir_nest", {"impresora": "Ender"})
    assert r.status == "err" and "Form 4" in r.message


def _con_nest():
    taller = Taller()
    taller.nests[1] = {"id": 1, "codigo": "N-0001", "estado": "abierto", "densidad": None, "stl": None}
    return taller


def test_anidar_espera_y_dice_que_entro():
    r, _, _ = _correr("anidar", {"nest": "N-0001"}, _con_nest())
    assert r.status == "ok", r.message
    assert r.outputs["entraron"] == [1]
    assert r.outputs["densidad"] == 0.62


def test_cerrar_nest_devuelve_el_stl():
    r, taller, _ = _correr("cerrar_nest", {"nest": 1}, _con_nest())
    assert r.status == "ok"
    assert r.outputs["stl"] == "camas/N-0001.stl" and r.outputs["estado"] == "cerrado"


def test_ocupado_espera_la_otra_tarea_y_reintenta():
    taller = _con_nest()
    taller.tareas[9] = {"tarea": "validar", "faltan": 2, "resultado": None, "error": ""}
    r, taller, _ = _correr("anidar", {"nest": 1}, taller)
    assert r.status == "ok", r.message
    assert len(taller.posts("/api/nest/anidar")) == 2
    consultadas = [parse_qs(urlparse(c["url"]).query).get("id") for c in taller.calls if "/api/tarea" in c["url"]]
    assert ["9"] in consultadas


def test_una_tarea_que_falla_en_el_taller_es_err():
    taller = _con_nest()
    tid, _ = taller._tarea("cerrar", error="sparrow se cayó")
    r, _, _ = _correr("esperar_tarea", {"tarea_id": tid}, taller)
    assert r.status == "err" and "sparrow se cayó" in r.message


def test_esperar_tiene_tope():
    taller = Taller(vueltas=10_000)
    r, _, clock = _correr("ingresar", {"carpeta": "/stl"}, taller, config={"espera_max": 1, "cada": 10})
    assert r.status == "err" and "sigue corriendo" in r.message
    assert clock.total_slept >= 60


def test_cancelar_corta_la_espera():
    r, _, clock = _correr("ingresar", {"carpeta": "/stl"}, Taller(vueltas=50), cancelado=lambda: True)
    assert r.status == "err" and "cancelada" in r.message
    assert clock.slept == []


# ── acciones de la pantalla ───────────────────────────────────────────────

def _accion(nombre, params, taller):
    return _reg(taller, FakeClock()).execute_action("model-arranger", nombre, _ctx(params))


def test_probar_es_la_misma_salud():
    r = _accion("probar", {}, Taller())
    assert r.status == "ok" and r.outputs["version"] == "1.0"


def test_crear_fuentes_en_connections():
    taller = Taller()
    r = _accion("crear_fuentes", {}, taller)
    assert r.status == "ok", r.message
    assert len(taller.fuentes) == 4
    piezas = taller.fuentes["Model%20Arranger%20-%20piezas"]
    assert piezas["url"] == f"{BASE}/api/piezas"
    assert piezas["results_path"] == "filas" and piezas["key_field"] == "codigo"
    assert piezas["page_param"] == "pagina" and piezas["total_path"] == "total"
    assert "matriz" in piezas["columnas_ocultas"]


def test_crear_fuentes_no_pisa_las_que_existen():
    taller = Taller()
    taller.fuentes["Model%20Arranger%20-%20nests"] = {"url": "a mano"}
    r = _accion("crear_fuentes", {}, taller)
    assert r.status == "ok" and r.outputs["salteadas"] == ["Model Arranger - nests"]
    assert taller.fuentes["Model%20Arranger%20-%20nests"] == {"url": "a mano"}


def test_crear_fuentes_err_si_el_bot_rechaza():
    taller = Taller()
    taller.bot_status = 422
    r = _accion("crear_fuentes", {}, taller)
    assert r.status == "err" and "422" in r.message


def test_las_fuentes_usan_la_direccion_de_config():
    assert _fuentes("http://taller:9000")[0]["url"] == "http://taller:9000/api/ordenes"


# ── el taller se reinició y perdió el historial de tareas ─────────────────

def _que_pierde_las_tareas(taller):
    """El taller se reinicia justo después de aceptar cada tarea: /api/tarea ya no la conoce."""
    original = taller._tarea

    def perder(nombre, resultado=None, error="", al_terminar=None):
        tid, ocupado = original(nombre, resultado, error, al_terminar=None)
        if tid:
            del taller.tareas[tid]
        return tid, ocupado

    taller._tarea = perder
    return taller


def test_tarea_perdida_en_validar_relee_la_orden():
    taller = _que_pierde_las_tareas(Taller())
    taller.ordenes[1]["estado"] = "con_falla"
    r, taller, _ = _correr("validar", {"orden": 1}, taller)
    assert r.status == "ok", r.message
    assert r.outputs["con_falla"] == ["O-0001"]  # lo que dice la orden releída, no un resultado inventado
    assert any("/api/orden?" in c["url"] for c in taller.calls)


def test_esperar_tarea_perdida_es_err_claro():
    r, _, _ = _correr("esperar_tarea", {"tarea_id": 42})
    assert r.status == "err" and "ya no conoce" in r.message


def test_ocupado_por_una_tarea_perdida_reintenta_igual():
    taller = _con_nest()


    # 409 apuntando a una tarea que /api/tarea no conoce (el taller se reinició entre medio).
    respuestas = iter([_json({"error": "ocupado", "ocupado": True, "tarea_id": 77}, 409)])
    original = taller._api

    def api(method, path, q, b):
        if method == "POST" and path == "/api/nest/anidar":
            siguiente = next(respuestas, None)
            if siguiente is not None:
                return siguiente
        return original(method, path, q, b)

    taller._api = api
    r, taller, _ = _correr("anidar", {"nest": 1}, taller)
    assert r.status == "ok", r.message
    assert len(taller.posts("/api/nest/anidar")) == 2


def test_cerrar_que_no_termino_es_err():
    r, _, _ = _correr("cerrar_nest", {"nest": 1}, _que_pierde_las_tareas(_con_nest()))
    assert r.status == "err" and "sigue abierto" in r.message


def test_anidar_con_tarea_perdida_no_inventa_cuales_entraron():
    r, _, _ = _correr("anidar", {"nest": 1}, _que_pierde_las_tareas(_con_nest()))
    assert r.status == "ok"
    assert r.outputs["entraron"] == [] and "no se sabe" in r.message
