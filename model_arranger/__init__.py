"""
Re-exporta `PLUGIN` a nivel de paquete: es lo que busca el auto-descubrimiento
de `plugins_dir` (`backend/core/instance.py:_plugins_de_la_carpeta`) para una
carpeta con `__init__.py` — importa `model_arranger:PLUGIN`. La carpeta es
`model_arranger` (con guion bajo) aunque el `name` del manifest sea
`model-arranger`: el schema exige que `path` sea un identificador de Python.
"""

from .plugin import MANIFEST, PLUGIN, build_plugin

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
