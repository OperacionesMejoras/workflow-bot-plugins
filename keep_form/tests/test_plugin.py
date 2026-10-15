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


def test_el_pasillo_del_qr_es_solo_aviso_y_no_rechaza(tmp_path, arcada_stl):
    """El criterio y el marco son los de ToothFORM, pero hasta que el
    proveedor lo confirme, con poco lugar avisa en el log y exporta igual."""
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-L05-A.stl").write_bytes(arcada_stl)
    logs = []
    r = _exportar(LocalFsAdapter(root=tmp_path), logs=logs, carpeta="caso", salida="sale", LimitX="10")

    assert r.status == "ok", r.message
    assert r.outputs["exportados"] == ["AB123-L05-A"]
    assert "Clip limit" not in r.outputs["log_texto"]
    assert any("pasillo QR" in m for m in logs)
    assert any("sólo aviso" in m for m in logs)
    assert (tmp_path / "sale" / "AB123" / "AB123-L05-A.stl").exists()


def test_el_marco_es_el_de_toothform():
    """Arcada simétrica, cerrada atrás por una recta: esa recta va paralela a Y,
    los incisivos a +X, y = 0 en el medio de la arcada, base en z = -0,5 y el
    frente a QR_FRENTE_X (acá manda la regla del frente: la cara lingual
    quedaría más lejos que QR_LINGUAL_X)."""
    _con_librerias()
    import numpy as np

    from plugins.keep_form.enkukador import toothform as tf

    malla = _arcada_con_dientes()
    # La U extruida tiene la tapa igual a la base; achicarla un poco para que
    # la base sea la de abajo, como en un escaneo.
    malla.vertices[malla.vertices[:, 2] > 13.9, :2] *= 0.99
    M, curva = tf.marco(malla)
    v = malla.copy().apply_transform(M).vertices
    assert abs(v[:, 2].min() + 0.5) < 1e-6
    assert abs(v[:, 1].mean()) < 0.05  # simétrica: y=0 en el medio
    atras = v[v[:, 0] < v[:, 0].min() + 1e-3]  # la recta de atrás, toda al mismo x
    assert np.ptp(atras[:, 1]) > 30
    frente = v[(np.abs(v[:, 1]) < 0.5) & (v[:, 2] < 1.0)]  # el anillo de la base, adelante
    assert abs(frente[:, 0].max() - tf.QR_FRENTE_X) < 0.05
    assert curva[:, 0].max() > 20 and abs(curva[np.argmax(curva[:, 0]), 1]) < 0.5


def test_pasillo_qr_es_el_criterio_de_toothform():
    """Con QrSize 15: w = 8; ningún vértice en x <= 2 con |y| < 8 + LimitX."""
    _con_librerias()
    import numpy as np
    import trimesh

    from plugins.keep_form.enkukador import toothform as tf

    p = tf.Parametros()
    def con(*puntos):
        return trimesh.Trimesh(np.array(puntos, float), [[0, 1, 2]], process=False)

    # Un vértice en x=2, y=10.5: justo en el borde con LimitX 2.5.
    assert tf.pasillo_qr(con((2, 10.5, 1), (30, 30, 1), (30, -30, 1)), p) == 2.5
    # Más adelante que x=2 no cuenta (ahí está el frente de la arcada).
    assert tf.pasillo_qr(con((2.1, 0, 1), (30, 30, 1), (30, -30, 1)), p) == float("inf")
    # Detrás, del lado -Y, también cuenta.
    assert tf.pasillo_qr(con((-20, -9, 1), (30, 30, 1), (30, -30, 1)), p) == 1.0


def _arcada_que_invade_el_pasillo(alto_saliente=6.0):
    """La arcada de arriba con un saliente en la pared palatina de un lado,
    que entra en el pasillo de la pinza: en el marco de ToothFORM queda en
    x -4..0 con |y| desde 10,2 (pasillo 2,2 < LimitX 2,5), de `alto_saliente`
    de alto desde la base."""
    import trimesh
    from shapely.geometry import box

    malla = _arcada_con_dientes()
    malla.vertices[malla.vertices[:, 2] > 13.9, :2] *= 0.99
    saliente = trimesh.creation.extrude_polygon(box(10.2, -3.0, 17.5, 1.0), alto_saliente, engine="manifold")
    piezas = malla.split(only_watertight=False)
    encia = max(piezas, key=lambda q: q.area)
    encia = encia.union(saliente, engine="manifold")
    return trimesh.util.concatenate([encia, *[q for q in piezas if q is not max(piezas, key=lambda q: q.area)]])


def test_abre_el_pasillo_en_la_encia_cuando_no_llega_a_limitx(tmp_path):
    """Con AbrirPasillo=1, si el pasillo no llega a LimitX le saca a la encía
    una cuña desde la altura del QR, inclinada 2°, hasta LimitX + margen; los
    dientes no se tocan, lo dice en el log y -gum.stl sale con la muesca."""
    _con_librerias()
    import numpy as np
    import trimesh

    from plugins.keep_form.enkukador import toothform as tf

    malla = _arcada_que_invade_el_pasillo()
    p = tf.Parametros()
    v = malla.copy().apply_transform(tf.marco(malla, p)[0]).vertices
    assert tf.pasillo_qr(trimesh.Trimesh(v, process=False), p) < p.LimitX  # el saliente invade

    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-U05-A.stl").write_bytes(malla.export(file_type="stl"))
    logs = []
    r = _exportar(LocalFsAdapter(root=tmp_path), logs=logs, carpeta="caso", salida="sale", AbrirPasillo="1")

    assert r.status == "ok", r.message
    assert r.outputs["exportados"] == ["AB123-U05-A"]
    aviso = next(m for m in logs if "muesca" in m)
    assert "lado -Y" in aviso or "lado +Y" in aviso
    assert "queda 2.8" in aviso and "desde z=3.2" in aviso
    assert any("pasillo QR 2.2" in m for m in logs)  # el del log sigue siendo el de antes
    gum = trimesh.load(tmp_path / "sale" / "AB123" / "AB123-U05-A-gum.stl", force="mesh")
    g = gum.vertices
    w = (p.QrSize + 1) / 2
    arriba = g[(g[:, 0] <= 10 - w) & (g[:, 2] > p.PasilloPiso + 0.01)]
    assert np.abs(arriba[:, 1]).min() - w >= p.LimitX + p.PasilloMargen - 1e-3
    abajo = g[(g[:, 0] <= 10 - w) & (g[:, 2] < p.PasilloPiso - 0.01) & (g[:, 2] >= 0)]
    assert np.abs(abajo[:, 1]).min() - w < p.LimitX  # por debajo del piso queda como estaba
    # La cara se abre hacia arriba (2°): en lo alto de la muesca (z = 5,5, el
    # tope del saliente) hay 2,3 mm x tan 2° = 0,08 más de lugar que en el piso.
    cara = g[(g[:, 0] <= 10 - w) & (g[:, 0] > -4.5) & (g[:, 2] >= p.PasilloPiso - 0.01) & (np.abs(g[:, 1]) < 11.0)]
    piso, tope = cara[cara[:, 2] < p.PasilloPiso + 0.01], cara[cara[:, 2] > 5.4]
    assert len(piso) and len(tope)
    assert abs(np.abs(piso[:, 1]).max() - 10.8) < 0.01
    assert abs(np.abs(tope[:, 1]).max() - 10.8 - 2.3 * np.tan(np.radians(2))) < 0.01


def test_la_muesca_tiene_limites_y_si_no_se_puede_es_clip_limit(tmp_path):
    """Si falta más que la tolerancia, si la cuña no sale de la pared antes de
    PasilloAltoMax, o si queda cerca de un diente, no se abre: es 'Clip limit
    exceeded' con el motivo. Y apagado (por defecto) sigue siendo sólo aviso."""
    _con_librerias()
    stl = _arcada_que_invade_el_pasillo().export(file_type="stl")
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-U05-A.stl").write_bytes(stl)

    def corrida(**params):
        logs = []
        r = _exportar(LocalFsAdapter(root=tmp_path), logs=logs, carpeta="caso", salida="sale", **params)
        return r, logs

    r, logs = corrida(AbrirPasillo="1", PasilloTolerancia="0.1")
    assert r.status == "err" and r.outputs["modelos_fallidos"][0]["motivo"].startswith("Clip limit exceeded (")
    assert "PasilloTolerancia" in r.outputs["modelos_fallidos"][0]["motivo"]
    r, _ = corrida(AbrirPasillo="1", PasilloAltoMax="1")
    assert r.status == "err" and "PasilloAltoMax" in r.outputs["modelos_fallidos"][0]["motivo"]
    r, _ = corrida(AbrirPasillo="1", PasilloDiente="50")
    assert r.status == "err" and "diente" in r.outputs["modelos_fallidos"][0]["motivo"]
    r, logs = corrida()
    assert r.status == "ok" and not any("muesca" in m for m in logs) and any("sólo aviso" in m for m in logs)


def _solido_unico():
    """La arcada de arriba fundida en un solo shell (Type3): encía y dientes unidos."""
    import trimesh

    malla = _arcada_con_dientes()
    malla.vertices[malla.vertices[:, 2] > 13.9, :2] *= 0.99
    piezas = malla.split(only_watertight=False)
    encia = max(piezas, key=lambda q: q.area)
    for d in piezas:
        if d is not encia:
            encia = encia.union(d, engine="manifold")
    return encia


def test_type3_un_solo_shell_mas_pts(tmp_path):
    """Type3 (leído del binario, sin par real todavía): un STL de un solo shell
    y <modelo>.pts al lado. Sale la pieza, -gum.stl con el shell entero, sin
    -tooth.ply, y -pts.pts con los puntos llevados al marco de la pieza con la
    misma matriz que el modelo. Sin el .pts: 'Pts file not found!'."""
    _con_librerias()
    import numpy as np
    import trimesh

    from plugins.keep_form.enkukador import toothform as tf

    solido = _solido_unico()
    assert len(solido.split(only_watertight=False)) == 1
    # Un pedacito suelto de menos de 100 caras: ToothFORM lo descarta al cargar.
    basura = trimesh.creation.box((1, 1, 1))
    basura.apply_translation([60, 60, 5])
    entrada = trimesh.util.concatenate([solido, basura])
    (tmp_path / "caso").mkdir()
    (tmp_path / "caso" / "AB123-U05-A.stl").write_bytes(entrada.export(file_type="stl"))
    # Puntos "x y z" (con campos de más, que se ignoran) y una línea con tabulaciones.
    v = solido.vertices
    puntos = np.array([v[0], v[len(v) // 2], [1.5, -2.25, 3.0]])
    texto = f"{puntos[0,0]} {puntos[0,1]} {puntos[0,2]} 7 etiqueta\n\n{puntos[1,0]}\t{puntos[1,1]}\t{puntos[1,2]}\n1.5 -2.25 3\n"
    (tmp_path / "caso" / "AB123-U05-A.pts").write_text(texto)
    logs = []
    r = _exportar(LocalFsAdapter(root=tmp_path), logs=logs, carpeta="caso", salida="sale", tipo="Type3")

    assert r.status == "ok", r.message
    assert r.outputs["exportados"] == ["AB123-U05-A"]
    salida = tmp_path / "sale" / "AB123"
    assert (salida / "AB123-U05-A.stl").exists() and (salida / "AB123-U05-A-gum.stl").exists()
    assert not (salida / "AB123-U05-A-tooth.ply").exists()
    gum = trimesh.load(salida / "AB123-U05-A-gum.stl", force="mesh")
    assert gum.bounds[1][2] > 12  # el shell entero, con los "dientes", cortado en z=0
    assert gum.bounds[0][2] > -1e-6 and gum.bounds[1][0] < 59  # sin la basura
    M, _ = tf.marco(solido)
    esperado = puntos @ M[:3, :3].T + M[:3, 3]
    lineas = (salida / "AB123-U05-A-pts.pts").read_bytes().split(b"\r\n")
    assert lineas[-1] == b"" and len(lineas) == 4
    leido = np.array([[float(c) for c in l.split(b" ")] for l in lineas[:3]])
    assert np.abs(leido - esperado).max() < 1e-3  # 6 cifras significativas
    assert any("3 puntos del .pts" in m for m in logs)

    (tmp_path / "caso" / "AB123-U05-A.pts").unlink()
    r = _exportar(LocalFsAdapter(root=tmp_path), carpeta="caso", salida="sale2", tipo="Type3")
    assert r.status == "err"
    assert r.outputs["modelos_fallidos"][0]["motivo"].startswith("Pts file not found!")
    assert "AB123-U05-A    Pts file not found!" in r.outputs["log_texto"]
    r = _exportar(LocalFsAdapter(root=tmp_path), carpeta="caso", salida="sale3", tipo="Type2")
    assert r.status == "err" and "Type2" in r.message


def test_leer_pts_como_toothform():
    _con_librerias()
    from plugins.keep_form.enkukador import toothform as tf

    p = tf.leer_pts("1 2 3\r\n4\t5\t6\t7\n  8   9   10 \nsolo dos 1\n1.5e1 x 2\n")
    # "solo dos 1" tiene 3 campos: atof de lo que no es número da 0, como en ToothFORM
    assert p.tolist() == [[1, 2, 3], [4, 5, 6], [8, 9, 10], [0.0, 0.0, 1.0], [15.0, 0.0, 2.0]]
