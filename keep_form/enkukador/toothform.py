"""Salida con referencias para el robot, en el formato de ToothFORM.

Por cada modelo deja, como ToothFORM:

    <modelo>.stl        la pieza para imprimir: hueca, con placa, agujeros del
                        robot, llave, QR en relieve y texto calado
    <modelo>-gum.stl    la encía sin lo que tapan los dientes, cortada en z=0
                        (superficie abierta; va al cálculo de la curva de corte)
    <modelo>-tooth.ply  los dientes, cerrados, en PLY ASCII (ídem)

Todo en el marco de ToothFORM: base original en z=-0,5 (se cortan 0,5 mm),
el lado más largo del casco convexo del contorno de corte paralelo a Y,
incisivos hacia +X y origen en el agujero del centro del QR, que queda en el
medio de la arcada en Y y a una distancia fija del modelo en X (ver `marco`:
es el algoritmo de Toothform.exe, leído del binario).

Lo medido está en docs/TOOTHFORM_REFERENCIAS.md. Lo que es provisorio (falta
confirmarlo con más pares) está marcado PROVISORIO.

Copia de Enkukador (ver __init__.py). Arreglos de keep-form: la pieza usa el
plan B de la costura (hueco._restar) como ya hacía hueco.vaciar, y ni `_tm` ni
hueco.unir juntan vértices (rompía las uniones donde dos partes se tocan por
una arista). Y dos cambios para que corra adentro del plugin: el texto sale de la DejaVu Sans Bold que viene en esta carpeta,
leída con fontTools, en vez de matplotlib; y `exportar` devuelve los bytes de
cada archivo en vez de escribirlos, porque el plugin escribe por el port fs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import trimesh

from . import arco, base, hueco


@dataclass
class Parametros:
    """Los de `config.json` de ToothFORM que usamos (mismos nombres)."""

    ShellThickness: float = 1.8
    BottomPlaneThickness: float = 1.0
    LimitR: float = 98.0
    LimitH: float = 23.0
    QrSize: float = 15.0
    Radius1: float = 2.62
    Radius2: float = 2.12
    # Lugar libre que necesita la pinza del robot alrededor de la lengüeta del
    # QR, que es por donde toma la pieza. 2.5 en el JSON de esta instalación;
    # el documento de ToothFORM dice que nunca menos de 2; 3.0 en el JSON de
    # ejemplo del fabricante. Hoy sólo decide si se avisa (ver pasillo_qr).
    LimitX: float = 2.5


# --- medidas fijas (BZ219-L05-A y BZ254-U05-A, iguales en los dos) ---
CORTE = 0.5  # mm por encima de la base original
PLACA = 2.4  # espesor de la placa central
RELIEVE_QR = 0.8
R1_X = 20.0  # agujero bajo los incisivos, sobre el eje X
R1_CONO_Z = (3.16, 5.71)  # el cilindro pasa a cono de 30° y termina plano
CHAFLAN = (0.55, 0.47)  # chaflán de abajo de R2 y R1 (45°)
LLAVE = [(9.0, -2.0), (10.5, -2.0), (12.0, 0.0), (12.0, 2.0), (9.0, 2.0)]
LENGUETA = (-8.0, 8.5, -8.0, 8.0)  # x0, x1, y0, y1
TEXTO_CUERPO = 3.44  # mm de cuerpo (Verdana Bold): letras de 2,5 mm de alto
TEXTO_POS = 0.70  # fracción del largo de cada rama, desde el frente
PUENTE_ANCHO = 14.4  # largo del puente a lo largo de la arcada
# Dónde queda el modelo respecto del QR en X (ver marco, paso 4). El 12,5 es
# 12 + 0,5 de Core.dll. El 26 es 41 más un valor de configuración de Core.dll
# que no se ve desde afuera; está medido en BZ254-L22-A (único par en que
# mandó esta regla y no la lingual) y hay que confirmarlo con más pares.
QR_FRENTE_X = 26.0
QR_LINGUAL_X = 12.5
# PROVISORIO: borde de la placa bajo los incisivos (dos rectas medidas).
PLACA_FRENTE = [(8.5, -10.5), (32.0, -8.0), (32.0, 8.0), (8.5, 10.5)]


# Los estados del log, con los nombres de ToothFORM ("Toothform Error Meanings
# and Solutions", el documento del fabricante):
EXPORTO = "Export successfully"
MACIZO = "Fail to hollow, changed to solid"  # exportado igual: "No modification is required"
EXPORTADOS = (EXPORTO, MACIZO)
FALLO_PREPROCESO = "Failed to preprocess"
FALLO_VACIADO = "Failed to hollow"
FALLO_UNION_QR = "Failed to merge"
CLIP = "Clip limit exceeded"


class FallaToothform(Exception):
    """Un modelo que no sale, con el estado que pondría ToothFORM en su log."""

    def __init__(self, estado: str, detalle: str):
        super().__init__(f"{estado}: {detalle}")
        self.estado, self.detalle = estado, detalle


@dataclass
class Resultado:
    pieza: trimesh.Trimesh
    encia: trimesh.Trimesh
    dientes: trimesh.Trimesh
    matriz: np.ndarray  # entrada -> marco ToothFORM
    estado: str = EXPORTO
    avisos: list[str] = field(default_factory=list)
    holgura_qr: float | None = None  # margen del pasillo de la pinza hacia el QR (ver pasillo_qr)


# ------------------------------------------------------------------ marco


def _curva(local: trimesh.Trimesh) -> tuple[np.ndarray, object]:
    poli = arco.contorno_base(local, CORTE)
    return arco.remuestrear_curva(arco.curva_central(poli), 0.1), poli


def _cruces_x(malla: trimesh.Trimesh, y: float, z: float) -> np.ndarray:
    """x de los cruces de la malla con la recta paralela a X que pasa por (y, z), ordenados."""
    loc, _, _ = malla.ray.intersects_location(
        np.array([[-1e3, y, z]]), np.array([[1.0, 0.0, 0.0]]), multiple_hits=True
    )
    return np.sort(np.asarray(loc, float).reshape(-1, 3)[:, 0]) if len(loc) else np.empty(0)


def _cruces_y(malla: trimesh.Trimesh, x: float, y: float, z: float) -> tuple[float, float]:
    """Distancias (hacia +Y, hacia -Y) a la pared más cercana desde (x, y, z)."""
    o = np.array([[x, y, z], [x, y, z]])
    loc, ir, _ = malla.ray.intersects_location(o, np.array([[0, 1.0, 0], [0, -1.0, 0]]), multiple_hits=True)
    t = np.asarray(loc, float).reshape(-1, 3)[:, 1] - y
    mas, menos = t[ir == 0], t[ir == 1]
    mas, menos = mas[mas > 0], menos[menos < 0]
    return (float(mas.min()) if len(mas) else np.inf), (float(menos.max()) if len(menos) else -np.inf)


def marco(malla: trimesh.Trimesh, p: Parametros | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Matriz entrada -> marco ToothFORM y la curva central (a CORTE) en ese marco.

    Es lo que hace Toothform.exe (release 20260518, leído del binario y
    comprobado contra pares entrada/salida de BZ219 y BZ254: giro y origen
    coinciden a menos de 0,03 mm):

    1. Plano de la base, Z hacia adentro del modelo; se cortan CORTE mm.
    2. Giro en Z: el lado más largo del casco convexo del contorno de corte
       queda paralelo a Y (en la U es la recta que cierra la arcada por
       atrás, entre los dos extremos). Los incisivos quedan hacia +X.
    3. Origen: desde el centroide de los vértices, siete rectas paralelas a
       Y a 2 mm sobre el corte, en x = xc + 2k (k = -3..3). Cada una da el
       punto medio entre las dos paredes más cercanas (vale si las dos están
       a 3 mm o más y el medio a menos de 20); el origen es el promedio de
       esos puntos. Así y = 0 queda en el medio de la arcada.
    4. Corrimiento en X (DzCreateDentalQrCommand::IsAutoCutSupportedQr, en
       Core.dll): con rectas paralelas a X a 0,1 mm sobre el corte, se corre
       el modelo lo máximo entre dejar su frente (último cruce en y = 0) en
       QR_FRENTE_X y dejar su cara lingual (primer cruce en y = ±(QrSize+1)/2,
       el ancho del QR con su margen) en QR_LINGUAL_X.
    """
    p = p or Parametros()
    encia = base.pieza_principal(malla)
    b = base.detectar_base(encia)
    M = b.plano.a_local()  # base en z=0, Z hacia adentro del modelo
    enc = encia.copy()
    enc.apply_transform(M)
    curva, poli = _curva(enc)
    # 2. Giro: el lado más largo del casco convexo del contorno va a Y, y X
    # apunta de ese lado hacia la arcada (del fondo de la U a los incisivos).
    casco = np.array(poli.convex_hull.exterior.coords)
    seg = np.diff(casco, axis=0)
    i = int(np.argmax(np.linalg.norm(seg, axis=1)))
    d = seg[i] / np.linalg.norm(seg[i])
    n = np.array([-d[1], d[0]])
    if np.dot(np.array(poli.centroid.coords[0]) - casco[i], n) < 0:
        n = -n
    G = arco.rotacion_z(np.degrees(np.arctan2(n[1], n[0])))  # expresa la geometría con X = n
    local = malla.copy()
    local.apply_transform(G @ M)
    c = curva @ G[:2, :2].T
    # 3. Origen: el medio de la arcada a 2 mm sobre el corte.
    v = local.vertices[local.vertices[:, 2] >= CORTE]
    xc, yc = v[:, :2].mean(axis=0)
    medios = []
    for k in range(-3, 4):
        x = xc + 2.0 * k
        mas, menos = _cruces_y(local, x, yc, CORTE + 2.0)
        medio = (mas + menos) / 2
        if abs(mas) >= 3.0 and abs(menos) >= 3.0 and abs(medio) < 20.0:
            medios.append((x, yc + medio))
    if not medios:
        raise ValueError("no se encontró el medio de la arcada para ubicar el QR")
    ox, oy = np.mean(medios, axis=0)
    # 4. Corrimiento en X. Las rectas van a 0,1 mm sobre el corte, donde sólo
    # hay encía; los cruces se miden respecto del origen de recién.
    w = (p.QrSize + 1.0) * 0.5
    frente = _cruces_x(local, oy, CORTE + 0.1)
    lingual = [_cruces_x(local, oy + s * w, CORTE + 0.1) for s in (1.0, -1.0)]
    if len(frente) < 2 or not all(len(l) for l in lingual):
        raise ValueError("la recta a 0,1 mm sobre el corte no cruza el modelo: no se puede ubicar el QR")
    dx = max(QR_FRENTE_X - (frente.max() - ox), QR_LINGUAL_X - (min(l.min() for l in lingual) - ox))
    T = np.eye(4)
    T[:3, 3] = [dx - ox, -oy, -CORTE]
    return T @ G @ M, c + T[:2, 3]


# ------------------------------------------------------------------ mallas


def _mf(t: trimesh.Trimesh):
    import manifold3d as mf

    m = mf.Manifold(mf.Mesh(np.asarray(t.vertices, np.float32), np.asarray(t.faces, np.uint32)))
    if m.status() != mf.Error.NoError:
        raise ValueError(f"malla no cerrada para la booleana: {m.status()}")
    return m


def _tm(m) -> trimesh.Trimesh:
    # Sin juntar vértices, por lo mismo que hueco.unir: lo que sale de
    # manifold3d ya es cerrado, y juntarlos lo rompe donde dos partes se tocan
    # por una arista (después ni manifold3d la acepta de vuelta).
    k = m.to_mesh()
    return trimesh.Trimesh(k.vert_properties[:, :3], k.tri_verts, process=False)


def _prisma(poli, z0: float, z1: float):
    """Extrusión de un polígono shapely (con agujeros) entre z0 y z1."""
    import manifold3d as mf

    polis = getattr(poli, "geoms", [poli])
    contornos = []
    for p in polis:
        contornos.append(np.asarray(p.exterior.coords)[:-1])
        contornos += [np.asarray(i.coords)[:-1] for i in p.interiors]
    cs = mf.CrossSection(contornos, mf.FillRule.EvenOdd)
    return mf.Manifold.extrude(cs, z1 - z0).translate((0, 0, z0))


def _cono(r0: float, r1: float, z0: float, z1: float, n: int = 96):
    import manifold3d as mf

    return mf.Manifold.cylinder(z1 - z0, r0, r1, n).translate((0, 0, z0))


# ------------------------------------------------------------------ texto


FUENTE = Path(__file__).with_name("DejaVuSans-Bold.ttf")


@lru_cache(maxsize=1)
def _fuente():
    """La fuente del texto, como `fontTools.ttLib.TTFont`.

    ToothFORM usa Verdana Bold. Enkukador la toma del sistema si está y si no
    usa DejaVu Sans Bold, que es casi igual (letras a ±0,15 mm). Acá va siempre
    DejaVu, que viene en esta carpeta: no depende de qué fuentes tenga la PC
    ni de leer fuera de lo que el plugin tiene permitido.
    """
    from fontTools.ttLib import TTFont

    return TTFont(str(FUENTE))


def _contornos_glifo(fuente, nombre: str, escala: float, dx: float) -> list[np.ndarray]:
    """Los contornos de un glifo como polilíneas, en mm, corridos `dx`."""
    from fontTools.pens.basePen import BasePen

    class Pluma(BasePen):
        def __init__(self, glifos):
            super().__init__(glifos)
            self.contornos: list[list] = []
            self.actual: list = []

        def _moveTo(self, p):
            self.actual = [p]

        def _lineTo(self, p):
            self.actual.append(p)

        def _qCurveToOne(self, p1, p2):
            p0 = self.actual[-1]
            for t in np.linspace(0, 1, 9)[1:]:
                self.actual.append(tuple(
                    (1 - t) ** 2 * np.array(p0) + 2 * (1 - t) * t * np.array(p1) + t**2 * np.array(p2)
                ))

        def _curveToOne(self, p1, p2, p3):
            p0 = self.actual[-1]
            for t in np.linspace(0, 1, 13)[1:]:
                self.actual.append(tuple(
                    (1 - t) ** 3 * np.array(p0) + 3 * (1 - t) ** 2 * t * np.array(p1)
                    + 3 * (1 - t) * t**2 * np.array(p2) + t**3 * np.array(p3)
                ))

        def _closePath(self):
            if len(self.actual) > 2:
                self.contornos.append(self.actual)
            self.actual = []

        _endPath = _closePath

    glifos = fuente.getGlyphSet()
    pluma = Pluma(glifos)
    glifos[nombre].draw(pluma)
    return [np.array(c, float) * escala + [dx, 0.0] for c in pluma.contornos]


def texto(cadena: str):
    """Polígonos del texto (shapely), sin contadores, centrados en el origen.

    Sin contadores porque el texto se cala de lado a lado: el centro de una B
    o un 0 quedaría suelto. Así lo hace ToothFORM. El cuerpo es TEXTO_CUERPO,
    como el `size` de `TextPath` en Enkukador; sin kerning.
    """
    import shapely
    from shapely.geometry import Polygon

    f = _fuente()
    escala = TEXTO_CUERPO / f["head"].unitsPerEm
    cmap, avances = f.getBestCmap(), f["hmtx"].metrics
    contornos, x = [], 0.0
    for letra in cadena:
        nombre = cmap.get(ord(letra), ".notdef")
        contornos += _contornos_glifo(f, nombre, escala, x)
        x += avances[nombre][0] * escala
    polis = [Polygon(c).buffer(0) for c in contornos if len(c) > 2]
    ext = [p for p in polis if not any(q is not p and q.contains(p) for q in polis)]
    u = shapely.unary_union(ext)
    x0, y0, x1, y1 = u.bounds
    return shapely.affinity.translate(u, -(x0 + x1) / 2, -(y0 + y1) / 2)


def _ubicar_textos(curva: np.ndarray, palabras: tuple[str, str]):
    """Cada palabra sobre la curva central, al 70% de su rama desde el frente,
    siguiendo la tangente y legible desde abajo. La primera (el caso) va en la
    rama +Y; la segunda (el modelo), en la -Y."""
    import shapely

    s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(curva, axis=0), axis=1))]
    k0 = int(np.argmax(curva[:, 0]))
    salida = []
    for palabra, signo in zip(palabras, (1, -1)):
        # rama: del frente hacia el extremo con y de ese signo
        extremo = 0 if np.sign(curva[0, 1]) == signo else len(curva) - 1
        largo = abs(s[extremo] - s[k0])
        objetivo = s[k0] + np.sign(s[extremo] - s[k0]) * TEXTO_POS * largo
        i = int(np.argmin(np.abs(s - objetivo)))
        t = curva[min(i + 10, len(curva) - 1)] - curva[max(i - 10, 0)]
        if t[0] < 0:
            t = -t  # la lectura avanza hacia el frente (+X)
        ang = np.degrees(np.arctan2(t[1], t[0]))
        g = texto(palabra)
        g = shapely.affinity.scale(g, 1, -1, origin=(0, 0))  # legible desde abajo
        g = shapely.affinity.rotate(g, ang, origin=(0, 0))
        g = shapely.affinity.translate(g, *curva[i])
        salida.append((g, curva[i], t / np.linalg.norm(t)))
    return salida


# ------------------------------------------------------------------ pieza


def _qr_modulos(texto_qr: str, lado: float):
    """Módulos del QR (shapely) centrados en el origen, vistos desde arriba.

    Como ToothFORM: alfanumérico, versión 2, corrección H, máscara 3, y
    espejado visto desde arriba (se lee derecho desde abajo).
    """
    import shapely
    import segno
    from shapely.geometry import box

    q = segno.make_qr(texto_qr, error="h", version=2, mode="alphanumeric", mask=3,
                      boost_error=False)
    m = np.flipud(np.array(q.matrix, bool))  # = girado 180° + espejado
    n = m.shape[0]
    t = lado / n
    # Cada módulo crece 5 µm: dos módulos que se tocan solo en diagonal
    # comparten una arista y la malla queda no-manifold (le pasa a ToothFORM:
    # sus no-manifold están todos en el QR). Así se solapan y se unen limpio.
    e = 0.005
    cajas = [
        box(-lado / 2 + j * t - e, lado / 2 - (i + 1) * t - e,
            -lado / 2 + (j + 1) * t + e, lado / 2 - i * t + e)
        for i, j in zip(*np.nonzero(m))
    ]
    return shapely.unary_union(cajas).intersection(box(-lado / 2, -lado / 2, lado / 2, lado / 2))


def _contorno(solido: trimesh.Trimesh):
    """Huella del sólido en z=0 (polígono shapely)."""
    import shapely

    s = solido.section([0, 0, 1], [0, 0, 1e-3])
    p, _ = s.to_2D(to_2D=np.eye(4))
    return shapely.unary_union(p.polygons_full)


def _puente(huella, centro: np.ndarray, tangente: np.ndarray):
    """Franja de PUENTE_ANCHO a lo largo de la arcada, cruzándola entera."""
    from shapely.geometry import Polygon

    n = np.array([-tangente[1], tangente[0]])
    a, b = tangente * PUENTE_ANCHO / 2, n * 30
    return Polygon([centro - a - b, centro + a - b, centro + a + b, centro - a + b]).intersection(
        huella
    )


def pieza(solido: trimesh.Trimesh, curva: np.ndarray, caso: str, modelo: str,
          p: Parametros) -> tuple[trimesh.Trimesh, bool]:
    """Sólido ya en el marco ToothFORM (cortado en z=0) -> (pieza para imprimir,
    si quedó hueca). Si no se puede vaciar, sale maciza, como ToothFORM ("Fail
    to hollow, changed to solid"); si fallan las booleanas de la placa, el QR o
    los agujeros, FallaToothform("Failed to merge")."""
    import shapely
    from shapely.geometry import Polygon, box

    huella = _contorno(solido)
    hueco_ok = True
    try:
        cavidad = hueco.cavidad(solido, p.ShellThickness, 0.4)
        try:
            hueca = _mf(hueco.coser(solido, cavidad))
        except ValueError:
            # El plan B que Enkukador ya usa en hueco.vaciar y acá faltaba: restar
            # la cavidad con manifold3d. Sin esto, "la costura no quedó cerrada"
            # perdía el modelo (BY184 U03-A, keep-form, 15/10/2026).
            hueca = _mf(hueco._restar(solido, cavidad))
    except Exception:  # noqa: BLE001 - como ToothFORM: sin hueco, sale macizo
        hueca, hueco_ok = _mf(solido), False

    # Piso: placa central (lengüeta + bajo los incisivos) y puentes con el texto.
    # Lo agregado queda 0,1 mm adentro de la pared exterior, así la unión no
    # deja caras coplanares. La placa cubre también el interior de la U.
    adentro = huella.buffer(-0.1)
    x0, x1, y0, y1 = LENGUETA
    placa = box(x0, y0, x1, y1).union(Polygon(PLACA_FRENTE).intersection(
        huella.convex_hull.buffer(-0.1)))
    textos = _ubicar_textos(curva, (caso, modelo))
    puentes = shapely.unary_union([_puente(adentro, c, t) for _, c, t in textos])
    r_macizo = p.Radius1 + p.ShellThickness
    macizo = shapely.Point(R1_X, 0).buffer(r_macizo, 96).intersection(adentro.union(placa))
    import manifold3d as mf

    todo = mf.Manifold.batch_boolean([
        hueca,
        # Desde z=-1 y después se corta todo en z=0 de una vez: así no quedan
        # piezas que se toquen solo por una arista en el piso.
        _prisma(placa, -1, PLACA),
        _prisma(puentes, -1, p.BottomPlaneThickness),
        # Macizo alrededor del centrador R1 (dentro de la cavidad), hasta tapar el cono.
        _prisma(macizo, -1, R1_CONO_Z[1] + 0.3),
        _prisma(_qr_modulos(f"{caso}{modelo}".replace("-", ""), p.QrSize), PLACA,
                PLACA + RELIEVE_QR),
    ], mf.OpType.Add).trim_by_plane((0, 0, 1), 0.0)

    # Agujeros: R2 (centro del QR) pasante, R1 ciego con cono, llave, texto.
    c2, c1 = CHAFLAN
    r2, r1 = p.Radius2, p.Radius1
    quitar = [
        _cono(r2, r2, -1, PLACA + RELIEVE_QR + 1),
        _cono(r2 + c2 + 1, r2, -1, c2),
        _cono(r1, r1, -1, R1_CONO_Z[0]).translate((R1_X, 0, 0)),
        _cono(r1, r1 - (R1_CONO_Z[1] - R1_CONO_Z[0]) * np.tan(np.radians(30)),
              R1_CONO_Z[0], R1_CONO_Z[1]).translate((R1_X, 0, 0)),
        _cono(r1 + c1 + 1, r1, -1, c1).translate((R1_X, 0, 0)),
        _prisma(Polygon(LLAVE), -1, PLACA + RELIEVE_QR + 1),
        _prisma(shapely.unary_union([g for g, _, _ in textos]), -1, p.BottomPlaneThickness + 0.01),
    ]
    todo = todo - mf.Manifold.batch_boolean(quitar, mf.OpType.Add)
    salida = _tm(todo)
    if todo.status() != mf.Error.NoError or len(salida.faces) == 0:
        raise FallaToothform(FALLO_UNION_QR, f"la placa, el QR o los agujeros no se unieron ({todo.status()})")
    return salida, hueco_ok


# ------------------------------------------------------------------ encía y dientes


def encia_y_dientes(malla: trimesh.Trimesh) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """Como -gum.stl y -tooth.ply de ToothFORM (malla ya en su marco).

    Encía: la superficie de la encía sin lo que queda dentro de algún diente,
    cortada en z=0 y tapada ahí; queda abierta solo donde apoyan los dientes.
    Dientes: tal cual, cerrados. Medido en BZ219-L05-A: de las caras de la
    encía que faltan en -gum.stl, 8266 estaban dentro de un diente y 1950
    debajo del corte; la tapa de z=0 son 1137 mm².
    """
    import manifold3d as mf

    piezas = sorted(malla.split(only_watertight=False), key=lambda q: -q.area)
    encia, dientes = piezas[0], piezas[1:]
    if dientes:
        e = _mf(encia).as_original()
        k = (e - mf.Manifold.batch_boolean([_mf(d) for d in dientes], mf.OpType.Add)).to_mesh()
        # Quedarse con las caras que vienen de la encía (manifold3d sabe de qué
        # malla original sale cada triángulo).
        de_encia = np.zeros(len(k.tri_verts), bool)
        for i, oid in enumerate(k.run_original_id):
            if oid == e.original_id():
                de_encia[k.run_index[i] // 3 : k.run_index[i + 1] // 3] = True
        encia = trimesh.Trimesh(k.vert_properties[:, :3], k.tri_verts[de_encia])
        encia.remove_unreferenced_vertices()
    encia = trimesh.intersections.slice_mesh_plane(encia, [0, 0, 1], [0, 0, 0], cap=True)
    return encia, trimesh.util.concatenate(dientes) if dientes else trimesh.Trimesh()


# ------------------------------------------------------------------ todo junto


def pasillo_qr(solido: trimesh.Trimesh, p: Parametros) -> float:
    """Margen del pasillo de la pinza hacia el QR, en mm: "Clip limit exceeded"
    de ToothFORM si es menor que LimitX.

    Es el control de Toothform.exe (release 20260518), leído del binario: con
    w = (QrSize + 1) / 2, ningún vértice del modelo puede caer en x <= 10 - w
    con |y| < w + LimitX. La pinza entra al QR desde atrás, por la parte
    abierta de la U, y necesita un pasillo del ancho del QR más LimitX de cada
    lado. Acá se devuelve min |y| - w de los vértices con x <= 10 - w; si queda
    menos que LimitX, ToothFORM rechaza. (Toothform.exe, 0x41d065-0x41d147:
    w de ParameterSettings.QrSize, la constante 10.0 en 0x44c638 y LimitX;
    estado 103 = "Clip limit exceeded".) Lo mide sobre la encía cortada, sin
    los 0,5 mm de abajo: acá, sobre el sólido ya cortado en z = 0.

    Depende de dónde quede el QR respecto de la arcada: con el marco de
    `marco` (el de ToothFORM) los pares reales dan lo mismo que ToothFORM a
    menos de 0,03 mm.
    """
    w = (p.QrSize + 1.0) * 0.5
    v = solido.vertices
    atras = v[v[:, 0] <= 10.0 - w]
    if len(atras) == 0:
        return float("inf")
    return float(np.abs(atras[:, 1]).min() - w)


def generar(malla: trimesh.Trimesh, caso: str, modelo: str,
            p: Parametros | None = None) -> Resultado:
    p = p or Parametros()
    # Todo lo de antes de vaciar (marco, encía y dientes, unión) es el
    # "preprocesamiento" de ToothFORM: si falla, no hay pieza.
    try:
        M, curva = marco(malla, p)
        m = malla.copy()
        m.apply_transform(M)
        encia, dientes = encia_y_dientes(m)
        solido = _tm(_mf(hueco.unir(m)).trim_by_plane((0, 0, 1), 0.0))
    except Exception as exc:  # noqa: BLE001
        raise FallaToothform(FALLO_PREPROCESO, f"{type(exc).__name__}: {exc}") from exc
    res = Resultado(pieza=solido, encia=encia, dientes=dientes, matriz=M)

    # Límites de la máquina: se chequean antes de armar la pieza.
    alto = float(solido.bounds[1][2])
    radio = float(np.hypot(*solido.vertices[:, :2].T).max())
    if alto > p.LimitH:
        res.estado = "Height limit exceeded"
        res.avisos.append(f"alto {alto:.2f} mm > LimitH {p.LimitH}")
        return res
    # LimitR es un diámetro: "Models with a diameter exceeding 98 mm are not
    # suitable" (documento de errores de ToothFORM). Medido alrededor del QR,
    # que es el centro de la pieza; eso último sigue sin confirmar.
    if radio > p.LimitR / 2:
        res.estado = "Radius limit exceeded"
        res.avisos.append(f"radio {radio:.2f} mm > LimitR/2 {p.LimitR / 2}")
        return res
    # Sólo aviso todavía, hasta que el proveedor confirme el criterio y el
    # LimitX que usa la instalación. Con el marco de ToothFORM los casos
    # reales dan: aceptados BZ254-U06-A 2,55, BZ219-U05-A 2,55 y BZ254-L22-A
    # 5,66; rechazados BZ254-U00-B 2,45 y U01-A 2,44 (separa justo con 2,5).
    # BZ472-U00-B, que ToothFORM rechazó, da 3,49: eso no lo explica este
    # control, y es lo que falta aclarar antes de rechazar.
    res.holgura_qr = pasillo_qr(solido, p)
    if res.holgura_qr < p.LimitX:
        res.avisos.append(
            f"el pasillo de la pinza hacia el QR deja {res.holgura_qr:.2f} mm y LimitX es {p.LimitX}: "
            "ToothFORM daría 'Clip limit exceeded' (sólo aviso hasta confirmarlo con el proveedor)"
        )
    try:
        res.pieza, hueca = pieza(solido, curva, caso, modelo, p)
    except FallaToothform:
        raise
    except Exception as exc:  # noqa: BLE001 - el vaciado ya se atajó adentro: es la placa, el QR o los agujeros
        raise FallaToothform(FALLO_UNION_QR, f"{type(exc).__name__}: {exc}") from exc
    if not hueca:
        res.estado = MACIZO
        res.avisos.append("no se pudo vaciar: sale maciza, como hace ToothFORM")
    return res


def ply_ascii(malla: trimesh.Trimesh) -> bytes:
    """La malla en PLY ASCII, como el -tooth.ply de ToothFORM."""
    import io

    v, f = malla.vertices, malla.faces
    fh = io.StringIO()
    fh.write("ply\nformat ascii 1.0\n")
    fh.write(f"element vertex {len(v)}\nproperty float x\nproperty float y\nproperty float z\n")
    fh.write(f"element face {len(f)}\nproperty list uchar int vertex_indices\nend_header\n")
    np.savetxt(fh, v, fmt="%.6f")
    np.savetxt(fh, np.c_[np.full(len(f), 3), f], fmt="%d")
    return fh.getvalue().encode("ascii")


def exportar(res: Resultado, nombre: str) -> dict[str, bytes]:
    """Los archivos como ToothFORM, {nombre de archivo: bytes}: <nombre>.stl
    (si exportó, hueca o maciza), -gum.stl y -tooth.ply. Quien llama los escribe."""
    archivos = {}
    if res.estado in EXPORTADOS:
        archivos[f"{nombre}.stl"] = res.pieza.export(file_type="stl")
    archivos[f"{nombre}-gum.stl"] = res.encia.export(file_type="stl")
    archivos[f"{nombre}-tooth.ply"] = ply_ascii(res.dientes)
    return archivos
