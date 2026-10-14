"""Paso 1 del orientador: encontrar la base plana y armar el plano de referencia.

Réplica de lo que hacía «orientador 3.1» en Modelos planos V5.06.02:

1. Casco convexo de los vértices (3D ConvexHull). Sobre el casco la base del
   modelo es una sola cara plana grande, aunque la base real tenga texto, huecos
   o el escaneo tenga ruido: el casco solo ve el borde exterior.
2. Caras del casco coplanares con sus vecinas (Face to Face Angles con dominio
   [0; 0,01°]), agrupadas en regiones; se toma la de mayor área.
3. Plano ajustado a los vértices de esa región (Plane Fit).
4. Origen = centroide del volumen del casco proyectado sobre el plano
   (Volume + Plane Closest Point).
5. La Z del plano apunta hacia adentro del modelo (en el GH: si el corte a +3 mm
   no da curvas, se invierte la normal).
6. Los ejes X/Y salen de cómo Rhino construye un plano a partir de una normal
   (Plane Normal), que se replica en `plano_rhino`.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import trimesh


@dataclass
class Plano:
    origen: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray

    def a_local(self) -> np.ndarray:
        """Matriz 4x4 que lleva este plano al plano XY del mundo (Orient)."""
        m = np.eye(4)
        m[:3, :3] = np.vstack([self.x, self.y, self.z])
        m[:3, 3] = -m[:3, :3] @ self.origen
        return m


def _perpendicular_rhino(v: np.ndarray) -> np.ndarray:
    """ON_3dVector::PerpendicularTo de openNURBS, tal cual.

    Importa replicarlo exacto: define el eje X del plano y por lo tanto en qué
    cuadrante cae el modelo antes del giro de 90° del orientador.
    """
    ax, ay, az = abs(v[0]), abs(v[1]), abs(v[2])
    if ay > ax:
        if az > ay:
            i, j, k, a, b = 2, 1, 0, v[2], -v[1]
        elif az >= ax:
            i, j, k, a, b = 1, 2, 0, v[1], -v[2]
        else:
            i, j, k, a, b = 1, 0, 2, v[1], -v[0]
    elif az > ax:
        i, j, k, a, b = 2, 0, 1, v[2], -v[0]
    elif az > ay:
        i, j, k, a, b = 0, 2, 1, v[0], -v[2]
    else:
        i, j, k, a, b = 0, 1, 2, v[0], -v[1]
    out = np.zeros(3)
    out[i], out[j], out[k] = b, a, 0.0
    return out


def plano_rhino(origen, normal) -> Plano:
    """ON_Plane::CreateFromNormal: el plano que arma Rhino con origen y normal."""
    z = np.asarray(normal, float)
    z = z / np.linalg.norm(z)
    x = _perpendicular_rhino(z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    y = y / np.linalg.norm(y)
    return Plano(np.asarray(origen, float), x, y, z)


def pieza_principal(malla: trimesh.Trimesh) -> trimesh.Trimesh:
    """Disjoint Mesh + Sort por área (descendente) + ítem 0."""
    piezas = malla.split(only_watertight=False)
    if len(piezas) <= 1:
        return malla
    return max(piezas, key=lambda p: p.area)


def _regiones_planas(casco: trimesh.Trimesh, tol_grados: float) -> list[np.ndarray]:
    """Caras del casco agrupadas en regiones coplanares conectadas."""
    adj = casco.face_adjacency
    ang = casco.face_adjacency_angles
    g = nx.Graph()
    g.add_nodes_from(range(len(casco.faces)))
    g.add_edges_from(adj[ang <= np.radians(tol_grados)])
    return [np.fromiter(c, int) for c in nx.connected_components(g)]


@dataclass
class Base:
    plano: Plano
    area: float
    fraccion_area: float
    dominancia: float
    planitud: float


def detectar_base(malla: trimesh.Trimesh, tol_grados: float = 0.5) -> Base:
    """Base plana de `malla` (ya reducida a la pieza que corresponda).

    `tol_grados` agrupa caras del casco casi coplanares. El GH usaba 0,01° sobre
    el promedio de ángulos con las vecinas, que en la práctica erosiona la región
    pero da el mismo plano; acá se usa un umbral algo más permisivo para tolerar
    bases impresas y re-escaneadas que no son perfectamente planas.
    """
    casco = malla.convex_hull
    regiones = _regiones_planas(casco, tol_grados)
    areas = np.array([casco.area_faces[r].sum() for r in regiones])
    orden = np.argsort(areas)[::-1]
    region = regiones[orden[0]]

    pts = casco.vertices[np.unique(casco.faces[region].ravel())]
    centro = pts.mean(axis=0)
    normal = np.linalg.svd(pts - centro)[2][2]

    # Z hacia adentro del modelo: el centroide del volumen queda del lado de la
    # normal. Es lo mismo que el chequeo del GH (corte a +3 mm con o sin curvas).
    centroide = casco.center_mass
    if np.dot(centroide - centro, normal) < 0:
        normal = -normal
    origen = centroide - np.dot(centroide - centro, normal) * normal

    segunda = areas[orden[1]] if len(orden) > 1 else 0.0
    return Base(
        plano=plano_rhino(origen, normal),
        area=float(areas[orden[0]]),
        fraccion_area=float(areas[orden[0]] / casco.area),
        dominancia=float(areas[orden[0]] / segunda) if segunda else float("inf"),
        planitud=float(np.abs((pts - centro) @ normal).max()),
    )
