"""
`archivos.renombrar_lote`: una lista de rutas, un regex con grupos y un nombre
nuevo armado con ellos. Con el filesystem falso del núcleo.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from backend.core.contract import ToolContext  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeFs  # noqa: E402

from plugins.archivos.plugin import build_plugin  # noqa: E402

CASO = "C:/casos/BY726_BY726/2026-09-19-0703"
NOMBRES = [
    "Template1_lower_Stage1_step1_BY726_BY726.stl",
    "Template1_upper_Stage1_step1_BY726_BY726.stl",
    "Template1-0_lower_Stage1_step0_BY726_BY726.stl",
    "Template1-0_upper_Stage1_step0_BY726_BY726.stl",
    "Template2_lower_Stage1_step2_BY726_BY726.stl",
    "Template2_upper_Stage1_step2_BY726_BY726.stl",
]
RUTAS = [f"{CASO}/{n}" for n in NOMBRES]
PATRON = r"^Template\d+(-0)?_(?P<arcada>lower|upper)_Stage\d+_step(?P<step>\d+)_(?P<caso>[A-Z0-9]+)_.*\.stl$"


def _correr(archivos=None, log=None, **params):
    fs = FakeFs(files=dict.fromkeys(RUTAS, "x") if archivos is None else archivos)
    reg = ToolRegistry(adapters={"fs": fs})
    reg._add_plugin("archivos", "plugins.archivos:PLUGIN", build_plugin())
    anotar = (lambda m, level="info": log.append(m)) if log is not None else (lambda *_a, **_k: None)
    completos = {"rutas": RUTAS, "patron": PATRON, **params}

    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params(completos, {})
        return ToolContext(
            run_id="run-test", case_id="BY726", params=declarados, extras=extras,
            config={}, context={}, log=anotar, ports=ports or {},
        )

    return reg.execute("archivos.renombrar_lote", factory), fs


def test_renombra_cada_uno_con_los_grupos_del_patron_en_su_carpeta():
    r, fs = _correr(nombre_nuevo="<caso>_<arcada>_<step:02>.stl")
    assert r.status == "ok", r.message
    nuevos = sorted(pathlib.PurePosixPath(p).name for p in r.outputs["rutas"])
    assert nuevos == [
        "BY726_lower_00.stl", "BY726_lower_01.stl", "BY726_lower_02.stl",
        "BY726_upper_00.stl", "BY726_upper_01.stl", "BY726_upper_02.stl",
    ]
    assert not any(ruta in fs.files for ruta in RUTAS)
    primero = r.outputs["variables"][0]
    assert (primero["arcada"], primero["step"], primero["caso"]) == ("lower", "1", "BY726")


def test_con_destino_y_copiar_los_deja_en_otra_carpeta_y_no_toca_los_originales():
    r, fs = _correr(nombre_nuevo="<arcada>_<step>.stl", destino="D:/salida/BY726", modo="copiar")
    assert r.status == "ok", r.message
    assert all(p.startswith("D:/salida/BY726/") for p in r.outputs["rutas"])
    assert all(ruta in fs.files for ruta in RUTAS)
    assert r.outputs["carpeta"] == "D:/salida/BY726"


def test_si_uno_no_coincide_no_toca_ninguno():
    archivos = {**dict.fromkeys(RUTAS, "x"), f"{CASO}/notas.txt": ""}
    r, fs = _correr(archivos, rutas=RUTAS + [f"{CASO}/notas.txt"], nombre_nuevo="<caso>_<arcada>_<step>.stl")
    assert r.status == "err" and "notas.txt" in r.message
    assert all(ruta in fs.files for ruta in RUTAS)


def test_saltear_sin_coincidencia_sigue_con_los_demas():
    archivos = {**dict.fromkeys(RUTAS, "x"), f"{CASO}/notas.txt": ""}
    r, fs = _correr(archivos, rutas=RUTAS + [f"{CASO}/notas.txt"], nombre_nuevo="<caso>_<arcada>_<step>.stl",
                    saltear_sin_coincidencia=True)
    assert r.status == "ok" and r.outputs["cantidad"] == 6
    assert r.outputs["salteados"] == [f"{CASO}/notas.txt"] and f"{CASO}/notas.txt" in fs.files


def test_dos_que_terminan_igual_no_se_pisan():
    # Sin <step>, lower de step1 y lower de step2 quedarían con el mismo nombre.
    r, fs = _correr(nombre_nuevo="<caso>_<arcada>.stl")
    assert r.status == "err" and "terminarían los dos" in r.message
    assert all(ruta in fs.files for ruta in RUTAS)


def test_un_archivo_que_ya_existe_frena_salvo_que_se_pida_pisar():
    archivos = {**dict.fromkeys(RUTAS, "x"), f"{CASO}/BY726_lower_1.stl": "viejo"}
    r, _ = _correr(archivos, nombre_nuevo="<caso>_<arcada>_<step>.stl")
    assert r.status == "err" and "ya existe" in r.message

    r, fs = _correr(archivos, nombre_nuevo="<caso>_<arcada>_<step>.stl", pisar=True)
    assert r.status == "ok", r.message
    assert fs.files[f"{CASO}/BY726_lower_1.stl"] == "x"


def test_un_grupo_que_el_patron_no_tiene_es_err_con_los_que_hay():
    r, _ = _correr(nombre_nuevo="<paciente>.stl")
    assert r.status == "err" and "<paciente>" in r.message and "<arcada>" in r.message


def test_acepta_una_sola_ruta_y_grupos_por_numero():
    r, _ = _correr(rutas=[RUTAS[2]], patron=r"^(\w+?)-0_(lower|upper)", nombre_nuevo="<2>_<1>.stl")
    assert r.status == "ok", r.message
    assert r.outputs["rutas"] == [f"{CASO}/lower_Template1.stl"]


def test_rutas_vacias_o_patron_roto_son_err():
    r, _ = _correr(rutas=[], nombre_nuevo="x.stl")
    assert r.status == "err" and "vacío" in r.message
    r, _ = _correr(patron="(", nombre_nuevo="x.stl")
    assert r.status == "err" and "no es una expresión regular" in r.message


def test_el_manifest_se_puede_imprimir_en_una_consola_de_windows():
    """
    La app valida un plugin corriendo `python -m backend.core plugins --json`
    en otro proceso, y en Windows ese proceso imprime el catálogo en cp1252.
    Un carácter que cp1252 no tiene —una flecha → en una ayuda— rompe el
    print y el plugin entero "no carga". Le pasó a esta misma ayuda.
    """
    import json

    plugin = build_plugin()
    texto = json.dumps(
        {"plugin": plugin.manifest.doc, "tools": [t.manifest.to_dict() for t in plugin.tools]},
        ensure_ascii=False, default=str,
    )
    malos = sorted({c for c in texto if not c.encode("cp1252", "ignore")})
    assert not malos, f"caracteres que la consola de Windows no imprime: {malos}"
