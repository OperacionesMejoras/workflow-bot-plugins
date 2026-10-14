"""
`convertidor.toothform`: la salida de ToothFORM armada adentro del plugin
(convertidor/enkukador, copiado de Enkukador).

La geometría necesita las librerías de verdad —no tiene sentido fakearlas—, así
que esos tests se saltan si falta alguna. Van contra el disco real (`tmp_path`):
el `FakeFs` guarda texto y rompe un STL binario.

Corre con `python -m pytest plugins/convertidor`.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from backend.adapters.fs_local import LocalFsAdapter  # noqa: E402
from backend.core.contract import ToolContext  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeFs  # noqa: E402

from plugins.convertidor.plugin import build_plugin  # noqa: E402

LIBRERIAS = ("numpy", "trimesh", "shapely", "networkx", "manifold3d", "skimage", "PIL", "segno", "fontTools")


def _con_librerias():
    for nombre in LIBRERIAS:
        pytest.importorskip(nombre)


def _toothform(fs, logs=None, **params):
    reg = ToolRegistry(adapters={"fs": fs})
    reg._add_plugin("convertidor", "plugins.convertidor:PLUGIN", build_plugin())

    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params(params, {})
        return ToolContext(
            run_id="run-test", case_id="AB123", params=declarados, extras=extras,
            config={}, context={}, ports=ports or {},
            log=(lambda m, *_a, **_k: logs.append(m)) if logs is not None else (lambda *_a, **_k: None),
        )

    return reg.execute("convertidor.toothform", factory)


def _arcada_con_dientes():
    """Una U extruida con la base en z=0 y ocho "dientes" sobre la cresta, como
    la que usa Enkukador en sus tests (sin pasar por su alineado)."""
    import numpy as np
    import trimesh
    from shapely.geometry import Point, box

    from plugins.convertidor.enkukador import arco

    anillo = Point(0, 0).buffer(27, 256).difference(Point(0, 0).buffer(17, 256))
    u = anillo.intersection(box(-50, -6, 50, 50))
    encia = trimesh.creation.extrude_polygon(u.simplify(0.05), 14, engine="manifold")
    curva = arco.remuestrear_curva(arco.curva_central(arco.contorno_base(encia, 0.5)), 0.5)
    dientes = []
    for i in np.linspace(len(curva) * 0.1, len(curva) * 0.9, 8).astype(int):
        d = trimesh.creation.cylinder(radius=2.5, height=8.0, sections=24)
        d.apply_translation([curva[i, 0], curva[i, 1], 13.0])
        dientes.append(d)
    return trimesh.util.concatenate([encia, *dientes])


@pytest.fixture(scope="module")
def arcada_stl():
    _con_librerias()
    return _arcada_con_dientes().export(file_type="stl")


def test_exporta_como_toothform_pieza_gum_tooth_y_log(tmp_path, arcada_stl):
    import trimesh

    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-L05-A.stl").write_bytes(arcada_stl)
    r = _toothform(LocalFsAdapter(root=tmp_path), carpeta="caso", salida="salida", ShellThickness="1.8")

    assert r.status == "ok", r.message
    o = r.outputs
    assert (o["exitosos"], o["fallidos"], o["esperados"], o["escritos"], o["faltantes"]) == (1, 0, 1, 1, [])
    assert o["exportados"] == ["AB123-L05-A"] and o["hubo_log"] == "si"
    assert pathlib.Path(o["carpeta_export"]).name == "AB123"
    hecho = tmp_path / "salida" / "AB123"
    assert sorted(p.name for p in hecho.iterdir()) == [
        "AB123-L05-A-gum.stl", "AB123-L05-A-tooth.ply", "AB123-L05-A.stl",
    ]
    pieza = trimesh.load(hecho / "AB123-L05-A.stl")
    assert pieza.is_watertight and pieza.bounds[0][2] == pytest.approx(0, abs=1e-6)
    # El log, con el formato que lee 'toothform.check_log' / el flujo.
    log = (tmp_path / "salida" / o["log_file"]).read_text()
    assert log == "AB123-L05-A    Export successfully\n\nTotal 1 models, of which 1 succeeded and 0 failed\n"
    assert o["checkLogResult"] == {"log_file": o["log_file"], "message": "AB123-L05-A    Export successfully"}


def test_un_modelo_roto_no_frena_el_resto_y_es_err_con_su_motivo(tmp_path, arcada_stl):
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-L05-A.stl").write_bytes(arcada_stl)
    (tmp_path / "caso" / "AB123-U05-A.stl").write_text("no es un stl")
    r = _toothform(LocalFsAdapter(root=tmp_path), archivos=["caso/AB123-L05-A.stl", "caso/AB123-U05-A.stl"],
                   salida="salida")

    assert r.status == "err" and r.error_kind == "modelo_fallido"
    o = r.outputs
    assert o["exportados"] == ["AB123-L05-A"] and o["faltantes"] == ["AB123-U05-A"]
    assert o["modelos_fallidos"][0]["modelo"] == "AB123-U05-A"
    assert o["modelos_fallidos"][0]["motivo"].startswith("Failed to hollow (")
    assert "AB123-U05-A    Failed to hollow" in o["log_texto"]
    assert (tmp_path / "salida" / "AB123" / "AB123-L05-A.stl").exists()


def test_no_toma_una_salida_como_entrada():
    fs = FakeFs(files={"D:/caso/AB123/AB123-L05-A.stl": "x", "D:/caso/AB123/AB123-L05-A-gum.stl": "x"})
    r = _toothform(fs, carpeta="D:/caso/AB123", salida="D:/salida")
    assert r.status == "err" and r.error_kind == "entrada_exportada"


def test_parametros_desconocidos_o_que_no_son_numeros(monkeypatch):
    fs = FakeFs(files={"D:/caso/AB123-L05-A.stl": "x"})
    r = _toothform(fs, carpeta="D:/caso", salida="D:/salida", LimitX="2.5")
    assert r.status == "err" and "LimitX" in r.message

    _con_librerias()
    r = _toothform(fs, carpeta="D:/caso", salida="D:/salida", QrSize="grande")
    assert r.status == "err" and "QrSize" in r.message


def test_sin_las_librerias_dice_cual_falta(monkeypatch):
    for nombre in [m for m in sys.modules if m.startswith("plugins.convertidor.enkukador")]:
        monkeypatch.delitem(sys.modules, nombre)
    monkeypatch.setitem(sys.modules, "manifold3d", None)
    monkeypatch.setitem(sys.modules, "shapely", None)
    fs = FakeFs(files={"D:/caso/AB123-L05-A.stl": "x"})
    r = _toothform(fs, carpeta="D:/caso", salida="D:/salida")
    assert r.status == "err" and r.error_kind == "librerias"
    assert "requirements.txt" in r.message


def test_carpeta_sin_modelos_y_carpeta_o_archivos():
    fs = FakeFs(files={"D:/caso/notas.txt": "x"})
    assert _toothform(fs, carpeta="D:/caso", salida="D:/s").status == "err"
    assert _toothform(fs, salida="D:/s").status == "err"


def test_el_manifest_se_puede_imprimir_en_una_consola_de_windows():
    import json

    plugin = build_plugin()
    texto = json.dumps(
        {"plugin": plugin.manifest.doc, "tools": [t.manifest.to_dict() for t in plugin.tools]},
        ensure_ascii=False, default=str,
    )
    assert not sorted({c for c in texto if not c.encode("cp1252", "ignore")})
