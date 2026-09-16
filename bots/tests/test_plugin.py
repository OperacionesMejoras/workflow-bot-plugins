"""
Tests del plugin `bots`, sin red ni tiempo real: el otro Bot es un `FakeHttp`
guionado con las respuestas de su API, y el reloj es un `FakeClock`.

Necesita el núcleo (`backend/`) del repo de la app: se busca en la variable
`WORKFLOW_BOT_APP` o en `../workflow-bot-app`, al lado de este catálogo.
Corre con `python -m pytest bots` desde la raíz del catálogo.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

CATALOGO = pathlib.Path(__file__).resolve().parents[2]
APP = pathlib.Path(os.environ.get("WORKFLOW_BOT_APP") or CATALOGO.parent / "workflow-bot-app")
sys.path.insert(0, str(CATALOGO))
sys.path.insert(0, str(APP))

from backend.core.contract import ToolContext  # noqa: E402
from backend.core.ports import HttpResponse, PortError  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeClock, FakeHttp  # noqa: E402

from bots.plugin import build_plugin  # noqa: E402

BOTS = [
    {"nombre": "Impresión 2", "url": "http://192.168.9.41:8000", "nota": ""},
    {"nombre": "Impresión 3", "url": "http://192.168.9.42:8000/", "nota": ""},
    {"nombre": "Roto", "url": "ftp://nada", "nota": ""},
]
API2 = "http://192.168.9.41:8000/api/core"
API3 = "http://192.168.9.42:8000/api/core"


def _json(datos, status=200):
    return HttpResponse(status=status, text=json.dumps(datos), headers={"content-type": "application/json"})


def _correr(tool, params, http, clock=None, case_id="PADRE-1"):
    reg = ToolRegistry(adapters={"http": http, "clock": clock or FakeClock()})
    plugin = build_plugin()
    reg._add_plugin("bots", "bots:PLUGIN", plugin)
    registro = []

    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params(params, {})
        return ToolContext(
            run_id="run-test", case_id=case_id, params=declarados, extras=extras,
            config={}, context={}, log=lambda m, level="info": registro.append(m),
            ports=ports or {}, resources=lambda coleccion: BOTS if coleccion == "bots" else [],
        )

    return reg.execute(tool, factory), registro


def test_estado_dice_cuantos_corren_si_esta_libre_y_como_termino_el_ultimo():
    http = FakeHttp({
        f"{API2}/runs/en-vuelo": _json([{"case_id": "X", "hechos": 3, "total": 10, "paso": "Exportar"}]),
        f"{API2}/runs?limit=1": _json([{"status": "ok", "flow": "v3", "case_id": "AB123"}]),
    })
    r, _ = _correr("bots.estado", {"bot": "Impresión 2"}, http)
    assert r.status == "ok"
    assert (r.outputs["corriendo"], r.outputs["libre"]) == (1, "no")
    assert (r.outputs["ultimo_estado"], r.outputs["ultimo_flujo"], r.outputs["ultimo_caso"]) == ("ok", "v3", "AB123")


def test_un_bot_desconocido_o_con_direccion_rara_es_err_antes_de_ir_a_la_red():
    http = FakeHttp()
    r, _ = _correr("bots.estado", {"bot": "Ninguno"}, http)
    assert r.status == "err" and "Conocidos: Impresión 2, Impresión 3, Roto" in r.message
    r, _ = _correr("bots.estado", {"bot": "Roto"}, http)
    assert r.status == "err" and "http://ip:puerto" in r.message
    assert http.calls == []


def test_si_no_responde_es_err_con_la_direccion():
    http = FakeHttp({f"{API2}/runs/en-vuelo": PortError("connection refused")})
    r, _ = _correr("bots.estado", {"bot": "Impresión 2"}, http)
    assert r.status == "err" and "192.168.9.41" in r.message


def test_elegir_libre_toma_el_que_menos_tiene_y_saltea_al_caido():
    http = FakeHttp({
        f"{API2}/runs/en-vuelo": _json([{"case_id": "A"}, {"case_id": "B"}]),
        f"{API2}/runs?limit=1": _json([]),
        f"{API3}/runs/en-vuelo": _json([]),
        f"{API3}/runs?limit=1": _json([]),
    })
    r, _ = _correr("bots.elegir_libre", {"bots": "Impresión 2, Impresión 3"}, http)
    assert r.status == "ok" and r.outputs["bot"] == "Impresión 3" and r.outputs["corriendo"] == 0

    http = FakeHttp({
        f"{API2}/runs/en-vuelo": PortError("timeout"),
        f"{API3}/runs/en-vuelo": _json([{"case_id": "A"}]),
        f"{API3}/runs?limit=1": _json([]),
    })
    r, _ = _correr("bots.elegir_libre", {"bots": "Impresión 2, Impresión 3"}, http)
    assert r.status == "ok" and r.outputs["bot"] == "Impresión 3" and r.outputs["caidos"] == ["Impresión 2"]

    http = FakeHttp({f"{API2}/runs/en-vuelo": PortError("x"), f"{API3}/runs/en-vuelo": PortError("y")})
    r, _ = _correr("bots.elegir_libre", {"bots": "Impresión 2, Impresión 3"}, http)
    assert r.status == "err" and "ningún Bot respondió" in r.message


def test_correr_pide_sin_esperar_y_devuelve_el_ticket():
    http = FakeHttp({f"{API2}/runs": _json({"ticket": "abc123def456", "estado": "en_cola"})})
    r, _ = _correr("bots.correr", {"bot": "Impresión 2", "flujo": "TOOTHFORM CNC4 V3", "case_id": "AB123"}, http)
    assert r.status == "ok" and r.outputs["ticket"] == "abc123def456"
    [llamada] = http.calls
    assert llamada["method"] == "POST" and llamada["url"] == f"{API2}/runs"
    cuerpo = json.loads(llamada["body"])
    # Sin row, el hijo recibe lo que un flujo que empieza por "refrescar row" necesita.
    assert cuerpo["row"] == {"id_externo": "AB123"} and cuerpo["flow"] == "TOOTHFORM CNC4 V3"
    assert cuerpo["source"] == "bot:PADRE-1"


def test_correr_con_flujo_inexistente_en_el_hijo_es_err_con_su_mensaje():
    http = FakeHttp({f"{API2}/runs": _json({"detail": 'No existe el flujo "nada"'}, status=404)})
    r, _ = _correr("bots.correr", {"bot": "Impresión 2", "flujo": "nada", "case_id": "1"}, http)
    assert r.status == "err" and "404" in r.message and "No existe el flujo" in r.message


def test_esperar_sondea_anota_el_paso_del_hijo_y_termina_con_su_resultado():
    class Hijo:
        """Un FakeHttp que cambia de respuesta en cada consulta al ticket."""

        def __init__(self):
            self.respuestas = iter([
                {"estado": "en_cola", "vivo": {"hechos": 0, "total": 10, "paso": "", "en_cola": True}},
                {"estado": "en_vuelo", "vivo": {"hechos": 3, "total": 10, "paso": "Exportar"}},
                {"estado": "en_vuelo", "vivo": {"hechos": 3, "total": 10, "paso": "Exportar"}},
                {"estado": "terminado", "run": {"status": "ok", "run_id": "run-77", "message": ""}},
            ])
            self.calls = []

        def request(self, url, **kw):
            self.calls.append(url)
            return _json(next(self.respuestas))

    reloj = FakeClock()
    r, log = _correr("bots.esperar", {"bot": "Impresión 2", "ticket": "t1", "cada": 5}, Hijo(), reloj)
    assert r.status == "ok"
    assert (r.outputs["estado_hijo"], r.outputs["run_id"]) == ("ok", "run-77")
    assert reloj.slept == [5, 5, 5]
    # El paso se anota cuando cambia, no en cada sondeo.
    assert [l for l in log if "Impresión 2:" in l] == ["Impresión 2: 0/10 · en cola", "Impresión 2: 3/10 · Exportar"]


def test_esperar_err_si_el_hijo_termina_err_o_si_vence_el_timeout():
    http = FakeHttp({f"{API2}/runs/ticket/t2": _json({"estado": "terminado", "run": {"status": "err", "run_id": "run-8", "message": "Faltan archivos"}})})
    r, _ = _correr("bots.esperar", {"bot": "Impresión 2", "ticket": "t2"}, http)
    assert r.status == "err" and "Faltan archivos" in r.message and r.outputs["estado_hijo"] == "err"

    http = FakeHttp({f"{API2}/runs/ticket/t3": _json({"estado": "en_vuelo", "vivo": {"hechos": 1, "total": 4, "paso": "x"}})})
    r, _ = _correr("bots.esperar", {"bot": "Impresión 2", "ticket": "t3", "timeout": 12, "cada": 5}, http, FakeClock())
    assert r.status == "err" and "sigue corriendo" in r.message and r.outputs["estado_hijo"] == "en_vuelo"

    http = FakeHttp({f"{API2}/runs/ticket/t4": _json({"estado": "desconocido"})})
    r, _ = _correr("bots.esperar", {"bot": "Impresión 2", "ticket": "t4"}, http)
    assert r.status == "err" and "no conoce el ticket" in r.message


def test_la_accion_probar_resume_el_estado():
    http = FakeHttp({f"{API2}/runs/en-vuelo": _json([]), f"{API2}/runs?limit=1": _json([{"status": "ok"}])})
    reg = ToolRegistry(adapters={"http": http, "clock": FakeClock()})
    reg._add_plugin("bots", "bots:PLUGIN", build_plugin())

    def factory(accion, ports=None):
        return ToolContext(
            run_id="", case_id="", params={"nombre": "Impresión 2"}, extras={}, config={}, context={},
            log=lambda *a, **k: None, ports=ports or {}, resources=lambda c: BOTS,
        )

    r = reg.execute_action("bots", "probar", factory)
    assert r.status == "ok" and r.message == "responde · 0 en vuelo · último run ok"


def test_correr_acepta_row_como_texto_interpolado_por_el_nucleo():
    """`row={variable}` llega como str(dict) de Python, con comillas simples: no es JSON, y también sirve."""
    http = FakeHttp({f"{API2}/runs": _json({"ticket": "t9", "estado": "en_cola"})})
    fila = "{'id_externo': 'AB123', 'filesFolder': 'C:\\casos\\AB123\\stl'}"
    r, _ = _correr("bots.correr", {"bot": "Impresión 2", "flujo": "f", "case_id": "AB123", "row": fila}, http)
    assert r.status == "ok"
    assert json.loads(http.calls[0]["body"])["row"] == {"id_externo": "AB123", "filesFolder": "C:\casos\AB123\stl"}
    r, _ = _correr("bots.correr", {"bot": "Impresión 2", "flujo": "f", "case_id": "1", "row": "no es un objeto"}, FakeHttp())
    assert r.status == "err" and "objeto JSON" in r.message
