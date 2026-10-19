"""Análisis y cura de mallas cerradas, para después de exportar (tools `analizar` y `sanar`).

Los procesos que siguen a keep-form (nesting, slicer de la impresora) fallan con una malla
que no está totalmente cerrada. La cura es la de model-arranger (`salud.curar`, copiada en
`salud.py`): vértices casi coincidentes unidos, caras degeneradas y duplicadas afuera,
fragmentos sueltos afuera, orientación corregida, agujeros tapados y, acá siempre, las
aristas de 4 caras (dos partes que se tocan en una arista: non-manifold) separadas 2 um. Y,
de keep-form, los vértices non-manifold (dos partes que se tocan en un punto), también 2 um.

"Totalmente cerrada" es lo que pide un test de cierre estricto, medido sobre la malla como la
lee cualquier proceso posterior (STL con los vértices unidos por posición):
- ninguna arista de borde (agujeros),
- ninguna arista con más de dos caras (non-manifold),
- ningún vértice non-manifold (dos abanicos de caras que sólo comparten el vértice),
- ninguna cara de área 0,
- orientación consistente y volumen positivo.
"""

from __future__ import annotations

import numpy as np
import trimesh

from . import salud

# Una cura que cambia la superficie más que esto no es una reparación: algo se tapó o se quitó
# de más (un agujero que no es de escaneo, una parte entera). La pieza queda como estaba y es
# falla. Es la superficie y no el volumen porque el volumen de una malla abierta o mal
# orientada no significa nada, y la superficie sí; y una tapa grande la cambia: una esfera de
# 10 mm sin su casquete de 4 mm, tapada plana, cambia 4 %. Los agujeros de escaneo (3-22
# aristas) y las separaciones de 2 um no llegan a 0,01 %.
SUPERFICIE_MAX_PCT = 1.0


# Separación de los abanicos de un vértice non-manifold: lo mismo que `salud._desdoblar` hace
# con las aristas (2 um). Menos que eso y al guardar el STL se vuelven a unir por posición.
EPS_VERTICE = 0.002


def _abanicos(m: trimesh.Trimesh) -> np.ndarray:
    """Grupo de cada esquina de cara (3 por cara, en orden): dos esquinas del mismo vértice
    van al mismo grupo si sus caras comparten una arista que lo contiene. Un vértice sano tiene
    todas sus esquinas en un grupo; uno non-manifold (dos abanicos de caras que sólo comparten
    el vértice), en más de uno."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    f = m.faces
    adj, aristas = trimesh.graph.face_adjacency(f, return_edges=True)
    esquina = lambda caras, v: caras * 3 + (f[caras] == v[:, None]).argmax(1)  # noqa: E731
    filas = np.concatenate([esquina(adj[:, 0], aristas[:, k]) for k in (0, 1)])
    cols = np.concatenate([esquina(adj[:, 1], aristas[:, k]) for k in (0, 1)])
    n = len(f) * 3
    _, grupo = connected_components(coo_matrix((np.ones(len(filas)), (filas, cols)), shape=(n, n)), directed=False)
    return grupo


def _vertices_no_variedad(m: trimesh.Trimesh) -> int:
    if not len(m.faces):
        return 0
    pares = np.unique(np.c_[m.faces.ravel(), _abanicos(m)], axis=0)
    return int((np.bincount(pares[:, 0]) > 1).sum())


def _separar_vertices(m: trimesh.Trimesh, eps: float = EPS_VERTICE) -> tuple[trimesh.Trimesh, int]:
    """Cada vértice non-manifold pasa a un vértice por abanico: el abanico más grande se queda
    con el original y cada uno de los otros con una copia corrida `eps` hacia adentro de su
    propio abanico (hacia el promedio de sus vecinos). No abre nada: los abanicos no
    comparten aristas en ese vértice, así que cada arista sigue con las mismas caras.
    Devuelve (malla, vértices separados)."""
    f = m.faces
    if not len(f):
        return m, 0
    grupo = _abanicos(m)
    vert = f.ravel()
    pares, cuantas = np.unique(np.c_[vert, grupo], axis=0, return_counts=True)
    malos = np.flatnonzero(np.bincount(pares[:, 0]) > 1)
    if not len(malos):
        return m, 0
    V = m.vertices
    caras = f.copy().ravel()
    nuevos = []
    for v in malos.tolist():
        suyos = pares[:, 0] == v
        grupos, tam = pares[suyos, 1], cuantas[suyos]
        for g in grupos[np.argsort(-tam)][1:]:
            esq = np.flatnonzero((vert == v) & (grupo == g))
            vecinos = f[esq // 3].ravel()
            vecinos = vecinos[vecinos != v]
            d = V[vecinos].mean(0) - V[v]
            n = np.linalg.norm(d)
            nuevos.append(V[v] + (d / n * eps if n > 1e-12 else 0.0))
            caras[esq] = len(V) + len(nuevos) - 1
    return trimesh.Trimesh(np.vstack([V, nuevos]), caras.reshape(-1, 3), process=False), len(malos)


def medir(m: trimesh.Trimesh) -> dict:
    """Los números del análisis, sobre la malla tal como está."""
    e = salud.estado(m)
    e["vertices_no_variedad"] = _vertices_no_variedad(m)
    e["triangulos"] = len(m.faces)
    e["volumen"] = round(float(m.volume), 1) if len(m.faces) else 0.0
    e["superficie"] = round(float(m.area), 2)
    e["cerrada"] = bool(
        len(m.faces) and e["borde"] == 0 and e["multiples"] == 0 and e["vertices_no_variedad"] == 0
        and e["degeneradas"] == 0 and e["winding"] and m.volume > 0
    )
    return e


def problemas(e: dict) -> list[str]:
    """Lo que le falta para estar cerrada, en palabras."""
    p = []
    if e["borde"]:
        p.append(f"{e['borde']} aristas de borde (agujeros)")
    if e["multiples"]:
        p.append(f"{e['multiples']} aristas con más de 2 caras (non-manifold)")
    if e["vertices_no_variedad"]:
        p.append(f"{e['vertices_no_variedad']} vértices non-manifold")
    if e["degeneradas"]:
        p.append(f"{e['degeneradas']} caras de área 0")
    if not e["winding"]:
        p.append("orientación de caras inconsistente")
    elif e["volumen"] <= 0 and not e["borde"]:
        p.append("normales hacia adentro (volumen negativo)")
    return p


def sanar(m: trimesh.Trimesh) -> tuple[trimesh.Trimesh, dict, dict, list[str]]:
    """(malla, medida antes, medida después, qué se hizo). Si ya está cerrada no se toca."""
    antes = medir(m)
    if antes["cerrada"]:
        return m, antes, antes, []
    curada, hecho, _ = salud.curar(m.copy(), desdoblar=True)
    curada, separados = _separar_vertices(curada)
    if separados:
        hecho.append(f"{separados} vértice(s) non-manifold separado(s) 2 um")
    # Lo que la cura dejó, releído como lo va a leer el proceso siguiente: si al unir los
    # vértices por posición vuelve a abrirse, no está curada.
    releida = trimesh.load_mesh(trimesh.util.wrap_as_stream(curada.export(file_type="stl")), file_type="stl")
    despues = medir(releida)
    return releida, antes, despues, hecho
