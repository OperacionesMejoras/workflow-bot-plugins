"""
`archivos.mover` y su `si_existe`: qué pasa cuando el destino ya está. El caso
que lo motivó es el reproceso de un caso en BOT.1, con el export de ToothFORM
que se mueve de la carpeta local a <caso>\\<id> y esa carpeta ya existe.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from backend.core.contract import ToolContext  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeFs  # noqa: E402

from plugins.archivos.plugin import build_plugin  # noqa: E402

LOCAL = "D:/toothform-salida/BZ219"
CASO = "C:/casos/BZ219_Perez Rocio"
NUEVO = {f"{LOCAL}/BZ219-L01-A.stl": "nuevo", f"{LOCAL}/BZ219-L01-A-gum.stl": "nuevo"}
VIEJO = {f"{CASO}/BZ219/BZ219-L01-A.stl": "viejo", f"{CASO}/BZ219/BZ219-U09-A.stl": "viejo"}


def _mover(archivos, **params):
    fs = FakeFs(files=archivos)
    reg = ToolRegistry(adapters={"fs": fs})
    reg._add_plugin("archivos", "plugins.archivos:PLUGIN", build_plugin())

    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params(params, {})
        return ToolContext(
            run_id="run-test", case_id="BZ219", params=declarados, extras=extras,
            config={}, context={}, log=lambda *_a, **_k: None, ports=ports or {},
        )

    return reg.execute("archivos.mover", factory), fs


def test_por_defecto_sigue_dejandolo_adentro_como_siempre():
    r, fs = _mover({**NUEVO, **VIEJO}, origen=LOCAL, destino=f"{CASO}/BZ219")
    assert r.status == "ok"
    assert r.outputs["ruta"] == f"{CASO}/BZ219/BZ219"


def test_reemplazar_borra_la_salida_anterior_y_deja_la_nueva_en_su_lugar():
    r, fs = _mover({**NUEVO, **VIEJO}, origen=LOCAL, destino=f"{CASO}/BZ219", si_existe="reemplazar")
    assert r.status == "ok", r.message
    assert r.outputs["ruta"] == f"{CASO}/BZ219"
    assert sorted(k for k in fs.files if k.startswith(CASO)) == [
        f"{CASO}/BZ219/BZ219-L01-A-gum.stl", f"{CASO}/BZ219/BZ219-L01-A.stl",
    ]
    assert fs.files[f"{CASO}/BZ219/BZ219-L01-A.stl"] == "nuevo"
    assert not any(k.startswith(LOCAL) for k in fs.files)


def test_reemplazar_sin_nada_en_el_destino_es_un_mover_comun():
    r, fs = _mover(dict(NUEVO), origen=LOCAL, destino=f"{CASO}/BZ219", si_existe="reemplazar")
    assert r.status == "ok", r.message
    assert r.outputs["ruta"] == f"{CASO}/BZ219"


def test_reemplazar_resuelve_los_puntos_del_flujo():
    # El flujo de BOT.1 arma destino={carpeta}/../{id_externo}.
    archivos = {**NUEVO, **VIEJO, f"{CASO}/Perez Rocio 1.0 stl/BZ219-L01-A.stl": "original"}
    r, fs = _mover(
        archivos, origen=LOCAL, destino=f"{CASO}/Perez Rocio 1.0 stl/../BZ219", si_existe="reemplazar",
    )
    assert r.status == "ok", r.message
    assert fs.files[f"{CASO}/Perez Rocio 1.0 stl/BZ219-L01-A.stl"] == "original"


def test_reemplazar_no_borra_el_origen_si_esta_adentro_del_destino():
    archivos = {f"{CASO}/BZ219/nuevo/x.stl": "x"}
    r, fs = _mover(archivos, origen=f"{CASO}/BZ219/nuevo", destino=f"{CASO}/BZ219", si_existe="reemplazar")
    assert r.status == "err"
    assert f"{CASO}/BZ219/nuevo/x.stl" in fs.files


def test_error_no_mueve_nada_si_el_destino_existe():
    r, fs = _mover({**NUEVO, **VIEJO}, origen=LOCAL, destino=f"{CASO}/BZ219", si_existe="error")
    assert r.status == "err" and r.error_kind == "destino_existe"
    assert all(k in fs.files for k in NUEVO) and all(k in fs.files for k in VIEJO)
