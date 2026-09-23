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
from backend.core.ports import PortError  # noqa: E402
from backend.core.registry import ToolRegistry  # noqa: E402
from backend.tests.fakes import FakeClock, FakeFs, FakeProcess, FakeWindow  # noqa: E402

from plugins.toothform.plugin import build_plugin  # noqa: E402

SALIDA = "D:/salida"
STL = "D:/casos/QATF001/stl"
EXE = "D:/4in1/Toothform-20260518/Toothform.exe"
LOG_OK = "QATF001-L01-A    Export successfully\n\nTotal 1 models, of which 1 succeeded and 0 failed\n"
LOG_FALLA = "222222-U01-A    Failed to hollow\n\nTotal 2 models, of which 1 succeeded and 1 failed\n"
# Lo que queda cuando ToothFORM se muere exportando: líneas de éxito y ni una de totales.
LOG_A_MEDIAS = "QATF001-L01-A    Export successfully\nQATF001-L02-A    Export successfully\n"
# 0xC0000005, la excepción con la que se cae ToothFORM cuando no puede abrir la carpeta.
CRASH = 3221225477


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
    de salida, como hace el real. El exit code por defecto es 2, como el real
    cuando termina —bien o mal—, así que no sirve para decidir; se pasa
    `exit_code` para los casos en que se muere y Windows contesta una excepción.
    """

    def __init__(
        self, fs, log: str | None, nombre="20260914.13.32.08.log", timed_out=False, exit_code=2,
    ) -> None:
        super().__init__()
        self.stub(EXE, exit_code=exit_code)
        self.fs, self.log, self.nombre, self.timed_out = fs, log, nombre, timed_out
        self.cargadas: list[list[str]] = []

    def _anotar(self, command) -> None:
        """Qué STL vio ToothFORM en esta corrida: la carpeta de staging se borra al final."""
        config = json.loads(self.fs.files[command[1]])
        try:
            self.cargadas.append(sorted(
                e.name for e in self.fs.list_dir(config["FilesToProcess"]["OpenFolder"])
            ))
        except PortError:
            self.cargadas.append(None)  # el fs del test no deja mirar; el real sí

    def run(self, command, *, cwd=None, timeout=None, env=None):
        self._anotar(command)
        resultado = super().run(command, cwd=cwd, timeout=timeout, env=env)
        if self.log is not None:
            self.fs.write_text(f"{SALIDA}/{self.nombre}", self.log)
            self.fs.mtimes[self.nombre] = 1_000.0
        return replace(resultado, timed_out=self.timed_out)


# Dos STL en la carpeta de entrada. Antes los tests la dejaban vacía porque
# nadie la miraba; ahora el tool la lista para armar las tandas, y una carpeta
# sin un solo .stl es un error con su propio test.
STL_DE_PRUEBA = {f"{STL}/QATF001-L01-A.stl": "solid", f"{STL}/QATF001-U01-A.stl": "solid"}


def _disco(files=None, dirs=(SALIDA,), mtimes=None):
    return FsConMtimes({**STL_DE_PRUEBA, **(files or {})}, dirs, mtimes)


def _ctx_factory(node_params, context=None, logs=None):
    def factory(declaracion, ports=None):
        declarados, extras = declaracion.split_params(node_params, {})
        return ToolContext(
            run_id="run-test", case_id="QATF001",
            params=declarados, extras=extras, config={}, context=context or {},
            log=(lambda m, *_a, **_k: logs.append(m)) if logs is not None else (lambda *_a, **_k: None),
            ports=ports or {},
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


def _exportar(fs=None, process=None, logs=None, **params):
    fs = fs or _disco()
    process = process or ToothformFalso(fs, LOG_OK)
    resultado = _registry(fs=fs, process=process).execute(
        "toothform.exportar",
        _ctx_factory({"ejecutable": EXE, "carpeta": STL, "salida": SALIDA, **params}, logs=logs),
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
    fs = _disco()
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, LOG_FALLA))

    assert resultado.status == "err"
    assert resultado.outputs["hubo_log"] == "si"
    assert resultado.outputs["fallidos"] == 1
    assert resultado.outputs["checkLogResult"]["log_file"] == "20260914.13.32.08.log"


def test_exportar_sin_log_es_err_y_lo_dice_no_se_confunde_con_un_export_fallido():
    fs = _disco()
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, log=None))

    assert resultado.status == "err"
    assert "sin dejar un log" in resultado.message
    assert resultado.outputs["hubo_log"] == "no"
    assert resultado.outputs["log_file"] == ""


def test_exportar_anota_cuantos_stl_va_a_cargar_toothform_de_una():
    """
    Cuando ToothFORM se cae no deja log ni dice por qué: lo único que queda
    para correlacionar es cuánto le habían dado, y eso tiene que estar escrito
    antes de correr —después del 0xC0000005 no hay de dónde sacarlo.
    """
    fs = FsConMtimes(dirs=(SALIDA,), files={
        f"{STL}/QATF001-L01-A.stl": "x" * 2_097_152,
        f"{STL}/QATF001-L02-A.stl": "x" * 1_048_576,
        f"{STL}/lean.txt": "no es un STL",
    })
    logs = []
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, LOG_OK), logs=logs)

    assert resultado.status == "ok", resultado.message
    assert any("2 STL, 3 MB a cargar de una" in m for m in logs), logs


def test_exportar_es_err_si_se_murio_a_la_mitad_aunque_el_log_diga_export_successfully():
    """
    El caso que puso un export incompleto en producción: ToothFORM se muere
    exportando, el log queda con las líneas de éxito que alcanzó a escribir y
    sin la de totales, y leerlo por palabras da ok —así el flujo seguía por la
    rama buena con la mitad de los datos exportados.
    """
    fs = _disco()
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, LOG_A_MEDIAS, exit_code=CRASH))

    assert resultado.status == "err", resultado.message
    assert "sin la" in resultado.message and "totales" in resultado.message
    assert resultado.outputs["exit_code"] == CRASH
    # El log igual se expone entero: es lo que el flujo copia a la carpeta del caso.
    assert resultado.outputs["hubo_log"] == "si"
    assert resultado.outputs["exportados"] == ["QATF001-L01-A", "QATF001-L02-A"]


def test_exportar_es_err_si_lo_matan_por_afuera_con_el_log_a_medias():
    """
    Medido: un `taskkill /F` sobre Toothform.exe devuelve 1, que no es un
    código de excepción de Windows. Si la regla mirara sólo el rango 0xC0000000
    esto pasaría por bueno —el log a medias dice "Export successfully"— y el
    caso avanzaría con la mitad de los datos. Lo que decide es que 1 no es de
    los códigos que ToothFORM deja cuando llega a terminar.
    """
    fs = _disco()
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, LOG_A_MEDIAS, exit_code=1))

    assert resultado.status == "err", resultado.message
    assert "se cortó exportando" in resultado.message
    assert resultado.outputs["exit_code"] == 1


def test_exportar_es_err_si_lo_matan_por_afuera_sin_log():
    fs = _disco()
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, log=None, exit_code=1))

    assert resultado.status == "err"
    assert "se cortó antes de escribir el log" in resultado.message
    # No inventa la causa: el ejecutable no dejó nada de por qué se cortó.
    assert "memoria" not in resultado.message and "32 bits" not in resultado.message


def test_exportar_respeta_el_log_completo_aunque_el_proceso_se_caiga_despues():
    """
    Con la línea de totales, ToothFORM ya dijo cómo le fue: el crash es de
    después y el export está hecho. Fallar ahí frenaría casos terminados.
    """
    fs = _disco()
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, LOG_OK, exit_code=CRASH))

    assert resultado.status == "ok", resultado.message
    assert resultado.outputs["exitosos"] == 1
    assert resultado.outputs["exit_code"] == CRASH


def test_exportar_con_un_log_sin_totales_pero_sin_crash_sigue_leyendose_por_palabras():
    """La lectura por palabras existe para versiones que no escriben totales; el crash no las toca."""
    fs = _disco()
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, LOG_A_MEDIAS))

    assert resultado.status == "ok", resultado.message
    assert resultado.outputs["exit_code"] == 2


def test_exportar_sin_log_distingue_el_crash_de_una_ruta_que_la_app_no_ve():
    fs = _disco()
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, log=None, exit_code=CRASH))

    assert resultado.status == "err"
    assert "0xC0000005" in resultado.message
    assert "sin dejar un log" not in resultado.message
    assert resultado.outputs["hubo_log"] == "no"
    assert resultado.outputs["exit_code"] == CRASH


def test_exportar_ignora_un_log_viejo_que_ya_estaba_en_la_salida():
    fs = _disco(
        files={f"{SALIDA}/20250101.09.00.00.log": LOG_OK},
        mtimes={"20250101.09.00.00.log": 10.0},
    )
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, log=None))

    assert resultado.status == "err"
    assert resultado.outputs["hubo_log"] == "no"


def test_exportar_con_timeout_es_err_sin_log():
    fs = _disco()
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

    fs = _disco()
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


# ── exportar: tandas ────────────────────────────────────────────────────
#
# Una carpeta de caso entera mata a ToothFORM con 0xC0000005 sin dejar log y
# sin exportar nada —medido sobre BY733: 36 STL, 595 MB, muere a los 32,5 s—.
# Por qué se cae no se sabe; no es falta de memoria (llegó a 655 MB de un
# techo de 2 GB). En tandas al menos lo que salió antes de la que se corta
# queda exportado, y el log dice hasta dónde llegó.

UN_MB = "s" * 1_048_576


def _log_de(*datos):
    lineas = "".join(f"{d}    Export successfully\n" for d in datos)
    return f"{lineas}\nTotal {len(datos)} models, of which {len(datos)} succeeded and 0 failed\n"


class ToothformPorTandas(ToothformFalso):
    """
    Un log nuevo por corrida, como el real, y memoria de qué carpeta le tocó
    cargar a cada una — que es lo que dice si las tandas se armaron bien.
    """

    def __init__(self, fs, logs, exit_code=2) -> None:
        super().__init__(fs, log=None, exit_code=exit_code)
        self.logs, self.corridas = list(logs), 0

    def run(self, command, *, cwd=None, timeout=None, env=None):
        self._anotar(command)
        texto = self.logs[self.corridas] if self.corridas < len(self.logs) else None
        self.corridas += 1
        nombre = f"20260914.13.32.{self.corridas:02d}.log"
        resultado = FakeProcess.run(self, command, cwd=cwd, timeout=timeout, env=env)
        if texto is not None:
            self.fs.write_text(f"{SALIDA}/{nombre}", texto)
            self.fs.mtimes[nombre] = 1_000.0 + self.corridas
        return resultado


def _cuatro_mb():
    return _disco(files={f"{STL}/QATF001-{n}.stl": UN_MB for n in ("L00-A", "L01-A", "U00-A", "U01-A")})


def test_exportar_parte_la_carpeta_en_tandas_y_corre_toothform_una_vez_por_cada_una():
    fs = _cuatro_mb()
    process = ToothformPorTandas(fs, [
        _log_de("QATF001-L00-A", "QATF001-L01-A"),
        _log_de("QATF001-U00-A", "QATF001-U01-A"),
    ])
    resultado, _fs, _p = _exportar(fs=fs, process=process, max_mb_por_tanda=2.5)

    assert resultado.status == "ok", resultado.message
    assert process.corridas == 2
    # Cada corrida ve sólo su tanda: la carpeta de entrada se vacía entre una y
    # otra, si no la tanda anterior se exportaría de nuevo sin que nadie la pida.
    assert process.cargadas == [
        ["QATF001-L00-A.stl", "QATF001-L01-A.stl"],
        ["QATF001-U00-A.stl", "QATF001-U01-A.stl"],
    ]


def test_las_salidas_de_todas_las_tandas_llegan_unificadas():
    fs = _cuatro_mb()
    process = ToothformPorTandas(fs, [
        _log_de("QATF001-L00-A", "QATF001-L01-A"),
        _log_de("QATF001-U00-A", "QATF001-U01-A"),
    ])
    resultado, _fs, _p = _exportar(fs=fs, process=process, max_mb_por_tanda=2.5)
    salidas = resultado.outputs

    assert salidas["tandas"] == 2
    assert salidas["exitosos"] == 4 and salidas["fallidos"] == 0
    assert salidas["exportados"] == [
        "QATF001-L00-A", "QATF001-L01-A", "QATF001-U00-A", "QATF001-U01-A",
    ]
    assert salidas["log_files"] == ["20260914.13.32.01.log", "20260914.13.32.02.log"]
    # El texto de las dos, con de cuál es cada parte.
    assert salidas["log_texto"].count("Total 2 models") == 2
    assert all(n in salidas["log_texto"] for n in salidas["log_files"])
    # Con varias tandas la primera línea del último log no describe el export.
    assert salidas["checkLogResult"]["message"] == "2 tandas: 4 exportados, 0 fallidos"
    # El export de todas las tandas cae en la misma carpeta: la del caso.
    assert salidas["carpeta_export"] == f"{SALIDA}/QATF001"


def test_una_tanda_que_falla_corta_y_no_corre_las_que_quedan():
    fs = _cuatro_mb()
    # Con 1,5 MB de límite cada STL de 1 MB se va en su propia tanda: cuatro.
    process = ToothformPorTandas(fs, [
        _log_de("QATF001-L00-A"),
        "QATF001-L01-A    Failed to hollow\n\nTotal 1 models, of which 0 succeeded and 1 failed\n",
        _log_de("no deberia llegar"),
        _log_de("tampoco"),
    ])
    resultado, _fs, _p = _exportar(fs=fs, process=process, max_mb_por_tanda=1.5)

    assert resultado.status == "err"
    assert "tanda 2/4" in resultado.message
    assert process.corridas == 2, "las tandas 3 y 4 no tenían que correr"
    # Lo que exportó antes de cortar se reporta igual: el caso quedó a medias y
    # el flujo tiene que poder verlo.
    assert resultado.outputs["exitosos"] == 1 and resultado.outputs["fallidos"] == 1
    assert resultado.outputs["exportados"] == ["QATF001-L00-A"]


def test_una_carpeta_que_entra_de_una_va_tal_cual_sin_copiar_nada():
    fs = _disco()
    resultado, _fs, process = _exportar(fs=fs)

    assert resultado.status == "ok", resultado.message
    assert resultado.outputs["tandas"] == 1
    config = json.loads(fs.files[resultado.outputs["config"]])
    assert config["FilesToProcess"]["OpenFolder"] == STL
    assert not fs.exists(f"{SALIDA}/entrada-QATF001")


def test_max_mb_por_tanda_en_cero_manda_la_carpeta_entera():
    fs = _cuatro_mb()
    process = ToothformPorTandas(fs, [_log_de("QATF001-L00-A")])
    resultado, _fs, _p = _exportar(fs=fs, process=process, max_mb_por_tanda=0)

    assert resultado.status == "ok", resultado.message
    assert process.corridas == 1
    assert process.cargadas[0] == [
        "QATF001-L00-A.stl", "QATF001-L01-A.stl", "QATF001-U00-A.stl", "QATF001-U01-A.stl",
    ]


def test_un_stl_mas_grande_que_el_limite_se_va_solo_pero_se_va():
    """Partirlo no se puede; dejarlo afuera sería exportar de menos sin decirlo."""
    fs = _disco(files={f"{STL}/QATF001-L00-A.stl": UN_MB * 4, f"{STL}/QATF001-U00-A.stl": "chico"})
    process = ToothformPorTandas(fs, [_log_de("QATF001-L00-A"), _log_de("QATF001-U00-A")])
    resultado, _fs, _p = _exportar(fs=fs, process=process, max_mb_por_tanda=2.5)

    assert resultado.status == "ok", resultado.message
    assert ["QATF001-L00-A.stl"] in process.cargadas


def test_exportar_es_err_si_la_carpeta_no_tiene_ningun_stl():
    fs = FsConMtimes(dirs=(STL, SALIDA))
    resultado, _fs, process = _exportar(fs=fs)

    assert resultado.status == "err"
    assert "no hay ningún .stl" in resultado.message
    assert process.calls == []


def test_si_la_carpeta_no_se_deja_listar_va_entera_como_antes_de_las_tandas():
    """Un share que no contesta no puede frenar un export que hasta ayer andaba."""

    class SinListar(FsConMtimes):
        def list_dir(self, path):
            if path == STL:
                raise PortError("el share no contesta")
            return super().list_dir(path)

    fs = SinListar(dirs=(STL, SALIDA))
    resultado, _fs, _p = _exportar(fs=fs, process=ToothformFalso(fs, LOG_OK), max_mb_por_tanda=0.001)

    assert resultado.status == "ok", resultado.message
    assert resultado.outputs["tandas"] == 1
    assert json.loads(fs.files[resultado.outputs["config"]])["FilesToProcess"]["OpenFolder"] == STL


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
    assert process.cargadas == [["dos.stl", "uno.stl"]]
    # Y la carpeta de staging no queda: son copias que ya no le sirven a nadie.
    assert not fs.exists(entrada)


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
    fs = FakeFs(files={"D:/casos/uno-gum.stl": "g"}, dirs=(RESULT,))

    resultado, fs, _ = _toothcam_enviar(fs, carpeta_watch="D:/no-existe/watch", archivos=["D:/casos/uno-gum.stl"])

    assert resultado.status == "err"
    assert "D:/casos/uno-gum.stl" in fs.files


def test_toothcam_enviar_crea_la_subcarpeta_del_caso_adentro_de_la_vigilada():
    clock = FakeClock()
    fs = FsLogDemorado(
        clock, "20260101.log", aparece_en=3.0,
        files={"D:/casos/uno-gum.stl": "g", f"{RESULT}/20260101.log": LOG_OK},
        dirs=(WATCH, RESULT),
    )

    resultado, fs, _ = _toothcam_enviar(
        fs, clock, carpeta_watch=f"{WATCH}/BY275", archivos=["D:/casos/uno-gum.stl"], intervalo=3.0,
    )

    assert resultado.status == "ok"
    assert resultado.outputs["archivos_movidos"] == [f"{WATCH}/BY275/uno-gum.stl"]


def test_toothcam_enviar_no_crea_la_subcarpeta_si_falta_un_archivo():
    fs = FakeFs(dirs=(WATCH, RESULT))

    resultado, fs, _ = _toothcam_enviar(fs, carpeta_watch=f"{WATCH}/BY275", archivos=["D:/casos/no-existe.stl"])

    assert resultado.status == "err"
    assert f"{WATCH}/BY275" not in fs.dirs


class FsSalidaDemorada(FakeFs):
    """ToothCAM crea 'carpeta' con su log adentro recién cuando el reloj llega a 'aparece_en'."""

    def __init__(self, clock, carpeta: str, aparece_en: float, **kw) -> None:
        super().__init__(**kw)
        self.clock, self.carpeta, self.aparece_en = clock, carpeta, aparece_en

    def _todavia_no(self, path) -> bool:
        p = path.replace("\\", "/").rstrip("/")
        return self.clock.monotonic() < self.aparece_en and (p == self.carpeta or p.startswith(self.carpeta + "/"))

    def exists(self, path):
        return False if self._todavia_no(path) else super().exists(path)

    def list_dir(self, path):
        if self._todavia_no(path):
            raise PortError(f"no existe: {path}")
        return super().list_dir(path)


def test_toothcam_enviar_espera_a_que_toothcam_cree_la_carpeta_de_salida_del_caso():
    clock = FakeClock()
    salida = "D:/toothcam_out/BY275"
    fs = FsSalidaDemorada(
        clock, salida, aparece_en=10.0,
        files={"D:/casos/uno-gum.stl": "g", f"{salida}/20260101.log": LOG_OK},
        dirs=(WATCH, "D:/toothcam_out", salida),
    )

    resultado, fs, clock = _toothcam_enviar(
        fs, clock, carpeta_salida=salida, archivos=["D:/casos/uno-gum.stl"], intervalo=3.0, timeout=60.0,
    )

    assert resultado.status == "ok"
    assert resultado.outputs["log_file"] == "20260101.log"
    assert clock.total_slept >= 10.0
    assert ("make_dirs", salida) not in [c[:2] for c in fs.calls]  # la crea ToothCAM, no nosotros


def test_toothcam_enviar_archivos_vacio_es_err():
    resultado, _, _ = _toothcam_enviar(FakeFs(dirs=(WATCH, RESULT)), archivos=[])

    assert resultado.status == "err"


def test_toothcam_enviar_con_carpeta_mueve_todo_lo_de_adentro_sin_filtrar_por_extension():
    clock = FakeClock()
    fs = FsLogDemorado(
        clock, "20260101.log", aparece_en=3.0,
        files={
            "D:/casos/uno/uno-gum.stl": "g", "D:/casos/uno/uno-tooth.stl": "t",
            "D:/casos/uno/uno.pts": "p", "D:/casos/uno/sub/otro.stl": "x",
            f"{RESULT}/20260101.log": LOG_OK,
        },
        dirs=(WATCH, RESULT, "D:/casos/uno", "D:/casos/uno/sub"),
    )

    resultado, fs, _ = _toothcam_enviar(fs, clock, carpeta="D:/casos/uno", intervalo=3.0)

    assert resultado.status == "ok"
    assert sorted(resultado.outputs["archivos_movidos"]) == [
        f"{WATCH}/uno-gum.stl", f"{WATCH}/uno-tooth.stl", f"{WATCH}/uno.pts",
    ]
    assert "D:/casos/uno/sub/otro.stl" in fs.files  # las subcarpetas no se tocan


def test_toothcam_enviar_con_carpeta_vacia_o_inexistente_es_err():
    vacia, _, _ = _toothcam_enviar(FakeFs(dirs=(WATCH, RESULT, "D:/casos/uno")), carpeta="D:/casos/uno")
    inexistente, _, _ = _toothcam_enviar(FakeFs(dirs=(WATCH, RESULT)), carpeta="D:/casos/no-existe")

    assert vacia.status == "err" and "D:/casos/uno" in vacia.message
    assert inexistente.status == "err" and "D:/casos/no-existe" in inexistente.message


def test_toothcam_enviar_con_carpeta_y_archivos_a_la_vez_es_err():
    fs = FakeFs(files={"D:/casos/uno/uno-gum.stl": "g"}, dirs=(WATCH, RESULT, "D:/casos/uno"))

    resultado, fs, _ = _toothcam_enviar(fs, carpeta="D:/casos/uno", archivos=["D:/casos/uno/uno-gum.stl"])

    assert resultado.status == "err"
    assert "D:/casos/uno/uno-gum.stl" in fs.files


def test_toothcam_enviar_con_la_carpeta_vigilada_como_origen_es_err():
    fs = FakeFs(files={f"{WATCH}/uno-gum.stl": "g"}, dirs=(WATCH, RESULT))

    resultado, _, _ = _toothcam_enviar(fs, carpeta="D:\toothcam_watch\\")

    assert resultado.status == "err"


def test_toothcam_enviar_con_copiar_deja_los_originales_donde_estaban():
    clock = FakeClock()
    fs = FsLogDemorado(
        clock, "20260101.log", aparece_en=3.0,
        files={"D:/casos/uno/uno-gum.stl": "g", "D:/casos/uno/uno.pts": "p", f"{RESULT}/20260101.log": LOG_OK},
        dirs=(WATCH, RESULT, "D:/casos/uno"),
    )

    resultado, fs, _ = _toothcam_enviar(fs, clock, carpeta="D:/casos/uno", copiar=True, intervalo=3.0)

    assert resultado.status == "ok"
    assert sorted(resultado.outputs["archivos_movidos"]) == [f"{WATCH}/uno-gum.stl", f"{WATCH}/uno.pts"]
    assert "D:/casos/uno/uno-gum.stl" in fs.files and f"{WATCH}/uno-gum.stl" in fs.files


class FsToothCAM(FakeFs):
    """
    ToothCAM trabajando, como se vio con BY275: apenas termina de escanear deja
    "finish.log" ("finish in this folder"); después, cada 'cada' segundos le
    suma una línea al log del caso y, 'retraso' vueltas más tarde, el .txt de
    salida de ese modelo (salvo que haya fallado).
    """

    def __init__(self, clock, salida: str, lineas: list[str], cada: float, retraso: int = 0, **kw) -> None:
        super().__init__(**kw)
        self.clock, self.salida, self.lineas, self.cada, self.retraso = clock, salida, lineas, cada, retraso
        self.log = f"{salida}/BY275.log"

    def _vuelta(self) -> int:
        return int(self.clock.monotonic() // self.cada)

    def _sincronizar(self) -> None:
        vuelta = self._vuelta()
        if vuelta < 1:
            return
        self.dirs.add(self.salida)
        self.files[f"{self.salida}/finish.log"] = "finish in this folder\n"
        n = min(len(self.lineas), vuelta)
        if n:
            self.files[self.log] = "\n".join(self.lineas[:n]) + "\n"
        for linea in self.lineas[: max(0, min(len(self.lineas), vuelta - self.retraso))]:
            _n, dato, _fecha, estado = linea.strip().split("\t")
            if estado == "success":
                self.dirs.add(f"{self.salida}/{dato}")
                self.files[f"{self.salida}/{dato}/{dato}.TXT"] = "x"

    def exists(self, path):
        self._sincronizar()
        return super().exists(path)

    def walk(self, path, max_depth=None):
        self._sincronizar()
        return super().walk(path, max_depth)

    def list_dir(self, path):
        self._sincronizar()
        n = float(min(len(self.lineas), self._vuelta()))
        return [replace(e, modified_at=n) if e.path == self.log else e for e in super().list_dir(path)]


# El log de BY275 tal cual lo dejó ToothCAM (23/09/2026), con tabs y el tab del
# final: el número de la derecha son los archivos vistos hasta ahí, y el nodo
# cortó en "10\10" creyendo que había terminado. Las dos últimas líneas son
# inventadas, para que haya algo después de ese 10\10.
LOG_BY275 = [
    "1\\1\tBY275-L06-A\t2026-09-23-15-43-41\tsuccess\t",
    "2\\3\tBY275-L00-B\t2026-09-23-15-43-42\tsuccess\t",
    "3\\3\tBY275-L15-A\t2026-09-23-15-43-42\tsuccess\t",
    "4\\7\tBY275-L03-A\t2026-09-23-15-43-42\tsuccess\t",
    "5\\10\tBY275-L12-A\t2026-09-23-15-43-42\tsuccess\t",
    "6\\10\tBY275-L09-A\t2026-09-23-15-43-42\tsuccess\t",
    "7\\10\tBY275-U03-A\t2026-09-23-15-43-42\tsuccess\t",
    "8\\10\tBY275-U06-A\t2026-09-23-15-43-42\tsuccess\t",
    "9\\10\tBY275-U00-B\t2026-09-23-15-43-42\tsuccess\t",
    "10\\10\tBY275-U09-A\t2026-09-23-15-43-42\tsuccess\t",
    "11\\12\tBY275-L01-A\t2026-09-23-15-43-50\tsuccess\t",
    "12\\12\tBY275-U01-A\t2026-09-23-15-43-51\tsuccess\t",
]
SALIDA_CASO = "D:/toothcam_out/BY275"
# El patrón de producción de los STL de entrada, con .txt en vez de -gum.stl.
PATRON_TXT = r"^(?P<id_externo>[A-Z]{2}\d{3})-(?P<maxilar>[LU])(?P<movimiento>\d{2})-(?P<type>[A-Z])\.txt$"


def _toothcam_con(fs, clock, **params):
    return _toothcam_enviar(
        fs, clock, **{
            "carpeta_salida": SALIDA_CASO, "archivos": ["D:/casos/uno-gum.stl"],
            "intervalo": 5.0, "timeout": 600.0, **params,
        },
    )


def _toothcam_fs(clock, lineas, **kw):
    return FsToothCAM(
        clock, SALIDA_CASO, lineas, cada=10.0,
        files={"D:/casos/uno-gum.stl": "g"}, dirs=(WATCH, "D:/toothcam_out"), **kw,
    )


def _con_falla(dato: str) -> list[str]:
    return [l.replace("success", "fail") if dato in l else l for l in LOG_BY275]


def test_toothcam_enviar_con_esperados_no_corta_en_10_de_10_ni_con_el_finish_log():
    clock = FakeClock()

    resultado, _, clock = _toothcam_con(_toothcam_fs(clock, LOG_BY275), clock, esperados=12)

    assert resultado.status == "ok"
    assert resultado.outputs["log_file"] == "BY275.log"  # no el finish.log
    assert resultado.outputs["exitosos"] == 12 and resultado.outputs["fallidos"] == 0
    assert clock.monotonic() >= 10.0 * len(LOG_BY275)  # hasta la línea 12, no antes


def test_toothcam_enviar_con_un_dato_que_falla_es_err_y_lo_nombra():
    clock = FakeClock()

    resultado, _, _ = _toothcam_con(_toothcam_fs(clock, _con_falla("BY275-L09-A")), clock, esperados=12)

    assert resultado.status == "err"
    assert "BY275-L09-A" in resultado.message
    assert resultado.outputs["exitosos"] == 11 and resultado.outputs["fallidos"] == 1


def test_toothcam_enviar_con_patron_salida_espera_tambien_los_txt():
    clock = FakeClock()
    fs = _toothcam_fs(clock, LOG_BY275, retraso=3)

    resultado, _, clock = _toothcam_con(fs, clock, esperados=12, patron_salida=PATRON_TXT)

    assert resultado.status == "ok"
    assert clock.monotonic() >= 10.0 * (len(LOG_BY275) + 3)  # el log llegó antes que los txt


def test_toothcam_enviar_con_patron_salida_un_modelo_fallido_no_lo_deja_esperando():
    clock = FakeClock()
    fs = _toothcam_fs(clock, _con_falla("BY275-U03-A"))

    resultado, _, _ = _toothcam_con(fs, clock, esperados=12, patron_salida=PATRON_TXT)

    assert resultado.status == "err"  # por la falla, no por timeout
    assert "BY275-U03-A" in resultado.message


def test_toothcam_enviar_timeout_con_esperados_dice_hasta_donde_llego():
    clock = FakeClock()

    resultado, _, _ = _toothcam_con(_toothcam_fs(clock, LOG_BY275), clock, esperados=36, timeout=300.0)

    assert resultado.status == "err"
    assert "12 de 36" in resultado.message
    assert resultado.outputs["log_file"] == "BY275.log"


def test_toothcam_enviar_patron_salida_sin_esperados_es_err_antes_de_mover():
    clock = FakeClock()
    fs = _toothcam_fs(clock, LOG_BY275)

    resultado, fs, _ = _toothcam_con(fs, clock, patron_salida=PATRON_TXT)

    assert resultado.status == "err"
    assert "D:/casos/uno-gum.stl" in fs.files


def test_toothcam_enviar_patron_salida_invalido_es_err():
    clock = FakeClock()

    resultado, _, _ = _toothcam_con(_toothcam_fs(clock, LOG_BY275), clock, esperados=12, patron_salida="(")

    assert resultado.status == "err"
    assert "patron_salida" in resultado.message


def test_toothcam_enviar_el_finish_log_solo_no_cuenta_como_log():
    clock = FakeClock()

    resultado, _, _ = _toothcam_con(_toothcam_fs(clock, []), clock, timeout=60.0)

    assert resultado.status == "err"
    assert "no apareció un log nuevo" in resultado.message


def test_toothcam_enviar_sin_esperados_corta_en_la_primera_linea_n_de_n():
    # El límite que 'esperados' viene a arreglar: sin el total, "1\1" parece el final.
    clock = FakeClock()

    resultado, _, _ = _toothcam_con(_toothcam_fs(clock, LOG_BY275), clock)

    assert resultado.status == "ok"
    assert resultado.outputs["exitosos"] == 1
