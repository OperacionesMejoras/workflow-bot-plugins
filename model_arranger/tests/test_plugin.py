"""
Sin tools todavia (ver docstring de `plugin.py`): por ahora solo confirma que
el manifest carga. Cuando haya tools, cada uno necesita su propio test contra
los fakes de `fs`/`process` del nucleo (ver `archivos/tests/` o
`convertidor/tests/` para el patron).

Mismo esquema de import que esos dos (`parents[3]` + `plugins.<name>...`):
no corre suelto parado en este checkout — como sus tests tampoco corren solos
acá (`plugins.archivos` no resuelve sin armar antes el entorno de curaduria).
Hace falta `backend` (`workflow-bot-core`) y este repo montado como `plugins/`
al lado, siguiendo `docs/curar-un-plugin.md` (pasos b/c), antes de poder
correr pytest sobre esta carpeta.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from plugins.model_arranger.plugin import MANIFEST, PLUGIN  # noqa: E402


def test_manifest():
    assert MANIFEST.name == "model-arranger"
    assert set(MANIFEST.ports) == {"fs", "process"}


def test_plugin_sin_tools_todavia():
    assert PLUGIN.tools == []
