"""
Re-exporta `PLUGIN` a nivel de paquete: el auto-descubrimiento de la carpeta
de plugins importa `keep_form:PLUGIN`.
"""

from .plugin import MANIFEST, PLUGIN, build_plugin

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
