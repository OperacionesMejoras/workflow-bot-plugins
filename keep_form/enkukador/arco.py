"""Paso 2 del orientador: curva central del arco y giro por cuadrantes.

Con la malla ya llevada al plano de la base (z=0, Z hacia adentro):

1. Corte a 0,5 mm de la base. A esa altura no hay dientes: el contorno es solo
   material de base, una U. Se toma la curva más larga (Mesh | Plane + Sort por
   largo descendente).
2. Eje medial de esa U por Voronoi ("Curva int" en el GH). El GH recortaba las
   celdas de Voronoi contra el contorno y podaba ramas a mano; acá se arma el
   esqueleto interior y se toma su camino más largo, que para una U es el arco.
3. Vector desde el punto medio de la curva central hacia el origen (centroide
   del casco proyectado), es decir hacia la parte posterior. Ángulo de ese
   vector al eje Y del plano, medido en sentido antihorario (Vector3d.VectorAngle
   con plano).
4. Según el cuadrante se gira el plano 180°, 90°, 0° o 270°, para que la parte
   posterior quede hacia -Y y los incisivos hacia +Y. El GH no corrige más que
   eso acá: el ajuste fino de rotación viene después con la plantilla.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import trimesh
from scipy.spatial import Voronoi
from shapely import contains_xy
from shapely.geometry import LineString, Polygon

# Paneles del GH: dominios de ángulo -> giro del plano en grados.
CUADRANTES = ((-45.0, 45.0, 180.0), (45.0, 135.0, 90.0), (135.0, 225.0, 0.0), (225.0, 315.0, 270.0))


def contorno_base(malla: trimesh.Trimesh, altura: float = 0.5) -> Polygon:
    """Polígono del corte a `altura` sobre la base (malla en el marco de la base)."""
    seccion = malla.section(plane_origin=[0, 0, altura], plane_normal=[0, 0, 1])
    if seccion is None:
        raise ValueError(f"el corte a {altura} mm de la base no toca el modelo")
    plano, _ = seccion.to_2D(to_2D=np.eye(4))
    polis = [p for p in plano.polygons_full if p is not None and p.is_valid]
    if not polis:
        raise ValueError("el corte de la base no cerró ningún contorno")
    return max(polis, key=lambda p: p.exterior.length)


def _remuestrear(borde, paso: float) -> np.ndarray:
    n = max(64, int(borde.length / paso))
    d = np.linspace(0.0, borde.length, n, endpoint=False)
    return np.array([borde.interpolate(x).coords[0] for x in d])


def curva_central(poli: Polygon, paso: float = 0.4, margen: float = 0.35) -> np.ndarray:
    """Eje medial de la U: camino más largo del esqueleto de Voronoi interior."""
    pts = _remuestrear(poli.exterior, paso)
    vor = Voronoi(pts)
    dentro = poli.buffer(-margen)  # erosionar evita ramas pegadas al borde
    V = vor.vertices
    bueno = contains_xy(dentro, V[:, 0], V[:, 1])

    g = nx.Graph()
    for a, b in vor.ridge_vertices:
        if a < 0 or b < 0 or not (bueno[a] and bueno[b]):
            continue
        if dentro.contains(LineString([V[a], V[b]])):
            g.add_edge(a, b, weight=float(np.linalg.norm(V[a] - V[b])))
    if g.number_of_nodes() == 0:
        raise ValueError("el esqueleto del contorno quedó vacío")
    g = g.subgraph(max(nx.connected_components(g), key=len)).copy()

    # Diámetro del árbol: dos barridas de Dijkstra.
    d1 = nx.single_source_dijkstra_path_length(g, next(iter(g.nodes)))
    a = max(d1, key=d1.get)
    d2, caminos = nx.single_source_dijkstra(g, a)
    b = max(d2, key=d2.get)
    camino = caminos[b]

    return _suavizar(V[camino])


def bordes(poli: Polygon, curva: np.ndarray, paso: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    """Parte el contorno de la U en sus dos orillas (labial y lingual).

    El corte se hace en los puntos del contorno más cercanos a los extremos de
    la curva central, que están en las puntas posteriores de la U. Cuál orilla
    es cuál no importa para el ajuste.
    """
    anillo = _remuestrear(poli.exterior, paso)
    i0 = int(np.argmin(np.linalg.norm(anillo - curva[0], axis=1)))
    i1 = int(np.argmin(np.linalg.norm(anillo - curva[-1], axis=1)))
    anillo = np.roll(anillo, -i0, axis=0)
    k = (i1 - i0) % len(anillo)
    return anillo[: k + 1], np.vstack([anillo[k:], anillo[:1]])


def _suavizar(curva: np.ndarray, veces: int = 3) -> np.ndarray:
    """Smooth Polyline: promedio con los vecinos, extremos fijos."""
    c = curva.copy()
    for _ in range(veces):
        c[1:-1] = (c[:-2] + 2 * c[1:-1] + c[2:]) / 4.0
    return c


def punto_medio(curva: np.ndarray) -> np.ndarray:
    """Punto a mitad de longitud de la polilínea."""
    seg = np.linalg.norm(np.diff(curva, axis=0), axis=1)
    acum = np.concatenate([[0.0], np.cumsum(seg)])
    t = acum[-1] / 2.0
    i = int(np.searchsorted(acum, t, side="right") - 1)
    i = min(i, len(seg) - 1)
    f = (t - acum[i]) / seg[i] if seg[i] > 0 else 0.0
    return curva[i] + f * (curva[i + 1] - curva[i])


@dataclass
class Cuadrante:
    angulo: float  # grados, del vector posterior al eje Y, antihorario
    giro: float  # grados que se gira el plano (0, 90, 180 o 270)


def cuadrante(curva: np.ndarray) -> Cuadrante:
    """Ángulo del vector (medio de la curva -> origen) al eje Y y giro a aplicar."""
    v = -punto_medio(curva)  # el origen es (0, 0) en el marco de la base
    ang = float(np.degrees(np.arctan2(v[0], v[1])) % 360.0)  # de v hacia +Y, antihorario
    a = ang - 360.0 if ang >= 315.0 else ang
    for lo, hi, giro in CUADRANTES:
        if lo <= a <= hi:
            return Cuadrante(ang, giro)
    raise AssertionError(f"ángulo fuera de rango: {ang}")


def remuestrear_curva(curva: np.ndarray, paso: float = 0.25) -> np.ndarray:
    """Puntos equiespaciados por longitud (los del Voronoi no lo están)."""
    seg = np.linalg.norm(np.diff(curva, axis=0), axis=1)
    acum = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.linspace(0.0, acum[-1], max(16, int(acum[-1] / paso)))
    return np.column_stack([np.interp(t, acum, curva[:, 0]), np.interp(t, acum, curva[:, 1])])


def giro_simetria(
    curva: np.ndarray, rango: float = 50.0, recorte: float = 0.75
) -> tuple[float, float]:
    """Ángulo (grados, antihorario) del eje de simetría de la curva respecto de +Y.

    Método de KeepModelReferencer: se refleja la curva sobre cada eje candidato
    y se mide cuánto se aparta de sí misma. Se usa un percentil y no el promedio
    porque los modelos se recortan asimétricos (una rama más larga que la otra)
    y la cola que sobra no tiene que arrastrar el resultado. No depende del
    tamaño de la arcada, a diferencia de la plantilla de 4 puntos.

    Devuelve (ángulo, residuo en mm).
    """
    from scipy.optimize import minimize_scalar
    from scipy.spatial import cKDTree

    Q = remuestrear_curva(curva)
    Q = Q - Q.mean(axis=0)
    arbol = cKDTree(Q)

    def costo(grados):
        t = np.radians(grados)
        n = np.array([np.cos(t), np.sin(t)])  # normal al eje (el eje es +Y girado t)
        d, _ = arbol.query(Q - 2.0 * np.outer(Q @ n, n))
        return float(np.quantile(d, recorte))

    grilla = np.arange(-rango, rango + 0.01, 0.5)
    g0 = grilla[int(np.argmin([costo(g) for g in grilla]))]
    fino = minimize_scalar(costo, bounds=(g0 - 0.5, g0 + 0.5), method="bounded")
    return float(fino.x), float(fino.fun)


def rotacion_z(grados: float) -> np.ndarray:
    """Transformación que expresa la geometría en el plano girado `grados` (CCW)."""
    t = np.radians(grados)
    m = np.eye(4)
    m[:2, :2] = [[np.cos(t), np.sin(t)], [-np.sin(t), np.cos(t)]]
    return m
