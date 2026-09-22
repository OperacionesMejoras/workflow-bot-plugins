"""
Tests del plugin `toothform`.

Sin ToothFORM, sin disco y sin reloj de verdad: el filesystem, el proceso, la
ventana y el tiempo son los fakes del núcleo. Lo que se prueba es la
semántica que el flujo TOOTHFORM CNC4 espera de cada tool por sus aristas
—ok / err / loop—, no la app.

Corre con `python -m pytest plugins/toothform` (plugins/ no está en la
invocación por defecto del repo).
"""

from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import replace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from backend.core.contract import ToolContext  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeClock, FakeFs, FakeProcess, FakeWindow  # noqa: E402

from plugins.toothform.plugin import build_plugin  # noqa: E402

SALIDA = "D:/salida"
STL = "D:/casos/QATF001/stl"
EXE = "D:/4in1/Toothform-20260518/Toothform.exe"
LOG_OK = "QATF001-L01-A    Export successfully\n\nTotal 1 models, of which 1 succeeded and 0 failed\n"
LOG_FALLA = "222222-U01-A    Failed to hollow\n\nTotal 2 models, of which 1 succeeded and 1 failed\n"


class FsConMtimes(FakeFs):
    """`FakeFs` no fecha los archivos; `check_log` decide por la fecha, así que acá se declara por nombre."""

    def __init__(self, files=None, dirs=(), mtimes=None) -> None:
        super().__init__(files, dirs)
        self.mtimes = dict(mtimes or {})

    def stat(self, path):
        info = super().stat(path)
        return replace(info, modified_at=self.mtimes.get(info.name, 0.0))


class ToothformFalso(FakeProcess):
    """
    Un ToothFORM por cmd guionado: al correr deja (o no) un log en la carpeta
    de salida, como hace el real. El exit code es 2 siempre, como el real
    también —lo que el tool no puede usar para decidir.
    """

    def __init__(self, fs, log: str | None, nombre="20260914.13.32.08.log", timed_out=False) -> None:
        super().__init__()
        self.stub(EXE, exit_code=2)
        self.fs, self.log, self.nombre, self.timed_out = fs, log, nombre, timed_out

    def run(self, command, *, cwd=None, timeout=None, env=None):
        resultado = super().run(command, cwd=cwd, timeout=timeout, env=env)
        if self.log is not None:
            self.fs.write_text(f"{SALIDA}/{self.nombre}", self.log)
            self.fs.mtimes[self.nombre] = 1_000.0
        return replace(resultado, timed_out=self.timed_out)


def _ctx_factory(node_params, context=None):
    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params(node_params, {})
        return ToolContext(
            run_id="run-test", case_id="QATF001",
            params=declarados, extras=extras, config={}, context=context or {},
            log=lambda *_a, **_k: None, ports=ports or {},
        )

    return factory


def _registry(*, fs=None, clock=None, window=None, process=None) -> ToolRegistry:
    reg = ToolRegistry(adapters={
        "fs": fs or FsConMtimes(dirs=(SALIDA,)),
        "clock": clock or FakeClock(),
        "window": window or FakeWindow({"Toothform": {}}),
        "process": process or FakeProcess(),
    })
    reg._add_plugin("toothform", "plugins.toothform:PLUGIN", build_plugin())
    return reg


def _check_log(fs, clock=None, **params):
    return _registry(fs=fs, clock=clock).execute(
        "toothform.check_log", _ctx_factory({"carpeta": SALIDA, **params}),
    )


def _exportar(fs=None, process=None, **params):
    fs = fs or FsConMtimes(dirs=(STL, SALIDA))
    process = process or ToothformFalso(fs, LOG_OK)
    resultado = _registry(fs=fs, process=process).execute(
        "toothform.exportar",
        _ctx_factory({"ejecutable": EXE, "carpeta": STL, "salida": SALIDA, **params}),
    )
    return resultado, fs, process


# ── El plugin carga limpio ──────────────────────────────────────────────


def test_el_plugin_carga_con_sus_cuatro_ports():
    reg = _registry()
    assert not reg.errors
    plugin = next(p for p in reg.plugins if p.name == "toothform")
    assert set(plugin.ports) == {"process", "fs", "clock", "window"}
    assert set(reg.tool_ids) == {
        "toothform.exportar", "toothform.add_qr", "toothform.check_log", "toothform.toothcam_enviar",
    }


# ── exportar: JSON → proceso → log ──────────────────────────────────────


def test_exportar_escribe_el_json_que_toothform_espera_y_corre_el_exe_con_el():
    resultado, fs, process = _exportar(tipo="Type1")

    assert resultado.status == "ok", resultado.message
    ruta_config = resultado.outputs["config"]
    assert ruta_config == f"{SALIDA}/toothform-QATF001.json"
    config = json.loads(fs.files[ruta_config])
    assert config["FilesToProcess"] == {"OpenFolder": STL, "Type": "Type1"}
    # Con las dos rutas iguales el export queda todo junto en <salida>\<dato>, como en la UI.
    assert config["General"] == {"ExportPrintPath": SALIDA, "ExportFilePath": SALIDA}
    assert config["ParameterSettings"]["LimitX"] == "2.5"
    assert process.calls == [{"command": [EXE, ruta_config], "cwd": "D:/4in1/Toothform-20260518", "timeout": 900.0}]


def test_exportar_lee_el_log_y_expone_lo_mismo_que_check_log_mas_la_carpeta_del_export():
    resultado, _fs, _p = _exportar()

    assert resultado.status == "ok"
    assert resultado.outputs["hubo_log"] == "si"
    assert resultado.outputs["log_file"] == "20260914.13.32.08.log"
    assert (resultado.outputs["exitosos"], resultado.outputs["fallidos"]) == (1, 0)
    assert resultado.outputs["checkLogResult"]["message"] == "QATF001-L01-A    Export successfully"
    assert resultado.outputs["exportados"] == ["QATF001-L01-A"]
    # El prefijo antes del primer "-" es la subcarpeta donde ToothFORM deja los archivos.
    assert resultado.outputs["carpeta_export"] == f"{SALIDA}/QATF001"


def test_exportar_con_fallas_en_el_log_es_err_con_el_log_para_copiarlo():
    fs = FsConMtimes(dirs=(STL, SALIDA))
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, LOG_FALLA))

    assert resultado.status == "err"
    assert resultado.outputs["hubo_log"] == "si"
    assert resultado.outputs["fallidos"] == 1
    assert resultado.outputs["checkLogResult"]["log_file"] == "20260914.13.32.08.log"


def test_exportar_sin_log_es_err_y_lo_dice_no_se_confunde_con_un_export_fallido():
    fs = FsConMtimes(dirs=(STL, SALIDA))
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, log=None))

    assert resultado.status == "err"
    assert "sin dejar un log" in resultado.message
    assert resultado.outputs["hubo_log"] == "no"
    assert resultado.outputs["log_file"] == ""


def test_exportar_ignora_un_log_viejo_que_ya_estaba_en_la_salida():
    fs = FsConMtimes(
        dirs=(STL,), files={f"{SALIDA}/20250101.09.00.00.log": LOG_OK},
        mtimes={"20250101.09.00.00.log": 10.0},
    )
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, log=None))

    assert resultado.status == "err"
    assert resultado.outputs["hubo_log"] == "no"


def test_exportar_con_timeout_es_err_sin_log():
    fs = FsConMtimes(dirs=(STL, SALIDA))
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, log=None, timed_out=True))

    assert resultado.status == "err"
    assert "no terminó" in resultado.message
    assert resultado.outputs["hubo_log"] == "no"


def test_exportar_corta_antes_de_correr_si_una_ruta_no_es_ascii():
    fs = FsConMtimes(dirs=("D:/instalación/stl", SALIDA))
    resultado, _fs, process = _exportar(fs=fs, carpeta="D:/instalación/stl")

    assert resultado.status == "err"
    assert "ASCII" in resultado.message
    assert process.calls == []


def test_exportar_acepta_parametros_de_toothform_como_extras_y_rechaza_los_desconocidos():
    resultado, fs, _p = _exportar(LimitX="3.0", ShellThickness="2.0")
    config = json.loads(fs.files[resultado.outputs["config"]])
    assert config["ParameterSettings"]["LimitX"] == "3.0"
    assert config["ParameterSettings"]["ShellThickness"] == "2.0"

    rechazo, _fs, process = _exportar(Limite="3.0")
    assert rechazo.status == "err"
    assert "Limite" in rechazo.message
    assert process.calls == []


def test_exportar_respeta_el_maximo_de_simultaneas_y_devuelve_el_turno():
    from plugins.toothform import plugin as modulo

    fs = FsConMtimes(dirs=(STL, SALIDA))
    process = ToothformFalso(fs, LOG_OK)
    factory = _ctx_factory({"ejecutable": EXE, "carpeta": STL, "salida": SALIDA, "timeout": 0.05})

    def con_config(declaracion, ports=None):
        ctx = factory(declaracion, ports)
        ctx._config[modulo.MAX_SIMULTANEOS] = 1
        return ctx

    # Otro export ocupa el único lugar: éste espera su timeout y lo dice, sin correr nada.
    assert modulo._TURNOS.tomar(1, 0)
    try:
        resultado = _registry(fs=fs, process=process).execute("toothform.exportar", con_config)
        assert resultado.status == "err"
        assert "turno" in resultado.message
        assert process.calls == []
    finally:
        modulo._TURNOS.soltar()

    # Con el lugar libre corre, y al terminar lo devuelve.
    resultado = _registry(fs=fs, process=process).execute("toothform.exportar", con_config)
    assert resultado.status == "ok"
    assert modulo._TURNOS.en_curso == 0


def test_exportar_rechaza_un_tipo_que_no_existe_y_una_carpeta_inexistente():
    assert "tipo" in _exportar(tipo="Type9")[0].message
    resultado, _fs, process = _exportar(carpeta="D:/no/existe")
    assert resultado.status == "err"
    assert process.calls == []


# ── exportar: sólo algunos archivos ─────────────────────────────────────
#
# ToothFORM por cmd sólo sabe cargar una carpeta entera (`OpenFolder` del
# JSON; no hay forma documentada de darle una lista), así que "exportar sólo
# estos" es copiarlos a una carpeta propia y apuntarlo ahí.


def test_exportar_archivos_sueltos_los_copia_a_una_carpeta_y_apunta_ahi():
    fs = FsConMtimes(files={f"{STL}/uno.stl": "a", f"{STL}/dos.stl": "b", f"{STL}/tres.stl": "c"}, dirs=(SALIDA,))
    process = ToothformFalso(fs, LOG_OK)
    resultado = _registry(fs=fs, process=process).execute(
        "toothform.exportar",
        _ctx_factory({"ejecutable": EXE, "salida": SALIDA, "archivos": [f"{STL}/uno.stl", f"{STL}/dos.stl"]}),
    )

    assert resultado.status == "ok"
    entrada = f"{SALIDA}/entrada-QATF001"
    assert json.loads(fs.read_text(resultado.outputs["config"]))["FilesToProcess"]["OpenFolder"] == entrada
    # Sólo los pedidos, con su nombre: el tercero no viaja.
    assert sorted(p.rsplit("/", 1)[-1] for p in fs.files if p.startswith(entrada)) == ["dos.stl", "uno.stl"]


def test_exportar_vacia_la_carpeta_de_entrada_antes_de_copiar():
    # Lo que quedó de una corrida anterior se exportaría de nuevo sin que nadie
    # lo pida, y eso se ve como un export "de más", no como una falla.
    fs = FsConMtimes(
        files={f"{STL}/uno.stl": "a", f"{SALIDA}/entrada-QATF001/viejo.stl": "x"},
        dirs=(SALIDA, f"{SALIDA}/entrada-QATF001"),
    )
    resultado = _registry(fs=fs, process=ToothformFalso(fs, LOG_OK)).execute(
        "toothform.exportar",
        _ctx_factory({"ejecutable": EXE, "salida": SALIDA, "archivos": [f"{STL}/uno.stl"]}),
    )

    assert resultado.status == "ok"
    assert f"{SALIDA}/entrada-QATF001/viejo.stl" not in fs.files


def test_exportar_con_carpeta_y_archivos_a_la_vez_es_err():
    fs = FsConMtimes(files={f"{STL}/uno.stl": "a"}, dirs=(STL, SALIDA))
    resultado = _registry(fs=fs).execute(
        "toothform.exportar",
        _ctx_factory({"ejecutable": EXE, "carpeta": STL, "salida": SALIDA, "archivos": [f"{STL}/uno.stl"]}),
    )

    assert resultado.status == "err" and "no las dos" in resultado.message


def test_exportar_sin_carpeta_ni_archivos_es_err():
    resultado = _registry(fs=FsConMtimes(dirs=(SALIDA,))).execute(
        "toothform.exportar", _ctx_factory({"ejecutable": EXE, "salida": SALIDA}),
    )

    assert resultado.status == "err" and "'carpeta' o 'archivos'" in resultado.message


def test_exportar_un_archivo_que_no_existe_es_err_antes_de_copiar_nada():
    fs = FsConMtimes(files={f"{STL}/uno.stl": "a"}, dirs=(SALIDA,))
    resultado = _registry(fs=fs).execute(
        "toothform.exportar",
        _ctx_factory({"ejecutable": EXE, "salida": SALIDA, "archivos": [f"{STL}/uno.stl", f"{STL}/no-esta.stl"]}),
    )

    assert resultado.status == "err" and "no-esta.stl" in resultado.message
    assert not any(p.startswith(f"{SALIDA}/entrada-") for p in fs.files)


# ── check_log: ok / err / loop ──────────────────────────────────────────


def test_sin_log_todavia_pide_reintentar_no_falla():
    resultado = _check_log(FsConMtimes(dirs=(SALIDA,)))

    assert resultado.status == "ok"
    assert resultado.loop is True


def test_un_log_viejo_es_de_otro_caso_y_no_cuenta():
    clock = FakeClock()
    fs = FsConMtimes(
        files={f"{SALIDA}/20250101.09.00.00.log": LOG_OK},
        mtimes={"20250101.09.00.00.log": clock.now() - 2_000},
    )

    resultado = _check_log(fs, clock, max_edad=900)

    assert resultado.loop is True


def test_un_log_fresco_sin_fallas_es_ok_y_expone_el_nombre():
    clock = FakeClock()
    fs = FsConMtimes(
        files={f"{SALIDA}/20250619.17.02.30.log": LOG_OK},
        mtimes={"20250619.17.02.30.log": clock.now() - 5},
    )

    resultado = _check_log(fs, clock)

    assert resultado.status == "ok" and resultado.loop is False
    assert resultado.outputs["log_file"] == "20250619.17.02.30.log"
    assert (resultado.outputs["exitosos"], resultado.outputs["fallidos"]) == (1, 0)
    # Lo que los nodos legacy leen como {checkLogResult.log_file} / {checkLogResult.message}.
    assert resultado.outputs["checkLogResult"] == {
        "log_file": "20250619.17.02.30.log",
        "message": "QATF001-L01-A    Export successfully",
    }


def test_un_log_con_fallas_es_err_pero_deja_el_log_para_copiarlo():
    clock = FakeClock()
    fs = FsConMtimes(
        files={f"{SALIDA}/20250619.17.02.30.log": LOG_FALLA},
        mtimes={"20250619.17.02.30.log": clock.now() - 5},
    )

    resultado = _check_log(fs, clock)

    assert resultado.status == "err"
    assert resultado.outputs["fallidos"] == 1
    assert resultado.outputs["checkLogResult"]["log_file"] == "20250619.17.02.30.log"


def test_con_varios_logs_manda_el_mas_reciente():
    clock = FakeClock()
    fs = FsConMtimes(
        files={f"{SALIDA}/viejo.log": LOG_FALLA, f"{SALIDA}/nuevo.log": LOG_OK},
        mtimes={"viejo.log": clock.now() - 100, "nuevo.log": clock.now() - 1},
    )

    resultado = _check_log(fs, clock)

    assert resultado.status == "ok"
    assert resultado.outputs["log_file"] == "nuevo.log"


def test_una_carpeta_inexistente_es_err_no_loop():
    resultado = _check_log(FsConMtimes())

    assert resultado.status == "err"
    assert resultado.loop is False


# ── add_qr: sólo Export ─────────────────────────────────────────────────


def test_add_qr_busca_la_ventana_por_titulo_y_clickea_solo_export():
    window = FakeWindow({"Toothform": {}})

    resultado = _registry(window=window).execute("toothform.add_qr", _ctx_factory({}))

    assert resultado.status == "ok"
    assert window.calls == [
        {"op": "find_window", "title": "Toothform", "process": None},
        # `button` lo agregó el núcleo (core#25) para poder pedir el secundario;
        # este click no lo pasa, así que queda el primario por default.
        {"op": "click", "handle": "1", "control": "Export", "button": "left"},
    ]


def test_add_qr_con_proceso_busca_por_ejecutable_y_no_por_titulo():
    exe = r"D:\4in1\recover\Toothform.exe"
    window = FakeWindow({exe: {}})

    resultado = _registry(window=window).execute(
        "toothform.add_qr", _ctx_factory({"proceso": exe}),
    )

    assert resultado.status == "ok"
    assert window.calls[0] == {"op": "find_window", "title": None, "process": exe}


# ── toothcam_enviar: mover y esperar el log ──────────────────────────────

WATCH = "D:/toothcam_watch"
RESULT = "D:/toothcam_watch/batch_result"


class FsLogDemorado(FakeFs):
    """El log de 'nombre' no aparece en list_dir hasta que el reloj llega a 'aparece_en'."""

    def __init__(self, clock, nombre: str, aparece_en: float, **kw) -> None:
        super().__init__(**kw)
        self.clock, self.nombre, self.aparece_en = clock, nombre, aparece_en

    def list_dir(self, path):
        entradas = super().list_dir(path)
        if self.clock.monotonic() < self.aparece_en:
            entradas = [e for e in entradas if e.name != self.nombre]
        return entradas


def _toothcam_enviar(fs, clock=None, **params):
    clock = clock or FakeClock()
    reg = _registry(fs=fs, clock=clock)
    defaults = {"carpeta_watch": WATCH, "carpeta_salida": RESULT}
    resultado = reg.execute("toothform.toothcam_enviar", _ctx_factory({**defaults, **params}))
    return resultado, fs, clock


def test_toothcam_enviar_mueve_los_archivos_antes_de_esperar():
    # Un log que ya estaba en carpeta_salida ANTES de mover -mismo criterio que
    # 'exportar' de ToothFORM- no cuenta: es de otro caso, no de este envío.
    clock = FakeClock()
    fs = FsLogDemorado(
        clock, "20260101.log", aparece_en=3.0,
        files={"D:/casos/uno-gum.stl": "g", "D:/casos/uno-tooth.stl": "t", f"{RESULT}/20260101.log": LOG_OK},
        dirs=(WATCH, RESULT),
    )

    resultado, fs, clock = _toothcam_enviar(
        fs, clock, archivos=["D:/casos/uno-gum.stl", "D:/casos/uno-tooth.stl"], intervalo=3.0,
    )

    assert resultado.status == "ok"
    assert resultado.outputs["log_file"] == "20260101.log"
    assert resultado.outputs["archivos_movidos"] == [f"{WATCH}/uno-gum.stl", f"{WATCH}/uno-tooth.stl"]
    assert f"{WATCH}/uno-gum.stl" in fs.files and "D:/casos/uno-gum.stl" not in fs.files


def test_toothcam_enviar_espera_el_log_que_todavia_no_aparecio():
    clock = FakeClock()
    fs = FsLogDemorado(
        clock, "20260101.log", aparece_en=10.0,
        files={"D:/casos/uno-gum.stl": "g", f"{RESULT}/20260101.log": LOG_OK},
        dirs=(WATCH, RESULT),
    )

    resultado, _, clock = _toothcam_enviar(
        fs, clock, archivos=["D:/casos/uno-gum.stl"], intervalo=3.0, timeout=60.0,
    )

    assert resultado.status == "ok"
    assert resultado.outputs["log_file"] == "20260101.log"
    assert clock.total_slept >= 10.0  # esperó de verdad, en vez de encontrarlo de casualidad


def test_toothcam_enviar_agota_el_timeout_sin_log_es_err():
    clock = FakeClock()
    fs = FsLogDemorado(
        clock, "nunca.log", aparece_en=10_000.0,
        files={"D:/casos/uno-gum.stl": "g"}, dirs=(WATCH, RESULT),
    )

    resultado, _, _ = _toothcam_enviar(
        fs, clock, archivos=["D:/casos/uno-gum.stl"], intervalo=5.0, timeout=12.0,
    )

    assert resultado.status == "err"
    assert "timeout" in resultado.message or "no apareció" in resultado.message
    assert resultado.outputs["archivos_movidos"] == [f"{WATCH}/uno-gum.stl"]


def test_toothcam_enviar_no_mueve_nada_si_falta_un_archivo():
    fs = FakeFs(files={"D:/casos/uno-gum.stl": "g"}, dirs=(WATCH, RESULT))

    resultado, fs, _ = _toothcam_enviar(fs, archivos=["D:/casos/uno-gum.stl", "D:/casos/no-existe.stl"])

    assert resultado.status == "err"
    assert "D:/casos/no-existe.stl" in resultado.message
    assert "D:/casos/uno-gum.stl" in fs.files  # no se movió nada, ni siquiera el que sí existía


def test_toothcam_enviar_carpeta_watch_inexistente_es_err():
    resultado, _, _ = _toothcam_enviar(FakeFs(dirs=(RESULT,)), archivos=["D:/casos/uno-gum.stl"])

    assert resultado.status == "err"


def test_toothcam_enviar_archivos_vacio_es_err():
    resultado, _, _ = _toothcam_enviar(FakeFs(dirs=(WATCH, RESULT)), archivos=[])

    assert resultado.status == "err"
