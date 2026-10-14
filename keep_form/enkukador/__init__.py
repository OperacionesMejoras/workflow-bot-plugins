"""
La salida de ToothFORM —pieza hueca con las referencias del robot, QR y
texto, -gum.stl y -tooth.ply— reconstruida sin ToothFORM.

Copiado de OperacionesMejoras/Enkukador-keepsmiling, rama testing, commit
590dff4 (14/10/2026): `base.py`, `arco.py`, `hueco.py` y `toothform.py`, lo
que usa `--producto toothform`. Va adentro del plugin porque en la máquina
que lo usa no se puede instalar el programa Enkukador; sí las librerías, que
el plugin declara en su requirements.txt. Los cambios respecto del original
están dichos en el docstring de `toothform.py`. Lo que se arregle allá hay que
traerlo acá a mano: ésta es una copia, no una dependencia.

Se importa sólo desde adentro del tool (`keep_form.exportar`): necesita
numpy, scipy, trimesh, shapely, networkx, manifold3d, scikit-image, pillow,
segno y fonttools, y sin ellas el plugin tiene que cargar igual.

DejaVuSans-Bold.ttf es la de matplotlib, con su licencia en LICENSE_DEJAVU.
"""
