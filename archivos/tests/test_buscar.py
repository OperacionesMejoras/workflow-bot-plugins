"""
`archivos.buscar`: por etiqueta, por expresión regular, o las dos. Con el
filesystem falso del núcleo. Corre con `python -m pytest plugins/archivos`.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from backend.core.contract import ToolContext  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeFs  # noqa: E402

from plugins.archivos.plugin import build_plugin  # noqa: E402

CASO = "C:/casos/QATF001/stl"
ARCHIVOS = {
    f"{CASO}/QATF001-L01-A.stl": "",
    f"{CASO}/QATF001-U01-A.stl": "",
    f"{CASO}/QATF001-L01-A-gum.stl": "",
    f"{CASO}/notas.txt": "",
    f"{CASO}/sub/otro-L02-B.STL": "",
}


def _buscar(log=None, **params):
    reg = ToolRegistry(adapters={"fs": FakeFs(files=ARCHIVOS)})
    reg._add_plugin("archivos", "plugins.archivos:PLUGIN", build_plugin())
    anotar = (lambda m, level="info": log(m)) if log else (lambda *_a, **_k: None)

    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params({"carpeta": CASO, **params}, {})
        return ToolContext(
            run_id="run-test", case_id="QATF001", params=declarados, extras=extras,
            config={}, context={}, log=anotar, ports=ports or {},
        )

    return reg.execute("archivos.buscar", factory)


def test_por_etiqueta_es_substring_sin_mayusculas_y_recursivo():
    r = _buscar(etiqueta=".stl")
    assert r.status == "ok"
    assert r.outputs["cantidad"] == 4
    assert r.outputs["primera"].endswith("QATF001-L01-A-gum.stl")  # orden del walk: alfabético


def test_por_patron_distingue_el_modelo_de_sus_derivados():
    # Sólo los datos que ToothFORM carga: <nombre>-L01-A.stl, no el -gum.stl que exporta.
    r = _buscar(patron=r"^[A-Z0-9]+-[LU]\d{2}-[A-Z]\.stl$")
    assert r.status == "ok"
    assert sorted(pathlib.Path(p).name for p in r.outputs["rutas"]) == [
        "QATF001-L01-A.stl", "QATF001-U01-A.stl", "otro-L02-B.STL",
    ]


def test_el_patron_de_los_flujos_toothform_es_el_nombre_legacy_del_stl():
    # El legacy (folderSearchPatterns, label "toothform") aceptaba ext .stl y
    # stem ^(?P<id>[A-Z]{2}\d{3})-(?P<maxilla>[LU])(?P<movement>\d{2})-(?P<type>[A-Z])$.
    # En el .mmd va sin llaves ({2} se leería como variable) y con la extensión adentro.
    patron = r"^[A-Z][A-Z]\d\d\d-[LU]\d\d-[A-Z]\.stl$"
    archivos = {
        f"{CASO}/AB123-L01-A.stl": "", f"{CASO}/AB123-U12-B.STL": "",
        f"{CASO}/AB123-L01-A-gum.stl": "", f"{CASO}/QATF001-L01-A.stl": "",
        f"{CASO}/AB1234-L01-A.stl": "", f"{CASO}/AB123-L1-A.stl": "",
    }
    reg = ToolRegistry(adapters={"fs": FakeFs(files=archivos)})
    reg._add_plugin("archivos", "plugins.archivos:PLUGIN", build_plugin())

    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params({"carpeta": CASO, "patron": patron}, {})
        return ToolContext(
            run_id="run-test", case_id="AB123", params=declarados, extras=extras,
            config={}, context={}, log=lambda *_a, **_k: None, ports=ports or {},
        )

    r = reg.execute("archivos.buscar", factory)
    assert r.status == "ok"
    assert sorted(pathlib.Path(p).name for p in r.outputs["rutas"]) == ["AB123-L01-A.stl", "AB123-U12-B.STL"]


def test_etiqueta_y_patron_se_combinan():
    r = _buscar(etiqueta="QATF001", patron=r"-L\d{2}-")
    assert r.status == "ok"
    assert r.outputs["cantidad"] == 2


def test_devuelve_la_carpeta_que_contiene_lo_encontrado():
    # `buscar` da archivos, y hay tools que piden una carpeta (el 'exportar' de
    # ToothFORM). Sin esto no se pueden encadenar: encadenar la carpeta que se
    # buscó tampoco sirve, porque el walk es recursivo y lo encontrado puede
    # estar más abajo.
    r = _buscar(patron=r"^QATF001-[LU]\d{2}-[A-Z]\.stl$")
    assert r.status == "ok"
    assert r.outputs["carpeta"] == CASO
    assert r.outputs["carpetas"] == [CASO]


def test_con_resultados_repartidos_avisa_que_carpeta_deja_afuera():
    # Una sola carpeta no representa al resultado, y elegirla en silencio haría
    # que un export procesara una parte como si fueran todos.
    registro = []
    r = _buscar(etiqueta=".stl", log=registro.append)
    assert r.status == "ok"
    assert r.outputs["carpetas"] == [CASO, f"{CASO}/sub"]
    assert r.outputs["carpeta"] == CASO  # la de 'primera'
    assert any("repartidos en 2 carpetas" in m for m in registro)


def test_sin_coincidencias_es_err_con_salidas_vacias():
    r = _buscar(etiqueta="no-existe")
    assert r.status == "err"
    assert r.outputs == {"rutas": [], "cantidad": 0, "primera": "", "carpeta": "", "carpetas": []}


def test_sin_criterio_o_con_regex_rota_es_err_claro():
    assert "etiqueta" in _buscar().message
    assert "expresión regular" in _buscar(patron="[").message
