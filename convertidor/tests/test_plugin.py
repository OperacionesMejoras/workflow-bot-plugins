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
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from backend.core.contract import ToolContext  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeFs  # noqa: E402

from plugins.convertidor.plugin import _en_paralelo, build_plugin  # noqa: E402


def test_el_paquete_expone_plugin_como_lo_busca_el_nucleo():
    # backend/core/instance.py:_plugins_de_la_carpeta arma "convertidor:PLUGIN"
    # e importa el paquete raíz, no el submódulo plugin.py — un __init__.py
    # vacío (como pasó una vez) rompe esto sin que import plugins.convertidor.plugin
    # lo note, porque ese import bypasea el __init__.py del paquete.
    import plugins.convertidor as paquete

    assert hasattr(paquete, "PLUGIN"), "convertidor/__init__.py tiene que re-exportar PLUGIN"
    assert paquete.PLUGIN.manifest.name == "convertidor"


def test_en_paralelo_superpone_la_espera_en_vez_de_sumarla():
    # Si "rutas" con archivos lentos (red, WiFi) se procesaran de a uno, 5
    # ítems de 0.2s tardarían ~1s. En paralelo, la espera se superpone y
    # tarda apenas más que un solo ítem — esto es lo que reescribir_archivos
    # e inspeccionar_mallas ganan al usar _en_paralelo en vez de un for chato.
    def lento(item):
        time.sleep(0.2)
        return item * 2

    inicio = time.monotonic()
    resultado = _en_paralelo(list(range(5)), lento)
    transcurrido = time.monotonic() - inicio

    assert resultado == [0, 2, 4, 6, 8]  # mismo orden que la entrada, no el de finalización
    assert transcurrido < 0.6  # muy por debajo de 5 * 0.2s = 1s si fuera secuencial


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


def _inspeccionar(fs, **params):
    return _registry(adapters={"fs": fs}).execute("convertidor.inspeccionar_malla", _factory(params))


def _inspeccionar_varias(fs, **params):
    return _registry(adapters={"fs": fs}).execute("convertidor.inspeccionar_mallas", _factory(params))


def _extraer(fs, **params):
    return _registry(adapters={"fs": fs}).execute("convertidor.extraer_parte", _factory(params))


def _extraer_varias(fs, **params):
    return _registry(adapters={"fs": fs}).execute("convertidor.extraer_partes", _factory(params))


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


# El caso real que dejó armado Leandro para probar en el Bot del 8000.
PATRON_CNC3 = {
    "principal": r"(?P<id_externo>AP\d+)\s+(?P<nombre>[A-Za-z\s]+?)\s+\d+\.\d+\s+(?P<maxilar>Inf|Sup)\s+(?P<movimiento>\d+)\s+CNC\d+",
}
EXPRESIONES_CNC3 = [
    'IF(LOWER({maxilar})="inf", {maxilar}:"L", SET({maxilar}, "U"))',
    'IF({movimiento}="00", {type}:"B", {type}:"A")',
]


def test_reescribir_archivo_aplica_expresiones_antes_de_armar_el_nombre():
    origen = "C:/casos/AP962 Leandro Martinez 1.0 Inf 00 CNC2.stl"
    fs = FakeFs(files={origen: "x"})
    r = _reescribir(
        fs, origen=origen, carpeta_salida="D:/export", patrones=PATRON_CNC3,
        expresiones=EXPRESIONES_CNC3, template_nombre="{id_externo}-{maxilar}{movimiento}-{type}",
    )
    assert r.status == "ok"
    # maxilar "Inf" -> "L" y type "B" (movimiento "00") vienen de las
    # expresiones, no del regex: si reescribir_archivo no las aplicara, el
    # nombre saldría con el "Inf" crudo y sin "type" (KeyError).
    assert r.outputs["ruta"] == "D:/export/AP962-L00-B.stl"


def test_reescribir_archivo_con_expresion_invalida_es_err():
    origen = "C:/casos/AP962 Leandro Martinez 1.0 Inf 00 CNC2.stl"
    fs = FakeFs(files={origen: "x"})
    r = _reescribir(
        fs, origen=origen, carpeta_salida="D:/export", patrones=PATRON_CNC3,
        expresiones=["ESTO_NO_EXISTE({x})"], template_nombre="{id_externo}",
    )
    assert r.status == "err"
    assert "ESTO_NO_EXISTE" in r.message
    assert list(fs.files) == [origen]  # no llegó a copiar nada


# ── reescribir_archivos (batch) ──────────────────────────────────────────

def _reescribir_varios(fs, plantillas=(), **params):
    return _registry(adapters={"fs": fs}).execute(
        "convertidor.reescribir_archivos", _factory(params, plantillas),
    )


def test_reescribir_archivos_procesa_todas_las_rutas():
    archivos = {
        "C:/casos/AP962 Leandro Martinez 1.0 Inf 00 CNC2.stl": "x",
        "C:/casos/AP962 Leandro Martinez 1.0 Sup 05 CNC2.stl": "x",
    }
    fs = FakeFs(files=archivos)
    r = _reescribir_varios(
        fs, rutas=list(archivos), carpeta_salida="D:/export", patrones=PATRON_CNC3,
        expresiones=EXPRESIONES_CNC3, template_nombre="{id_externo}-{maxilar}{movimiento}-{type}",
    )
    assert r.status == "ok"
    assert r.outputs["procesados"] == 2
    assert r.outputs["fallidos"] == 0
    assert r.outputs["rutas_fallidas"] == []
    rutas_nuevas = {res["ruta"] for res in r.outputs["resultados"]}
    assert rutas_nuevas == {"D:/export/AP962-L00-B.stl", "D:/export/AP962-U05-A.stl"}


def test_reescribir_archivos_sigue_con_el_resto_si_uno_falla():
    archivos = {
        "C:/casos/AP962 Leandro Martinez 1.0 Inf 00 CNC2.stl": "x",
        "C:/casos/no-matchea-nada.stl": "x",
    }
    fs = FakeFs(files=archivos)
    r = _reescribir_varios(
        fs, rutas=list(archivos), carpeta_salida="D:/export", patrones=PATRON_CNC3,
        expresiones=EXPRESIONES_CNC3, template_nombre="{id_externo}-{maxilar}{movimiento}-{type}",
    )
    assert r.status == "err"
    assert r.outputs["procesados"] == 1
    assert r.outputs["fallidos"] == 1
    assert r.outputs["rutas_fallidas"] == ["C:/casos/no-matchea-nada.stl"]
    # el que sí matcheaba se copió igual, no lo frenó la falla del otro
    assert fs.files.get("D:/export/AP962-L00-B.stl") == "x"


def test_reescribir_archivos_rutas_vacio_es_err():
    r = _reescribir_varios(FakeFs(), rutas=[], carpeta_salida="D:/export", patrones=PATRON_CNC3, template_nombre="{id_externo}")
    assert r.status == "err"


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


# ── corregir_puntos / inspeccionar_malla ─────────────────────────────────

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

# Dos triángulos que no comparten ningún vértice: dos volúmenes desconectados
# (el caso que el "Inspector de Mallas" original marcaba en rojo).
STL_DOS_VOLUMENES = """
solid test
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 10 0 0
  vertex 11 0 0
  vertex 10 1 0
 endloop
endfacet
endsolid test
""".strip("\n")

# Tres islas desconectadas con 1, 2 y 3 caras respectivamente (por índice de
# aparición: partes[0] tiene 1 cara, partes[1] tiene 2, partes[2] tiene 3) —
# verificado con trimesh real que ese es el orden que devuelve .split(), así
# que "ordenar_por_tamano" cambia el orden y "sin ordenar" no.
STL_TRES_PARTES = """
solid test
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 10 0 0
  vertex 11 0 0
  vertex 11 1 0
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 10 0 0
  vertex 11 1 0
  vertex 10 1 0
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 20 0 0
  vertex 21 0 0
  vertex 21 1 0
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 20 0 0
  vertex 21 1 0
  vertex 20 1 0
 endloop
endfacet
facet normal 0 0 1
 outer loop
  vertex 21 0 0
  vertex 22 0 0
  vertex 21 1 0
 endloop
endfacet
endsolid test
""".strip("\n")


def test_inspeccionar_malla_de_un_solo_volumen():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/malla.stl": STL_TRIANGULO})
    r = _inspeccionar(fs, stl="D:/malla.stl")
    assert r.status == "ok"
    assert r.outputs["volumenes"] == 1
    assert r.outputs["es_multivolumen"] is False
    assert r.outputs["partes"] == [{"caras": 1, "vertices": 3}]


def test_inspeccionar_malla_multivolumen():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/malla.stl": STL_DOS_VOLUMENES})
    r = _inspeccionar(fs, stl="D:/malla.stl")
    assert r.status == "ok"
    assert r.outputs["volumenes"] == 2
    assert r.outputs["es_multivolumen"] is True
    assert len(r.outputs["partes"]) == 2


def test_inspeccionar_malla_sin_trimesh_instalado_es_err_claro(monkeypatch):
    monkeypatch.setitem(sys.modules, "trimesh", None)
    fs = FakeFs(files={"D:/malla.stl": STL_TRIANGULO})
    r = _inspeccionar(fs, stl="D:/malla.stl")
    assert r.status == "err"
    assert "trimesh" in r.message


def test_inspeccionar_malla_archivo_inexistente_es_err():
    r = _inspeccionar(FakeFs(), stl="D:/no-existe.stl")
    assert r.status == "err"


# ── inspeccionar_mallas (batch) ──────────────────────────────────────────

def test_inspeccionar_mallas_procesa_todas_y_junta_las_multivolumen():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={
        "D:/uno.stl": STL_TRIANGULO,
        "D:/dos.stl": STL_DOS_VOLUMENES,
    })
    r = _inspeccionar_varias(fs, rutas=["D:/uno.stl", "D:/dos.stl"])
    assert r.status == "ok"
    assert r.outputs["procesados"] == 2
    assert r.outputs["fallidos"] == 0
    assert r.outputs["rutas_fallidas"] == []
    assert r.outputs["multivolumen"] == ["D:/dos.stl"]
    volumenes = {res["ruta"]: res["volumenes"] for res in r.outputs["resultados"]}
    assert volumenes == {"D:/uno.stl": 1, "D:/dos.stl": 2}


def test_inspeccionar_mallas_sigue_con_el_resto_si_una_falla():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/uno.stl": STL_TRIANGULO})
    r = _inspeccionar_varias(fs, rutas=["D:/uno.stl", "D:/no-existe.stl"])
    assert r.status == "err"
    assert r.outputs["procesados"] == 1
    assert r.outputs["fallidos"] == 1
    assert r.outputs["rutas_fallidas"] == ["D:/no-existe.stl"]
    # la que sí existía se inspeccionó igual, no la frenó la falla de la otra
    ok_por_ruta = {res["ruta"]: res["ok"] for res in r.outputs["resultados"]}
    assert ok_por_ruta == {"D:/uno.stl": True, "D:/no-existe.stl": False}


def test_inspeccionar_mallas_sin_trimesh_instalado_es_err_claro(monkeypatch):
    monkeypatch.setitem(sys.modules, "trimesh", None)
    fs = FakeFs(files={"D:/malla.stl": STL_TRIANGULO})
    r = _inspeccionar_varias(fs, rutas=["D:/malla.stl"])
    assert r.status == "err"
    assert "trimesh" in r.message


def test_inspeccionar_mallas_rutas_vacio_es_err():
    r = _inspeccionar_varias(FakeFs(), rutas=[])
    assert r.status == "err"


# ── extraer_parte ─────────────────────────────────────────────────────────
#
# STL_TRES_PARTES: partes[0]=1 cara, partes[1]=2 caras, partes[2]=3 caras
# (orden real de trimesh.split(), verificado). "ordenar_por_tamano" con
# 'desc' pone ese orden como [2, 1, 0]; con 'asc', [0, 1, 2].
#
# Nota: FakeFs.write_bytes/read_bytes corrompe binario real (decodifica a
# UTF-8 con errors="replace" y reencodea) — no es un bug de este plugin, es
# de backend/tests/fakes.py. Por eso estos tests verifican los outputs de
# metadata (caras, vertices, indices_incluidos), que salen de la malla en
# memoria antes de escribirse, y no releen 'destino' a través de FakeFs.

def test_extraer_parte_ordenado_por_tamano_incluye_las_mas_grandes():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(
        fs, stl="D:/malla.stl", destino="D:/salida/top2.stl",
        indices=[0, 1], ordenar_por_tamano=True,  # 'desc' es default: las 2 más grandes
    )
    assert r.status == "ok"
    assert r.outputs["volumenes_totales"] == 3
    assert r.outputs["volumenes_incluidos"] == 2
    assert sorted(r.outputs["indices_incluidos"]) == [1, 2]  # las de 2 y 3 caras, no la de 1
    assert r.outputs["caras"] == 2 + 3
    assert "D:/salida/top2.stl" in fs.files


def test_extraer_parte_excluir_con_ordenar_por_tamano():
    pytest.importorskip("trimesh")
    # "todo menos la 2da más grande" (índice 1 en el orden desc = partes[1], 2 caras)
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(
        fs, stl="D:/malla.stl", destino="D:/salida/sin_2da.stl",
        indices=[1], modo="excluir", ordenar_por_tamano=True,
    )
    assert r.status == "ok"
    assert sorted(r.outputs["indices_incluidos"]) == [0, 2]  # quedan la más chica y la más grande
    assert r.outputs["caras"] == 1 + 3


def test_extraer_parte_indice_negativo_es_la_mas_chica_ordenando_por_tamano():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(
        fs, stl="D:/malla.stl", destino="D:/salida/chica.stl",
        indices=[-1], ordenar_por_tamano=True,
    )
    assert r.status == "ok"
    assert r.outputs["indices_incluidos"] == [0]  # partes[0] tiene 1 cara: la más chica
    assert r.outputs["caras"] == 1


def test_extraer_parte_sin_ordenar_usa_el_orden_de_trimesh_split():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(
        fs, stl="D:/malla.stl", destino="D:/salida/primera.stl",
        indices=[0],  # ordenar_por_tamano default False
    )
    assert r.status == "ok"
    assert r.outputs["indices_incluidos"] == [0]
    assert r.outputs["caras"] == 1  # partes[0] sin ordenar: la primera del archivo, no la más chica ni más grande


def test_extraer_parte_indice_fuera_de_rango_es_err_con_el_rango_valido():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(fs, stl="D:/malla.stl", destino="D:/salida/x.stl", indices=[5])
    assert r.status == "err"
    assert "-3..2" in r.message


def test_extraer_parte_indices_vacio_es_err():
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(fs, stl="D:/malla.stl", destino="D:/salida/x.stl", indices=[])
    assert r.status == "err"


def test_extraer_parte_modo_invalido_es_err():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(fs, stl="D:/malla.stl", destino="D:/salida/x.stl", indices=[0], modo="algo")
    assert r.status == "err"
    assert "algo" in r.message


def test_extraer_parte_excluir_todo_es_err_seleccion_vacia():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(fs, stl="D:/malla.stl", destino="D:/salida/x.stl", indices=[0, 1, 2], modo="excluir")
    assert r.status == "err"
    assert "D:/salida/x.stl" not in fs.files


def test_extraer_parte_archivo_inexistente_es_err():
    r = _extraer(FakeFs(), stl="D:/no-existe.stl", destino="D:/salida/x.stl", indices=[0])
    assert r.status == "err"


def test_extraer_parte_sin_trimesh_instalado_es_err_claro(monkeypatch):
    monkeypatch.setitem(sys.modules, "trimesh", None)
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer(fs, stl="D:/malla.stl", destino="D:/salida/x.stl", indices=[0])
    assert r.status == "err"
    assert "trimesh" in r.message


# ── extraer_partes (batch) ───────────────────────────────────────────────
#
# Existe porque pasarle una lista (ej. el 'multivolumen' de 'inspeccionar
# mallas') directo al 'stl' de 'extraer_parte' (que espera una sola ruta)
# rompe con "no existe el archivo: ['ruta1', 'ruta2', ...]" — el motor de
# flujos no tiene forma de "explotar" una lista en varios runs.

def test_extraer_partes_procesa_todas_guardando_con_el_nombre_original():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={
        "D:/uno/malla.stl": STL_TRES_PARTES,
        "D:/dos/malla.stl": STL_TRES_PARTES,
    })
    r = _extraer_varias(
        fs, stls=["D:/uno/malla.stl", "D:/dos/malla.stl"], carpeta_salida="D:/salida",
        indices=[0, 1], ordenar_por_tamano=True,
    )
    assert r.status == "ok"
    assert r.outputs["procesados"] == 2
    assert r.outputs["fallidos"] == 0
    assert r.outputs["rutas_fallidas"] == []
    rutas = {res["origen"]: res["ruta"] for res in r.outputs["resultados"]}
    assert rutas == {"D:/uno/malla.stl": "D:/salida/malla.stl", "D:/dos/malla.stl": "D:/salida/malla.stl"}
    for res in r.outputs["resultados"]:
        assert res["caras"] == 2 + 3  # las 2 más grandes de cada malla


def test_extraer_partes_sigue_con_el_resto_si_una_falla():
    pytest.importorskip("trimesh")
    fs = FakeFs(files={"D:/uno.stl": STL_TRES_PARTES})
    r = _extraer_varias(fs, stls=["D:/uno.stl", "D:/no-existe.stl"], carpeta_salida="D:/salida", indices=[0])
    assert r.status == "err"
    assert r.outputs["procesados"] == 1
    assert r.outputs["fallidos"] == 1
    assert r.outputs["rutas_fallidas"] == ["D:/no-existe.stl"]
    ok_por_origen = {res["origen"]: res["ok"] for res in r.outputs["resultados"]}
    assert ok_por_origen == {"D:/uno.stl": True, "D:/no-existe.stl": False}


def test_extraer_partes_sin_trimesh_instalado_es_err_claro(monkeypatch):
    monkeypatch.setitem(sys.modules, "trimesh", None)
    fs = FakeFs(files={"D:/malla.stl": STL_TRES_PARTES})
    r = _extraer_varias(fs, stls=["D:/malla.stl"], carpeta_salida="D:/salida", indices=[0])
    assert r.status == "err"
    assert "trimesh" in r.message


def test_extraer_partes_stls_vacio_es_err():
    r = _extraer_varias(FakeFs(), stls=[], carpeta_salida="D:/salida", indices=[0])
    assert r.status == "err"


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


def test_reescribir_archivo_usa_patrones_templates_y_expresiones_de_la_plantilla():
    origen = "C:/casos/AB123-L01-A.stl"
    fs = FakeFs(files={origen: "x"})
    r = _reescribir(
        fs, origen=origen, carpeta_salida="D:/export", plantillas=[PLANTILLA_LEGACY], plantilla="legacy",
    )
    assert r.status == "ok"
    # PLANTILLA_LEGACY trae expresiones=['SET({type}, "X")']: el "type"="A" que
    # sacó el regex queda pisado por la expresión antes de armar el nombre.
    assert r.outputs["ruta"] == "D:/export/AB123/AB123_L01_X.stl"


def test_aplicar_expresiones_usa_las_de_la_plantilla():
    r = _aplicar_expr(variables={}, plantillas=[PLANTILLA_LEGACY], plantilla="legacy")
    assert r.status == "ok"
    assert r.outputs["variables"]["type"] == "X"
