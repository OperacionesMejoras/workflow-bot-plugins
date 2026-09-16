"""
Tests del plugin `convertidor`.

`corregir_puntos` necesita 'trimesh' de verdad para el camino feliz (no tiene
sentido fakearlo: la geometría es el punto) — esos tests se saltan con
`pytest.importorskip` si no está instalado en el entorno de quien corre los
tests. El camino de error (trimesh ausente) se prueba aparte, ocultando el
import aunque trimesh esté instalado.

Corre con `python -m pytest plugins/convertidor`.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from backend.core.contract import ToolContext  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeFs  # noqa: E402

from plugins.convertidor.plugin import build_plugin  # noqa: E402

# Los mismos dos patrones que traía `patterns.py` del Convertidor original.
PATRONES_TOOTHFORM = {
    "standard": r"(?P<id>\S+)\s+(?P<patient>.+?)\s+(?P<stage>\d+\.\d+)\s+(?P<maxilla>Inf|Sup)\s+(?P<movement>\d+)",
    "medium": r"(?P<id>\S+)\s+(?P<patient>.+?)\s+(?P<stage>[A-Za-z]\.?\d+(\.\d+)?)\s+(?P<maxilla>Inf|Sup)\s+(?P<movement>\d+)",
}

# El nombre legacy del stem que usaba `archivos.buscar` como ejemplo.
PATRONES_LEGACY = {
    "legacy": r"(?P<id>[A-Z]{2}\d{3})-(?P<maxilla>[LU])(?P<movement>\d{2})-(?P<type>[A-Z])$",
}


def _registry(adapters=None):
    # El plugin declara el port fs (lo usa 'reescribir_archivo'): sin un
    # adapter atado, el registry rechaza el plugin entero al cargarlo, aunque
    # el tool que se vaya a correr no lo toque.
    reg = ToolRegistry(adapters={"fs": FakeFs(), **(adapters or {})})
    reg._add_plugin("convertidor", "plugins.convertidor:PLUGIN", build_plugin())
    return reg


def _factory(node_params, plantillas=()):
    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params(node_params, {})
        return ToolContext(
            run_id="run-test", case_id="AP962", params=declarados, extras=extras,
            config={}, context={}, log=lambda *_a, **_k: None, ports=ports or {},
            resources=lambda coleccion: list(plantillas) if coleccion == "plantillas" else [],
        )

    return factory


def _parsear(plantillas=(), **params):
    return _registry().execute("convertidor.parsear_nombre", _factory(params, plantillas))


def _generar(plantillas=(), **params):
    return _registry().execute("convertidor.generar_nombre", _factory(params, plantillas))


def _reescribir(fs, plantillas=(), **params):
    return _registry(adapters={"fs": fs}).execute("convertidor.reescribir_archivo", _factory(params, plantillas))


def _parsear_pts(fs, **params):
    return _registry(adapters={"fs": fs}).execute("convertidor.parsear_pts", _factory(params))


def _corregir(fs, **params):
    return _registry(adapters={"fs": fs}).execute("convertidor.corregir_puntos", _factory(params))


def _aplicar_expr(plantillas=(), **params):
    return _registry().execute("convertidor.aplicar_expresiones", _factory(params, plantillas))


def test_parsea_el_nombre_toothform_con_el_primer_patron_que_matchea():
    r = _parsear(nombre="AP962 Leandro Martinez 1.0 Inf 00 CNC2.stl", patrones=PATRONES_TOOTHFORM)
    assert r.status == "ok"
    assert r.outputs["patron"] == "standard"
    assert r.outputs["variables"]["id"] == "AP962"
    assert r.outputs["variables"]["maxilla"] == "Inf"
    assert r.outputs["variables"]["movement"] == "00"
    assert r.outputs["extension"] == "stl"


def test_ignora_la_ruta_y_usa_solo_el_nombre():
    r = _parsear(nombre="C:/casos/AP962/AP962 Leandro Martinez 1.0 Inf 00 CNC2.stl", patrones=PATRONES_TOOTHFORM)
    assert r.status == "ok"
    assert r.outputs["variables"]["id"] == "AP962"


def test_prueba_el_siguiente_patron_si_el_primero_no_matchea():
    # "M.5" no matchea 'standard' (\d+\.\d+ pide dígito antes del punto) pero sí 'medium'.
    r = _parsear(nombre="BG631 Alvarez Camila M.5 Inf 02 CNC2.stl", patrones=PATRONES_TOOTHFORM)
    assert r.status == "ok"
    assert r.outputs["patron"] == "medium"
    assert r.outputs["variables"]["stage"] == "M.5"


def test_patron_legacy_distinto_al_de_toothform():
    r = _parsear(nombre="AB123-L01-A.stl", patrones=PATRONES_LEGACY)
    assert r.status == "ok"
    assert r.outputs["variables"] == {"id": "AB123", "maxilla": "L", "movement": "01", "type": "A"}


def test_ningun_patron_matchea_es_err():
    r = _parsear(nombre="algo-que-no-matchea.stl", patrones=PATRONES_LEGACY)
    assert r.status == "err"
    assert r.outputs["patron"] == ""
    assert r.outputs["variables"] == {}


def test_nombre_o_patrones_vacio_es_err_claro():
    assert "nombre" in _parsear(nombre="", patrones=PATRONES_LEGACY).message
    assert "patrones" in _parsear(nombre="AB123-L01-A.stl", patrones={}).message


def test_regex_invalida_es_err_con_la_clave():
    r = _parsear(nombre="AB123-L01-A.stl", patrones={"roto": "(["})
    assert r.status == "err"
    assert "roto" in r.message


def test_generar_nombre_aplica_el_template_y_la_extension():
    variables = {"id": "AB123", "maxilla": "L", "movement": "01", "type": "A"}
    r = _generar(variables=variables, template="{id}-{maxilla}{movement}-{type}", extension="stl")
    assert r.status == "ok"
    assert r.outputs["nombre_nuevo"] == "AB123-L01-A.stl"


def test_generar_nombre_sin_extension_no_agrega_punto():
    r = _generar(variables={"id": "AB123"}, template="{id}", extension="")
    assert r.status == "ok"
    assert r.outputs["nombre_nuevo"] == "AB123"


def test_generar_nombre_con_variable_faltante_es_err_claro():
    r = _generar(variables={"id": "AB123"}, template="{id}-{maxilla}", extension="")
    assert r.status == "err"
    assert "maxilla" in r.message


def test_generar_nombre_con_template_vacio_es_err():
    r = _generar(variables={}, template="", extension="")
    assert r.status == "err"


def test_reescribir_archivo_copia_con_nombre_y_carpeta_nuevos():
    origen = "C:/casos/AP962/stl/AP962 Leandro Martinez 1.0 Inf 00 CNC2.stl"
    fs = FakeFs(files={origen: "contenido-stl"})
    r = _reescribir(
        fs, origen=origen, carpeta_salida="D:/export", patrones=PATRONES_TOOTHFORM,
        template_nombre="{id}-{maxilla}{movement}", template_carpeta="{id}",
    )
    assert r.status == "ok"
    assert r.outputs["nombre_nuevo"] == "AP962-Inf00.stl"
    assert r.outputs["ruta"] == "D:/export/AP962/AP962-Inf00.stl"
    assert r.outputs["patron"] == "standard"
    assert fs.files["D:/export/AP962/AP962-Inf00.stl"] == "contenido-stl"
    assert fs.files[origen] == "contenido-stl"  # el origen queda intacto


def test_reescribir_archivo_sin_template_carpeta_va_directo_a_la_salida():
    origen = "C:/casos/AB123-L01-A.stl"
    fs = FakeFs(files={origen: "x"})
    r = _reescribir(
        fs, origen=origen, carpeta_salida="D:/export", patrones=PATRONES_LEGACY,
        template_nombre="{id}_{maxilla}{movement}_{type}", template_carpeta="",
    )
    assert r.status == "ok"
    assert r.outputs["ruta"] == "D:/export/AB123_L01_A.stl"


def test_reescribir_archivo_origen_inexistente_es_err():
    fs = FakeFs()
    r = _reescribir(
        fs, origen="C:/no-existe.stl", carpeta_salida="D:/export",
        patrones=PATRONES_LEGACY, template_nombre="{id}", template_carpeta="",
    )
    assert r.status == "err"
    assert "no existe" in r.message


def test_reescribir_archivo_sin_match_es_err_y_no_copia_nada():
    origen = "C:/casos/no-matchea.stl"
    fs = FakeFs(files={origen: "x"})
    r = _reescribir(
        fs, origen=origen, carpeta_salida="D:/export", patrones=PATRONES_LEGACY,
        template_nombre="{id}", template_carpeta="",
    )
    assert r.status == "err"
    assert list(fs.files) == [origen]


def test_parsear_y_generar_encadenados_toothform_a_legacy():
    parseo = _parsear(nombre="AP962 Leandro Martinez 1.0 Inf 00 CNC2.stl", patrones=PATRONES_TOOTHFORM)
    assert parseo.status == "ok"
    variables = dict(parseo.outputs["variables"], type="A")
    generado = _generar(
        variables=variables, template="{id}-{maxilla}{movement}-{type}", extension=parseo.outputs["extension"],
    )
    assert generado.status == "ok"
    assert generado.outputs["nombre_nuevo"] == "AP962-Inf00-A.stl"


# ── parsear_pts ─────────────────────────────────────────────────────────

PTS_EJEMPLO = """
inf 00
0.0 0.0 0.0
1.0 0.0 0.0

sup 05
2.0 2.0 2.0
""".strip("\n")


def test_parsear_pts_separa_secciones_por_maxilla_y_movimiento():
    fs = FakeFs(files={"D:/casos/AP962.pts": PTS_EJEMPLO})
    r = _parsear_pts(fs, ruta="D:/casos/AP962.pts")
    assert r.status == "ok"
    assert r.outputs["cantidad"] == 2
    assert r.outputs["secciones"] == {
        "inf_00": ["0.0 0.0 0.0", "1.0 0.0 0.0"],
        "sup_05": ["2.0 2.0 2.0"],
    }


def test_parsear_pts_sin_secciones_es_err():
    fs = FakeFs(files={"D:/casos/vacio.pts": "esto no tiene ninguna sección\n"})
    r = _parsear_pts(fs, ruta="D:/casos/vacio.pts")
    assert r.status == "err"
    assert r.outputs["secciones"] == {}


def test_parsear_pts_archivo_inexistente_es_err():
    r = _parsear_pts(FakeFs(), ruta="D:/no-existe.pts")
    assert r.status == "err"


# ── corregir_puntos ─────────────────────────────────────────────────────

# Un solo triángulo en el plano XY (z=0): (0,0,0), (1,0,0), (0,1,0).
STL_TRIANGULO = """
solid test
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
endsolid test
""".strip("\n")


def test_corregir_puntos_sin_trimesh_instalado_es_err_claro(monkeypatch):
    # Oculta trimesh aunque esté instalado, para probar el camino sin la dependencia.
    monkeypatch.setitem(sys.modules, "trimesh", None)
    fs = FakeFs(files={"D:/malla.stl": STL_TRIANGULO})
    r = _corregir(fs, stl="D:/malla.stl", puntos=["0 0 0"], tolerancia=0.5, toda_la_malla=False)
    assert r.status == "err"
    assert "trimesh" in r.message


def test_corregir_puntos_archivo_stl_inexistente_es_err():
    r = _corregir(FakeFs(), stl="D:/no-existe.stl", puntos=["0 0 0"], tolerancia=0.5, toda_la_malla=False)
    assert r.status == "err"


def test_corregir_puntos_sin_puntos_es_err():
    fs = FakeFs(files={"D:/malla.stl": STL_TRIANGULO})
    r = _corregir(fs, stl="D:/malla.stl", puntos=[], tolerancia=0.5, toda_la_malla=False)
    assert r.status == "err"


def test_corregir_puntos_reemplaza_solo_lo_que_supera_la_tolerancia():
    pytest.importorskip("trimesh")
    pytest.importorskip("numpy")
    fs = FakeFs(files={"D:/malla.stl": STL_TRIANGULO})
    r = _corregir(
        fs, stl="D:/malla.stl",
        puntos=["0.2 0.2 0.0", "0.2 0.2 5.0"],
        tolerancia=0.5, toda_la_malla=False,
    )
    assert r.status == "ok"
    assert r.outputs["reemplazados"] == 1
    # El primero ya estaba sobre la malla: queda igual.
    assert r.outputs["puntos"][0] == "0.2 0.2 0.0"
    # El segundo estaba a distancia 5: se reemplaza por el punto más cercano en superficie.
    corregido = [float(v) for v in r.outputs["puntos"][1].split()]
    assert corregido[2] == pytest.approx(0.0, abs=1e-6)
    assert r.outputs["distancia_maxima"] == pytest.approx(5.0, abs=1e-6)


# ── aplicar_expresiones ─────────────────────────────────────────────────
#
# Los mismos ejemplos que traía la ayuda del Gestor de Expresiones original.

def test_if_con_condicion_verdadera_ejecuta_la_primera_accion():
    r = _aplicar_expr(
        variables={"movement": "00", "type": "A"},
        expresiones=['IF({movement}="00", {type}:"B", {type}:"A")'],
    )
    assert r.status == "ok"
    assert r.outputs["variables"]["type"] == "B"


def test_if_con_condicion_falsa_ejecuta_la_segunda_accion():
    r = _aplicar_expr(
        variables={"movement": "01", "type": "A"},
        expresiones=['IF({movement}="00", {type}:"B", {type}:"A")'],
    )
    assert r.status == "ok"
    assert r.outputs["variables"]["type"] == "A"


def test_ifs_prueba_condiciones_en_orden_y_cae_al_default():
    expresiones = ['IFS({movement}="00", {type}:"B", {movement}="01", {type}:"C", {type}:"A")']
    assert _aplicar_expr(variables={"movement": "00"}, expresiones=expresiones).outputs["variables"]["type"] == "B"
    assert _aplicar_expr(variables={"movement": "01"}, expresiones=expresiones).outputs["variables"]["type"] == "C"
    assert _aplicar_expr(variables={"movement": "99"}, expresiones=expresiones).outputs["variables"]["type"] == "A"


def test_set_asigna_directo():
    r = _aplicar_expr(variables={}, expresiones=['SET({type}, "X")'])
    assert r.status == "ok"
    assert r.outputs["variables"]["type"] == "X"


def test_upper_y_lower_transforman_una_variable_existente():
    r = _aplicar_expr(variables={"patient": "Leandro Martinez"}, expresiones=["UPPER({patient})"])
    assert r.outputs["variables"]["patient"] == "LEANDRO MARTINEZ"

    r = _aplicar_expr(variables={"maxilla": "Inf"}, expresiones=["LOWER({maxilla})"])
    assert r.outputs["variables"]["maxilla"] == "inf"


def test_condicion_con_lower_compara_sin_distinguir_mayusculas():
    r = _aplicar_expr(
        variables={"maxilla": "Inf"},
        expresiones=['IF(LOWER({maxilla})="inf", {type}:"L", {type}:"U")'],
    )
    assert r.outputs["variables"]["type"] == "L"


def test_varias_expresiones_se_aplican_en_orden():
    r = _aplicar_expr(
        variables={"movement": "00"},
        expresiones=['SET({type}, "A")', 'IF({movement}="00", {type}:"B", SET({type}, "C"))'],
    )
    assert r.outputs["variables"]["type"] == "B"


def test_expresion_invalida_se_reporta_y_no_corta_las_demas():
    r = _aplicar_expr(
        variables={},
        expresiones=['ESTO_NO_EXISTE({x})', 'SET({type}, "A")'],
    )
    assert r.status == "err"
    assert r.outputs["variables"]["type"] == "A"  # la segunda igual se aplicó
    assert len(r.outputs["errores"]) == 1


def test_sin_expresiones_devuelve_las_variables_intactas():
    r = _aplicar_expr(variables={"id": "AP962"}, expresiones=[])
    assert r.status == "ok"
    assert r.outputs["variables"] == {"id": "AP962"}
    assert r.outputs["errores"] == []


# ── plantillas ──────────────────────────────────────────────────────────

PLANTILLA_LEGACY = {
    "nombre": "legacy",
    "patrones": PATRONES_LEGACY,
    "template_nombre": "{id}_{maxilla}{movement}_{type}",
    "template_carpeta": "{id}",
    "expresiones": ['SET({type}, "X")'],
}


def test_parsear_nombre_usa_los_patrones_de_la_plantilla_sin_pasarlos():
    r = _parsear(nombre="AB123-L01-A.stl", plantillas=[PLANTILLA_LEGACY], plantilla="legacy")
    assert r.status == "ok"
    assert r.outputs["patron"] == "legacy"
    assert r.outputs["variables"]["id"] == "AB123"


def test_patrones_explicito_gana_por_encima_de_la_plantilla():
    otra_plantilla = {**PLANTILLA_LEGACY, "patrones": {"nunca": r"NO-MATCHEA-NADA"}}
    r = _parsear(
        nombre="AB123-L01-A.stl", plantillas=[otra_plantilla], plantilla="legacy",
        patrones=PATRONES_LEGACY,
    )
    assert r.status == "ok"
    assert r.outputs["patron"] == "legacy"


def test_plantilla_inexistente_es_err_con_las_conocidas():
    r = _parsear(nombre="AB123-L01-A.stl", plantillas=[PLANTILLA_LEGACY], plantilla="no-existe")
    assert r.status == "err"
    assert "legacy" in r.message


def test_generar_nombre_usa_el_template_de_la_plantilla():
    r = _generar(
        variables={"id": "AB123", "maxilla": "L", "movement": "01", "type": "A"},
        plantillas=[PLANTILLA_LEGACY], plantilla="legacy",
    )
    assert r.status == "ok"
    assert r.outputs["nombre_nuevo"] == "AB123_L01_A"


def test_reescribir_archivo_usa_patrones_y_templates_de_la_plantilla():
    origen = "C:/casos/AB123-L01-A.stl"
    fs = FakeFs(files={origen: "x"})
    r = _reescribir(
        fs, origen=origen, carpeta_salida="D:/export", plantillas=[PLANTILLA_LEGACY], plantilla="legacy",
    )
    assert r.status == "ok"
    assert r.outputs["ruta"] == "D:/export/AB123/AB123_L01_A.stl"


def test_aplicar_expresiones_usa_las_de_la_plantilla():
    r = _aplicar_expr(variables={}, plantillas=[PLANTILLA_LEGACY], plantilla="legacy")
    assert r.status == "ok"
    assert r.outputs["variables"]["type"] == "X"
