"""Salud de malla de una pieza: lo que bloquea el nesting (falla), lo que solo se avisa, y lo
que se cura solo antes de decidir.

Copia de `salud.py` de OperacionesMejoras/model-arranger (commit 17bcb9b, KeepNesting 0.2.0,
"cura de mallas"). Un solo cambio: los agujeros se triangulan con `_orejas` (recorte de
orejas, abajo) en vez de mapbox_earcut, que keep-form no tiene entre sus librerias curadas;
si no puede, el mismo abanico desde el centroide que ya usaba. Lo que agrega keep-form para
sus tools esta en `mallas.py`, para que esta copia se pueda volver a sincronizar.

Medido sobre escaneos reales de produccion (BY740, BY765, 2026-10-01/02):
- Ninguno es estanco (`is_watertight`): todos tienen 30-70 aristas compartidas por 4 caras,
  sin caras duplicadas. Son las cajitas del texto de la etiqueta (extruidas de la placa, de
  0.8 mm de alto) tocandose esquina con esquina: dos partes del mismo solido unidas por una
  arista. Imprime bien y el test de cierre de Prismlab Model Layouter lo ACEPTA (probado el
  2026-10-02 con cubos minimos: solo rechaza agujeros). Queda como aviso; `_desdoblar` (2 um
  de separacion y una cremallera) existe por si otro software exige variedad, apagado.
- Algunos (1 de 3 BY740 mirados; 33 de 65 piezas de la cama N-0001 del 2026-10-02) tienen
  aristas de borde: agujeros chicos de escaneo (3-22 aristas por pieza), a veces con caras
  degeneradas y orientacion inconsistente alrededor. Un agujero es un modelo abierto y el
  software de la impresora rechaza la cama ENTERA (paso con N-0001, que habia pasado la salud
  con el agujero como aviso). Asi que es falla, pero antes se intenta curar (`curar`): caras
  degeneradas y duplicadas afuera, fragmentos sueltos afuera, orientacion corregida y
  agujeros tapados, hasta PASADAS vueltas. Si sigue abierta, falla y se mira aparte.
- La base apoyada en Z=0 cubre 67-73 % de la silueta: el resto es lo que sobresale arriba
  (la silueta llega a su ancho maximo a ~4 mm de altura). Debajo de BASE_MIN la pieza esta
  inclinada o sin base plana y no imprime bien: falla. No es salud de malla: no se cura.
"""
import numpy as np
import trimesh
from trimesh.path.polygons import projected

BASE_MIN = 50.0     # % de la silueta que tiene que apoyar en el plano
BASE_TOL = 0.05     # mm: una cara es "de base" si esta toda a menos de esto del z minimo

PASADAS = 4         # vueltas de cura antes de darse por vencido
LAZO_MAX = 300      # aristas: un agujero mas grande no es de escaneo (falta la base, malla rota); no se tapa
FRAGMENTO_MAX = 50  # caras: un cuerpo suelto tan chico es ruido de escaneo y se quita


def _aristas(m):
    """(aristas unicas ordenadas, indice de cada arista de m.edges en ellas, cuantas caras la usan)."""
    u, inv, cnt = np.unique(m.edges_sorted, axis=0, return_inverse=True, return_counts=True)
    return u, inv.ravel(), cnt


def estado(m):
    """Lo que mira la salud, en numeros: aristas de borde (agujeros), aristas con mas de dos
    caras, caras degeneradas, orientacion consistente, cuerpos."""
    _, _, cnt = _aristas(m)
    degeneradas = ~m.nondegenerate_faces() | (m.area_faces < 1e-10)
    return dict(borde=int((cnt == 1).sum()), multiples=int((cnt > 2).sum()),
                degeneradas=int(degeneradas.sum()), winding=bool(m.is_winding_consistent),
                cuerpos=int(m.body_count))


def _lazos(m):
    """Lazos de aristas de borde (un lazo = un agujero), cada uno como lista de vertices en el
    sentido en que los recorre su unica cara. Devuelve (lazos, cuantos no cierran)."""
    _, inv, cnt = _aristas(m)
    salidas = {}
    for a, b in m.edges[cnt[inv] == 1].tolist():
        salidas.setdefault(a, []).append(b)
    lazos, rotos = [], 0
    for a in list(salidas):
        if not salidas.get(a):
            continue
        lazo, v = [], a
        while True:
            sal = salidas.get(v)
            if not sal:            # vertice sin salida: borde que no cierra (malla rota)
                rotos += 1
                lazo = None
                break
            lazo.append(v)
            v = sal.pop()
            if v == a:
                break
        if lazo:
            lazos.append(lazo)
    return lazos, rotos


def _orejas(uv):
    """Triangulacion de un poligono simple por recorte de orejas: (k-2, 3) indices en uv, cada
    triangulo antihorario, o None si no es simple (ahi se usa el abanico). O(k^2) con k <=
    LAZO_MAX, de sobra para agujeros de escaneo. Reemplaza a mapbox_earcut (ver arriba)."""
    k = len(uv)
    idx = list(range(k))
    x, y = uv[:, 0], uv[:, 1]
    if np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) < 0:
        idx.reverse()                                    # se recorta siempre en sentido antihorario
    tris, eps = [], 1e-12
    while len(idx) > 3:
        n = len(idx)
        for i in range(n):
            a, b, c = idx[i - 1], idx[i], idx[(i + 1) % n]
            pa, pb, pc = uv[a], uv[b], uv[c]
            if (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0]) <= eps:
                continue                                 # vertice reflejo o alineado: no es oreja
            otros = uv[[j for j in idx if j not in (a, b, c)]]
            if len(otros):
                d1 = (pb[0] - pa[0]) * (otros[:, 1] - pa[1]) - (pb[1] - pa[1]) * (otros[:, 0] - pa[0])
                d2 = (pc[0] - pb[0]) * (otros[:, 1] - pb[1]) - (pc[1] - pb[1]) * (otros[:, 0] - pb[0])
                d3 = (pa[0] - pc[0]) * (otros[:, 1] - pc[1]) - (pa[1] - pc[1]) * (otros[:, 0] - pc[0])
                if ((d1 >= -eps) & (d2 >= -eps) & (d3 >= -eps)).any():
                    continue                             # hay otro vertice adentro: no es oreja
            tris.append((a, b, c))
            idx.pop(i)
            break
        else:
            return None                                  # ninguna oreja: no es simple
    tris.append(tuple(idx))
    return np.array(tris)


def _tapar(m, lazo, idx_nuevo):
    """Triangulos que tapan el lazo, orientados al reves de sus caras vecinas (asi la
    orientacion queda consistente). Devuelve (caras, vertices nuevos): earcut sobre el plano
    de mejor ajuste, y si no puede, un abanico desde el centroide (un vertice nuevo)."""
    r = lazo[::-1]
    k = len(r)
    if k == 3:
        return np.array([r]), np.zeros((0, 3))
    P = m.vertices[r]
    c = P.mean(0)
    _, _, vt = np.linalg.svd(P - c, full_matrices=False)
    uv = (P - c) @ vt[:2].T
    tris = _orejas(uv)
    if tris is None:
        tris = np.zeros((0, 3), int)
    if len(tris) != k - 2:
        return np.array([[r[i], r[(i + 1) % k], idx_nuevo] for i in range(k)]), c[None]
    # la orientacion se compara con el area con signo del poligono (como hacia con earcut)
    poli = np.sum(uv[:, 0] * np.roll(uv[:, 1], -1) - np.roll(uv[:, 0], -1) * uv[:, 1])
    a, b, d = uv[tris[:, 0]], uv[tris[:, 1]], uv[tris[:, 2]]
    areas = (b[:, 0] - a[:, 0]) * (d[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (d[:, 0] - a[:, 0])
    if areas[np.abs(areas).argmax()] * poli < 0:
        tris = tris[:, ::-1]
    return np.array(r)[tris], np.zeros((0, 3))


def _unir_t(m):
    """Astillas: caras de area ~0 con tres vertices distintos. El vertice del medio cae sobre la
    arista mas larga, que la cara vecina de enfrente tiene entera (union en T). Quitarlas sin
    mas abre un agujero de 3 aristas que se tapa con la misma astilla; lo que cierra bien es
    partir la vecina por el vertice del medio y quitar la astilla. Devuelve (malla, resueltas).
    """
    f = m.faces
    astillas = np.flatnonzero(~m.nondegenerate_faces() | (m.area_faces < 1e-10))
    if not len(astillas):
        return m, 0
    es_astilla = np.zeros(len(f), bool)
    es_astilla[astillas] = True
    _, inv, cnt = _aristas(m)
    por_cara = inv.reshape(-1, 3)                        # arista unica de (0,1), (1,2), (2,0) de cada cara
    orden = np.argsort(inv, kind="stable")
    caras_de = np.repeat(np.arange(len(f)), 3)[orden]    # caras agrupadas por arista unica
    inicio = np.searchsorted(inv[orden], np.arange(len(cnt)))
    nuevas, quitar, tocadas = [], [], set()
    for s in astillas:
        tri = m.triangles[s]
        k = int(np.linalg.norm(tri - np.roll(tri, -1, axis=0), axis=1).argmax())
        a, b, c = f[s, k], f[s, (k + 1) % 3], f[s, (k + 2) % 3]
        e = por_cara[s, k]
        vecinas = [q for q in caras_de[inicio[e]:inicio[e] + cnt[e]] if q != s]
        if len(vecinas) != 1 or vecinas[0] in tocadas or es_astilla[vecinas[0]]:
            continue
        F = f[vecinas[0]]
        for r in range(3):                               # la arista {a,b} como primeros dos vertices de F
            p, q, d = F[r], F[(r + 1) % 3], F[(r + 2) % 3]
            if {p, q} == {a, b}:
                break
        else:
            continue
        nuevas += [[p, c, d], [c, q, d]]                  # mismo sentido p->q que tenia F
        quitar += [s, vecinas[0]]
        tocadas.add(vecinas[0])
    if not nuevas:
        return m, 0
    keep = np.ones(len(f), bool)
    keep[quitar] = False
    return trimesh.Trimesh(m.vertices, np.vstack([f[keep], nuevas]), process=False), len(nuevas) // 2


def _inconsistentes(m):
    """Mascara de caras que comparten una arista de exactamente dos caras recorriendola en el
    MISMO sentido: superficie plegada o duplicada por el escaner, que ninguna vuelta de caras
    arregla (orientar una rompe su otra arista). Se quitan las dos y el hueco se tapa."""
    _, inv, cnt = _aristas(m)
    orden = np.argsort(inv, kind="stable")
    inicio = np.searchsorted(inv[orden], np.arange(len(cnt)))
    dos = np.flatnonzero(cnt == 2)
    o1, o2 = orden[inicio[dos]], orden[inicio[dos] + 1]   # las dos apariciones de cada arista en m.edges
    mismo = (m.edges[o1] == m.edges[o2]).all(1)
    malas = np.zeros(len(m.faces), bool)
    malas[o1[mismo] // 3] = True                          # m.edges tiene 3 filas por cara, en orden
    malas[o2[mismo] // 3] = True
    return malas


def _orientar(m):
    """Deja las caras de cada cuerpo con la misma orientacion (lo que hace
    trimesh.repair.fix_winding, pero sin networkx): recorre la adyacencia en anchura y da
    vuelta la cara que recorre la arista compartida en el mismo sentido que su vecina ya
    visitada. Devuelve (malla, caras dadas vuelta)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import breadth_first_order, connected_components

    f = m.faces
    adj, aristas = trimesh.graph.face_adjacency(f, return_edges=True)
    if not len(adj):
        return m, 0

    def sentido(caras, u, v):  # +1 si la cara recorre u->v, -1 si v->u
        pu = (f[caras] == u[:, None]).argmax(1)
        pv = (f[caras] == v[:, None]).argmax(1)
        return np.where((pv - pu) % 3 == 1, 1, -1)

    opuestas = sentido(adj[:, 0], aristas[:, 0], aristas[:, 1]) != sentido(adj[:, 1], aristas[:, 0], aristas[:, 1])
    indice = {(int(a), int(b)): i for i, (a, b) in enumerate(np.sort(adj, axis=1))}
    g = coo_matrix((np.ones(len(adj)), (adj[:, 0], adj[:, 1])), shape=(len(f), len(f))).tocsr()
    n_comp, etiq = connected_components(g, directed=False)
    flip = np.zeros(len(f), bool)
    for comp in range(n_comp):
        raiz = int(np.flatnonzero(etiq == comp)[0])
        orden, pred = breadth_first_order(g, raiz, directed=False, return_predecessors=True)
        for v in orden[1:].tolist():
            p = int(pred[v])
            flip[v] = flip[p] ^ (not opuestas[indice[(min(p, v), max(p, v))]])
    if not flip.any():
        return m, 0
    f = f.copy()
    f[flip] = f[flip][:, ::-1]
    return trimesh.Trimesh(m.vertices, f, process=False), int(flip.sum())


def _desdoblar(m, eps=0.002):
    """Aristas compartidas por exactamente 4 caras: dos partes del modelo que se tocan en una
    arista (en los escaneos, las cajitas del texto de la etiqueta, extruidas de la placa y
    tocandose esquina con esquina). No es variedad, y un test de cierre estricto (cada arista
    = 2 caras) lo rechaza. Alrededor de la arista las 4 caras encierran dos cunas de material:
    se emparejan por eso, y las 2 caras de una de las partes pasan a copias de los dos vertices
    corridas `eps` hacia su interior; el hueco de `eps` que queda entre esas caras y sus vecinas
    se cierra con 4 triangulos finos (una cremallera). Asi la arista pasa a tener 2 caras sin
    abrir nada y sin depender de como sigue la superficie alrededor (las letras comparten la
    placa y a veces la tapa). Una arista cuyas caras ya se movieron en esta vuelta se deja para
    la siguiente. Devuelve (malla, resueltas, dejadas)."""
    f = m.faces.copy()
    V, N = m.vertices, m.face_normals
    u, inv, cnt = _aristas(m)
    nm = np.flatnonzero(cnt > 2)
    if not len(nm):
        return m, 0, 0
    orden = np.argsort(inv, kind="stable")
    caras_de = np.repeat(np.arange(len(f)), 3)[orden]
    inicio = np.searchsorted(inv[orden], np.arange(len(cnt)))

    nuevos, cremallera, tocadas, resueltas, dejadas = [], [], set(), 0, 0
    for e in nm:
        a, b = (int(x) for x in u[e])
        cs = [int(c) for c in caras_de[inicio[e]:inicio[e] + cnt[e]]]
        if cnt[e] != 4 or tocadas & set(cs):
            dejadas += 1
            continue
        eje = V[b] - V[a]
        eje = eje / np.linalg.norm(eje)
        ref = np.array([1.0, 0, 0]) if abs(eje[0]) < 0.9 else np.array([0, 1.0, 0])
        u1 = np.cross(eje, ref)
        u1 /= np.linalg.norm(u1)
        u2 = np.cross(eje, u1)
        alas, ang, lado = [], [], []
        for c in cs:
            o = next(x for x in f[c] if x != a and x != b)
            w = V[o] - V[a]
            w = w - eje * (w @ eje)
            if np.linalg.norm(w) < 1e-9:
                break
            w = w / np.linalg.norm(w)
            alas.append(w)
            ang.append(np.arctan2(w @ u2, w @ u1))
            lado.append(np.sign(np.cross(w, -N[c]) @ eje))  # +1: su material esta en sentido antihorario del ala
        idx = np.argsort(ang) if len(alas) == 4 else []
        pares = [(idx[k], idx[(k + 1) % 4]) for k in range(4) if lado[idx[k]] > 0 and lado[idx[(k + 1) % 4]] < 0]
        if len(pares) != 2 or len({i for p in pares for i in p}) != 4:
            dejadas += 1  # las 4 caras no encierran dos cunas (pliegue de escaner): se deja
            continue
        i, j = pares[1]                                  # la parte que se corre
        d = alas[i] + alas[j]
        d = d / np.linalg.norm(d)                        # hacia adentro de su cuna
        ia, ib = len(V) + len(nuevos), len(V) + len(nuevos) + 1
        nuevos += [V[a] + eps * d, V[b] + eps * d]
        for c in (cs[i], cs[j]):
            fc = f[c]
            l = fc.tolist()
            x = next(t for t in l if t != a and t != b)
            for v, v2 in ((a, ia), (b, ib)):
                # la cremallera (v, x, v2) recorre (v, x) como lo hacia esta cara (su vecina de
                # enfrente lo recorre al reves) y (x, v2) al reves que la cara movida
                if (l.index(x) - l.index(v)) % 3 == 1:
                    cremallera.append([v, x, v2])
                else:
                    cremallera.append([v2, x, v])
            fc[fc == a] = ia
            fc[fc == b] = ib
            tocadas.add(c)
        resueltas += 1
    if not resueltas:
        return m, 0, dejadas
    return trimesh.Trimesh(np.vstack([V, np.array(nuevos)]), np.vstack([f, np.array(cremallera)]),
                           process=False), resueltas, dejadas


def curar(m, pasadas=PASADAS, desdoblar=False):
    """Repara lo que se puede, en hasta `pasadas` vueltas. Devuelve (malla, [que se hizo], vueltas).

    Cada vuelta: vertices casi coincidentes unidos, caras degeneradas y duplicadas afuera,
    fragmentos sueltos afuera, orientacion consistente, agujeros tapados y, solo si
    `desdoblar`, aristas de 4 caras separadas (Prismlab no lo necesita: su test de cierre
    acepta esas aristas, medido el 2026-10-02; queda para un software que exija variedad).
    Para cuando la malla queda limpia o cuando una vuelta no cambia nada.
    """
    hecho, vuelta = [], 0
    while vuelta < pasadas and len(m.faces):
        vuelta += 1
        n_hecho = len(hecho)
        # 0. vertices casi coincidentes (indices distintos a < 0.1 um): micro-triangulos que
        #    como astillas se regeneran sin fin; unidos, quedan caras con vertice repetido
        nv = len(m.vertices)
        m.merge_vertices(digits_vertex=4)
        if len(m.vertices) < nv:
            hecho.append(f"{nv - len(m.vertices)} vertices casi coincidentes unidos")
        # 1. caras con un vertice repetido (no son superficie) y duplicadas afuera; astillas
        #    de area 0 resueltas como union en T
        f = m.faces
        repetido = (f[:, 0] == f[:, 1]) | (f[:, 1] == f[:, 2]) | (f[:, 0] == f[:, 2])
        if repetido.any():
            m.update_faces(~repetido)
            hecho.append(f"{int(repetido.sum())} caras con vertice repetido quitadas")
        unicas = m.unique_faces()
        if not unicas.all():
            m.update_faces(unicas)
            hecho.append(f"{int((~unicas).sum())} caras duplicadas quitadas")
        m, astillas = _unir_t(m)
        if astillas:
            hecho.append(f"{astillas} astilla(s) de area 0 resuelta(s) (union en T)")
        # 2. fragmentos sueltos (ruido de escaneo); los cuerpos grandes se dejan
        etiq = trimesh.graph.connected_component_labels(m.face_adjacency, node_count=len(m.faces))
        tam = np.bincount(etiq)
        chicos = tam <= FRAGMENTO_MAX
        if chicos.any() and not chicos.all():
            m.update_faces(~chicos[etiq])
            hecho.append(f"{int(chicos.sum())} fragmento(s) suelto(s) de hasta {FRAGMENTO_MAX} caras quitado(s)")
        # 3. orientacion: antes de tapar, porque el relleno copia la de las caras vecinas
        if not m.is_winding_consistent:
            m, volteadas = _orientar(m)
            if volteadas:
                hecho.append(f"orientacion corregida ({volteadas} caras dadas vuelta)")
        if not m.is_winding_consistent:
            malas = _inconsistentes(m)
            if malas.any():
                m.update_faces(~malas)
                hecho.append(f"{int(malas.sum())} caras de superficie plegada quitadas (el hueco se tapa)")
        if m.is_winding_consistent and m.volume < 0:
            m.invert()
            hecho.append("normales invertidas (volumen negativo)")
        # 4. agujeros
        lazos, rotos = _lazos(m)
        caras, verts, tapados, grandes = [], [], 0, 0
        for lazo in lazos:
            if len(lazo) > LAZO_MAX:
                grandes += 1
                continue
            f, v = _tapar(m, lazo, len(m.vertices) + sum(len(x) for x in verts))
            caras.append(f)
            if len(v):
                verts.append(v)
            tapados += 1
        if caras:
            m = trimesh.Trimesh(np.vstack([m.vertices, *verts]), np.vstack([m.faces, *caras]), process=False)
            hecho.append(f"{tapados} agujero(s) tapado(s) ({sum(len(f) for f in caras)} caras nuevas)")
        if grandes or rotos:
            hecho.append(f"sin tapar: {grandes} agujero(s) de mas de {LAZO_MAX} aristas, {rotos} borde(s) que no cierra(n)")
        # 5. aristas de 4 caras: partes que se tocan en una arista, separadas 2 um
        m, desdobladas, dejadas = _desdoblar(m) if desdoblar else (m, 0, 0)
        if desdobladas:
            hecho.append(f"{desdobladas} arista(s) de 4 caras desdoblada(s): partes separadas 2 um"
                         + (f" ({dejadas} sin resolver)" if dejadas else ""))
        e = estado(m)
        if e["borde"] == 0 and e["degeneradas"] == 0 and e["winding"] and (e["multiples"] == 0 or not desdoblar):
            break
        if len(hecho) == n_hecho:
            break  # esta vuelta no pudo hacer nada: insistir no cambia
    m.remove_unreferenced_vertices()
    return m, hecho, vuelta


def revisar(path, base_min=BASE_MIN, curar_malla=True):
    """{'estado': 'salud_ok'|'salud_falla', 'chequeos': [...], 'curada': bool, 'reparaciones': [...],
    'pasadas': n, + metricas (triangulos, vertices, area de la silueta, base_area, base_pct,
    superficie, volumen, alto, bbox)}, malla (ya curada; si `curada`, hay que guardarla)."""
    m = trimesh.load_mesh(path)  # process=True: une vertices, sin eso no hay cuerpos ni aristas compartidas
    ch = []

    def chequeo(nombre, ok, detalle, grave, curado=False):
        ch.append(dict(nombre=nombre, ok=bool(ok), detalle=detalle, nivel="falla" if grave else "aviso",
                       curado=bool(curado)))

    if not len(m.faces):
        chequeo("carga", False, "el STL no tiene triangulos (archivo vacio o truncado)", True)
        return dict(estado="salud_falla", chequeos=ch, triangulos=0, curada=False, reparaciones=[], pasadas=0), None
    chequeo("carga", True, f"{len(m.faces)} triangulos", True)

    antes = estado(m)
    hecho, pasadas = [], 0
    if curar_malla and (antes["borde"] or antes["degeneradas"] or not antes["winding"] or antes["cuerpos"] > 1):
        m, hecho, pasadas = curar(m)
        if not len(m.faces):
            chequeo("cura", False, "la cura dejo la malla vacia", True)
            return dict(estado="salud_falla", chequeos=ch, triangulos=0, curada=False, reparaciones=hecho, pasadas=pasadas), None
    e = estado(m)

    chequeo("un cuerpo", e["cuerpos"] == 1,
            f"{e['cuerpos']} cuerpo(s) separados" + (f" (habia {antes['cuerpos']})" if antes["cuerpos"] != e["cuerpos"] else ""),
            False, curado=antes["cuerpos"] != e["cuerpos"] and e["cuerpos"] == 1)
    if e["borde"] == 0:
        chequeo("sin agujeros", True,
                f"tenia {antes['borde']} aristas de borde: agujeros tapados" if antes["borde"] else "0 aristas de borde",
                True, curado=bool(antes["borde"]))
    else:
        chequeo("sin agujeros", False, f"{e['borde']} aristas de borde (modelo abierto: la impresora rechaza la cama)"
                + (f"; quedaron tras {pasadas} pasada(s) de cura, de {antes['borde']}" if pasadas else ""), True)
    if e["multiples"] == 0:
        chequeo("variedad", True, f"tenia {antes['multiples']} aristas de 4 caras: partes separadas 2 um"
                if antes["multiples"] else "todas las aristas con 2 caras", False, curado=bool(antes["multiples"]))
    else:
        chequeo("variedad", False, f"{e['multiples']} aristas compartidas por 4 caras (cajitas de la etiqueta que se "
                "tocan; la impresora lo acepta)", False)
    chequeo("normales consistentes", e["winding"],
            "orientacion de las caras" + (" corregida" if e["winding"] and not antes["winding"] else ""),
            False, curado=e["winding"] and not antes["winding"])
    chequeo("sin caras degeneradas", e["degeneradas"] == 0,
            f"{e['degeneradas']} caras de area 0" + (f" (quitadas {antes['degeneradas']})" if antes["degeneradas"] and not e["degeneradas"] else ""),
            False, curado=bool(antes["degeneradas"]) and not e["degeneradas"])

    zmin = m.bounds[0, 2]
    base = (m.face_normals[:, 2] < -0.99) & (m.triangles[:, :, 2].max(1) < zmin + BASE_TOL)
    area = projected(m, normal=[0, 0, 1], ignore_sign=True, precise=True).area
    base_pct = 100 * m.area_faces[base].sum() / area
    chequeo("base en Z=0", base_pct >= base_min,
            f"{base_pct:.0f} % de la silueta apoyada en el plano (minimo {base_min:.0f} %)", True)
    chequeo("en el plano", abs(zmin) < 0.01, f"z minimo {zmin:.3f} mm (se corrige al anidar)", False)

    falla = any(not c["ok"] and c["nivel"] == "falla" for c in ch)
    ext = m.bounds[1] - m.bounds[0]
    metricas = dict(
        triangulos=len(m.faces), vertices=len(m.vertices), area=round(area, 1),
        base_area=round(float(m.area_faces[base].sum()), 1), base_pct=round(base_pct, 1),
        superficie=round(float(m.area), 1), volumen=round(abs(float(m.volume)), 1),
        alto=round(float(ext[2]), 2), bbox=[round(float(x), 2) for x in ext],
    )
    m.base_mask = base  # que caras apoyan: el render de la base las pinta de otro color
    return dict(estado="salud_falla" if falla else "salud_ok", chequeos=ch, curada=bool(hecho),
                reparaciones=hecho, pasadas=pasadas, **metricas), m
