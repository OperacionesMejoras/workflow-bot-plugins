"""
Re-exporta `PLUGIN` a nivel de paquete: es lo que busca el auto-descubrimiento
de `plugins_dir` (`backend/core/instance.py:_plugins_de_la_carpeta`) para una
carpeta con `__init__.py` — importa `model_arranger:PLUGIN`, no
`model_arranger.plugin:PLUGIN`. La carpeta es `model_arranger` (con guion
bajo) aunque el `name` del manifest sea `model-arranger` (kebab-case): el
schema de este repo exige que `path` sea un identificador valido de Python
(`^[A-Za-z_][A-Za-z0-9_]*...`), no acepta guiones.
"""

from .plugin import MANIFEST, PLUGIN, build_plugin

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
