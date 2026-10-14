"""
`keep_form.exportar`: la salida de ToothFORM armada adentro del plugin
(keep_form/enkukador, copiado de Enkukador).

La geometría necesita las librerías de verdad —no tiene sentido fakearlas—, así
que esos tests se saltan si falta alguna. Van contra el disco real (`tmp_path`):
el `FakeFs` guarda texto y rompe un STL binario.

Corre con `python -m pytest plugins/keep_form`.
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

from plugins.keep_form.plugin import build_plugin  # noqa: E402

LIBRERIAS = ("numpy", "trimesh", "shapely", "networkx", "manifold3d", "skimage", "PIL", "segno", "fontTools")


def _con_librerias():
    for nombre in LIBRERIAS:
        pytest.importorskip(nombre)


def _exportar(fs, logs=None, **params):
    reg = ToolRegistry(adapters={"fs": fs})
    reg._add_plugin("keep-form", "plugins.keep_form:PLUGIN", build_plugin())

    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params(params, {})
        return ToolContext(
            run_id="run-test", case_id="AB123", params=declarados, extras=extras,
            config={}, context={}, ports=ports or {},
            log=(lambda m, *_a, **_k: logs.append(m)) if logs is not None else (lambda *_a, **_k: None),
        )

    return reg.execute("keep_form.exportar", factory)


def _arcada_con_dientes():
    """Una U extruida con la base en z=0 y ocho "dientes" sobre la cresta, como
    la que usa Enkukador en sus tests (sin pasar por su alineado)."""
    import numpy as np
    import trimesh
    from shapely.geometry import Point, box

    from plugins.keep_form.enkukador import arco

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
    r = _exportar(LocalFsAdapter(root=tmp_path), carpeta="caso", salida="salida", ShellThickness="1.8")

    assert r.status == "ok", r.message
    o = r.outputs
    assert (o["exitosos"], o["fallidos"], o["esperados"], o["escritos"], o["faltantes"]) == (1, 0, 1, 1, [])
    assert o["exportados"] == ["AB123-L05-A"] and o["hubo_log"] == "si"
    assert pathlib.Path(o["carpeta_export"]).name == "AB123"
    hecho = tmp_path / "salida" / "AB123"
    assert sorted(p.name for p in hecho.iterdir()) == [
        "AB123-L05-A-MatA.txt", "AB123-L05-A-gum.stl", "AB123-L05-A-tooth.ply", "AB123-L05-A.stl",
    ]
    # El de ToothFORM, byte a byte (66 bytes, CRLF, dos identidades).
    assert (hecho / "AB123-L05-A-MatA.txt").read_bytes() == b"1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1\r\n" * 2
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
    r = _exportar(LocalFsAdapter(root=tmp_path), archivos=["caso/AB123-L05-A.stl", "caso/AB123-U05-A.stl"],
                   salida="salida")

    assert r.status == "err" and r.error_kind == "modelo_fallido"
    o = r.outputs
    assert o["exportados"] == ["AB123-L05-A"] and o["faltantes"] == ["AB123-U05-A"]
    assert o["modelos_fallidos"][0]["modelo"] == "AB123-U05-A"
    # Un archivo que no es una malla es "Failed to preprocess", como en ToothFORM.
    assert o["modelos_fallidos"][0]["motivo"].startswith("Failed to preprocess (")
    assert "AB123-U05-A    Failed to preprocess" in o["log_texto"]
    assert (tmp_path / "salida" / "AB123" / "AB123-L05-A.stl").exists()
    # El que falló no deja MatA, como ToothFORM, que lo escribe por modelo exportado.
    assert not (tmp_path / "salida" / "AB123" / "AB123-U05-A-MatA.txt").exists()


def test_no_toma_una_salida_como_entrada():
    fs = FakeFs(files={"D:/caso/AB123/AB123-L05-A.stl": "x", "D:/caso/AB123/AB123-L05-A-gum.stl": "x"})
    r = _exportar(fs, carpeta="D:/caso/AB123", salida="D:/salida")
    assert r.status == "err" and r.error_kind == "entrada_exportada"


def test_parametros_desconocidos_o_que_no_son_numeros(monkeypatch):
    fs = FakeFs(files={"D:/caso/AB123-L05-A.stl": "x"})
    r = _exportar(fs, carpeta="D:/caso", salida="D:/salida", ToothMinVolume="30")
    assert r.status == "err" and "ToothMinVolume" in r.message

    _con_librerias()
    r = _exportar(fs, carpeta="D:/caso", salida="D:/salida", QrSize="grande")
    assert r.status == "err" and "QrSize" in r.message


def test_sin_las_librerias_dice_cual_falta(monkeypatch):
    for nombre in [m for m in sys.modules if m.startswith("plugins.keep_form.enkukador")]:
        monkeypatch.delitem(sys.modules, nombre)
    monkeypatch.setitem(sys.modules, "manifold3d", None)
    monkeypatch.setitem(sys.modules, "shapely", None)
    fs = FakeFs(files={"D:/caso/AB123-L05-A.stl": "x"})
    r = _exportar(fs, carpeta="D:/caso", salida="D:/salida")
    assert r.status == "err" and r.error_kind == "librerias"
    assert "requirements.txt" in r.message


def test_carpeta_sin_modelos_y_carpeta_o_archivos():
    fs = FakeFs(files={"D:/caso/notas.txt": "x"})
    assert _exportar(fs, carpeta="D:/caso", salida="D:/s").status == "err"
    assert _exportar(fs, salida="D:/s").status == "err"


def test_el_manifest_se_puede_imprimir_en_una_consola_de_windows():
    import json

    plugin = build_plugin()
    texto = json.dumps(
        {"plugin": plugin.manifest.doc, "tools": [t.manifest.to_dict() for t in plugin.tools]},
        ensure_ascii=False, default=str,
    )
    assert not sorted({c for c in texto if not c.encode("cp1252", "ignore")})


def test_saca_las_partes_sueltas_y_deja_la_pieza_mas_grande():
    _con_librerias()
    import trimesh

    from plugins.keep_form.plugin import _una_pieza

    grande = trimesh.creation.box((10, 10, 10))
    suelta = trimesh.creation.box((0.1, 0.1, 0.1))
    suelta.apply_translation([20, 0, 0])
    logs = []

    class Ctx:
        def log(self, m, *_a):
            logs.append(m)

    pieza = _una_pieza(Ctx(), "AB123-L05-A", trimesh.util.concatenate([grande, suelta]))
    assert len(pieza.faces) == len(grande.faces) and pieza.is_watertight
    assert "1 parte(s) suelta(s), 12 triángulos" in logs[0]
    assert _una_pieza(Ctx(), "x", grande) is grande


def test_si_rtree_no_carga_corta_una_vez_con_el_motivo(monkeypatch):
    """Lo de BOT.1: sin MSVCP140.DLL, rtree se cae con OSError al importarse."""
    _con_librerias()
    import builtins

    original = builtins.__import__

    def sin_dll(nombre, *args, **kwargs):
        if nombre.startswith("rtree"):
            raise OSError("could not find or load spatialindex_c-64.dll")
        return original(nombre, *args, **kwargs)

    for nombre in [m for m in sys.modules if m.startswith("rtree")]:
        monkeypatch.delitem(sys.modules, nombre)
    monkeypatch.setattr(builtins, "__import__", sin_dll)
    fs = FakeFs(files={"D:/caso/AB123-L05-A.stl": "x"})
    r = _exportar(fs, carpeta="D:/caso", salida="D:/salida")
    assert r.status == "err" and r.error_kind == "librerias"
    assert "MSVCP140" in r.message and "Visual C++" in r.message


def test_unir_piezas_que_se_tocan_por_una_arista():
    """BX087 U01/U10/U15, BX059 U02: 'la unión de las piezas no quedó cerrada'.
    manifold3d la da cerrada; juntar vértices la pellizcaba."""
    _con_librerias()
    import trimesh

    from plugins.keep_form.enkukador import hueco

    a = trimesh.creation.box((10, 10, 10))
    a.apply_translation([5, 5, 5])
    b = trimesh.creation.box((4, 4, 10))
    b.apply_translation([12, 12, 5])  # toca a 'a' sólo por la arista x=10, y=10
    unida = hueco.unir(trimesh.util.concatenate([a, b]))
    assert unida.is_watertight
    assert unida.volume == pytest.approx(1000 + 160)


def test_si_la_costura_falla_usa_el_plan_b(tmp_path, arcada_stl, monkeypatch):
    """BY184 U03-A: 'la costura no quedó cerrada' perdía el modelo."""
    import trimesh

    from plugins.keep_form.enkukador import hueco

    def costura_rota(*_a, **_k):
        raise ValueError("la costura no quedó cerrada")

    monkeypatch.setattr(hueco, "coser", costura_rota)
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-L05-A.stl").write_bytes(arcada_stl)
    r = _exportar(LocalFsAdapter(root=tmp_path), carpeta="caso", salida="salida")

    assert r.status == "ok", r.message
    pieza = trimesh.load(tmp_path / "salida" / "AB123" / "AB123-L05-A.stl")
    assert pieza.is_watertight and pieza.body_count == 1


def test_un_error_viejo_de_rtree_guardado_en_trimesh_se_reemplaza(monkeypatch):
    """Si rtree falló una vez, trimesh se quedaba con el error aunque se arreglara la DLL."""
    _con_librerias()
    import trimesh.base
    import trimesh.path.polygons
    from rtree.index import Index
    from trimesh.exceptions import ExceptionWrapper

    from plugins.keep_form.plugin import _rtree_listo

    viejo = ExceptionWrapper(OSError("could not find or load spatialindex_c-64.dll"))
    monkeypatch.setattr(trimesh.base, "Index", viejo)
    monkeypatch.setattr(trimesh.path.polygons, "Index", viejo)
    logs = []

    class Ctx:
        def log(self, m, *_a):
            logs.append(m)

    assert _rtree_listo(Ctx()) is None
    assert trimesh.base.Index is Index and trimesh.path.polygons.Index is Index
    assert len(logs) == 2 and "error viejo de rtree" in logs[0]


def test_paralelo_da_lo_mismo_y_en_el_orden_de_la_entrada(tmp_path, arcada_stl):
    (tmp_path / "caso").mkdir()
    for nombre in ("AB123-L05-A", "AB123-L06-A", "AB123-L07-A"):
        (tmp_path / "caso" / f"{nombre}.stl").write_bytes(arcada_stl)
    logs = []
    r = _exportar(LocalFsAdapter(root=tmp_path), logs=logs, carpeta="caso", salida="salida", paralelo=2)

    assert r.status == "ok", r.message
    assert r.outputs["exportados"] == ["AB123-L05-A", "AB123-L06-A", "AB123-L07-A"]
    assert r.outputs["log_texto"].splitlines()[:3] == [
        "AB123-L05-A    Export successfully", "AB123-L06-A    Export successfully",
        "AB123-L07-A    Export successfully",
    ]
    assert any("2 a la vez" in m for m in logs)
    import re

    assert sorted(m.split(" ")[0] for m in logs if re.match(r"\d+/\d+ ", m)) == ["1/3", "2/3", "3/3"]


def test_si_no_se_puede_vaciar_sale_maciza_como_toothform(tmp_path, arcada_stl, monkeypatch):
    """'Fail to hollow, changed to solid': ToothFORM la exporta igual y su
    documento dice que no hace falta corregir nada."""
    import trimesh

    from plugins.keep_form.enkukador import hueco

    def sin_cavidad(*_a, **_k):
        raise RuntimeError("la cavidad no cerró")

    monkeypatch.setattr(hueco, "cavidad", sin_cavidad)
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-L05-A.stl").write_bytes(arcada_stl)
    r = _exportar(LocalFsAdapter(root=tmp_path), carpeta="caso", salida="salida")

    assert r.status == "ok", r.message
    assert r.outputs["exportados"] == ["AB123-L05-A"]
    assert "AB123-L05-A    Fail to hollow, changed to solid" in r.outputs["log_texto"]
    hecho = tmp_path / "salida" / "AB123"
    assert (hecho / "AB123-L05-A-MatA.txt").exists()
    maciza = trimesh.load(hecho / "AB123-L05-A.stl")
    assert maciza.is_watertight and maciza.body_count == 1


def test_si_fallan_la_placa_o_el_qr_es_failed_to_merge(tmp_path, arcada_stl, monkeypatch):
    from plugins.keep_form.enkukador import toothform

    def qr_roto(*_a, **_k):
        raise RuntimeError("no se pudo armar el QR")

    monkeypatch.setattr(toothform, "_qr_modulos", qr_roto)
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-L05-A.stl").write_bytes(arcada_stl)
    r = _exportar(LocalFsAdapter(root=tmp_path), carpeta="caso", salida="salida")

    assert r.status == "err" and r.error_kind == "modelo_fallido"
    assert r.outputs["modelos_fallidos"][0]["motivo"].startswith("Failed to merge (")
    assert not (tmp_path / "salida" / "AB123" / "AB123-L05-A.stl").exists()


def test_clip_limit_si_no_queda_lugar_para_la_pinza_alrededor_del_qr(tmp_path, arcada_stl):
    """'Clip limit exceeded': la pinza toma la pieza por el QR. La arcada de
    prueba deja ~4,4 mm libres: pasa con el LimitX de siempre y no con 10."""
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-L05-A.stl").write_bytes(arcada_stl)
    logs = []
    r = _exportar(LocalFsAdapter(root=tmp_path), logs=logs, carpeta="caso", salida="sale")
    assert r.status == "ok", r.message
    assert any("QR libre" in m for m in logs)

    r = _exportar(LocalFsAdapter(root=tmp_path), carpeta="caso", salida="no-sale", LimitX="10")
    assert r.status == "err" and r.error_kind == "modelo_fallido"
    assert r.outputs["modelos_fallidos"][0]["motivo"] == "Clip limit exceeded"
    assert "AB123-L05-A    Clip limit exceeded" in r.outputs["log_texto"]
    # Como ToothFORM: sin pieza ni MatA, pero con -gum y -tooth.
    hecho = tmp_path / "no-sale" / "AB123"
    assert sorted(p.name for p in hecho.iterdir()) == ["AB123-L05-A-gum.stl", "AB123-L05-A-tooth.ply"]
