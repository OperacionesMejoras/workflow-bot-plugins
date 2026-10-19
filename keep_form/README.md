# keep-form

Reemplazo en proceso de ToothFORM, hecho sobre Enkukador: toma el STL de
Enkukador y saca la pieza para imprimir (hueca, con placa, agujeros del robot,
llave, QR en relieve y texto), la encía y los dientes, con el marco y los
controles de ToothFORM (leídos del binario, release 20260518). El detalle está
en `enkukador/toothform.py`.

## Muesca del pasillo de la pinza (0.2.0, anotado 15/10/2026)

Apagada por defecto: con `AbrirPasillo=1` como param extra, cuando el pasillo
no da keep-form abre el lugar en la encía en vez de avisar, y da 'Clip limit
exceeded' recién cuando ni así se puede (`abrir_pasillo` en
`enkukador/toothform.py`). Sin `AbrirPasillo`, el control sigue siendo sólo
aviso, como en 0.1.9.

**Qué es.** La garra del robot toma la pieza por la lengüeta del QR, cerrando
en Y por los costados (±8 mm) y entrando desde atrás por la parte abierta de
la U. ToothFORM exige LimitX (2,5) libre de cada lado en x ≤ 2 (`pasillo_qr`).
Cuando una pared palatina invade ese pasillo, se le saca a la encía un prisma
y los dientes no se tocan.

**Geometría del prisma (decidido con Leandro):**

- Zona: x ≤ 10 − (QrSize+1)/2 = 2, la misma del control de ToothFORM. Un
  prisma por lado, sólo del lado que invade (hasta ahora siempre uno).
- Piso: **a la altura del QR**, z = PLACA + RELIEVE_QR = 3,2 mm (la cara
  superior del QR). Por debajo la garra ya está en su lugar y la encía queda
  como está. A confirmar si es la cara del QR (3,2) o la de la placa (2,4):
  cambia la profundidad en 0,05 mm.
- Cara: **inclinada 2°** respecto de la vertical, por la orientación del
  termoformado: el hueco de la U se tiene que ensanchar hacia arriba para
  desmoldar. La cara queda en |y| = 8 + LimitX + margen + (z − 3,2)·tan 2°.
  La encía misma va a unos 4°, así que la cuña se afina sola hasta salir de
  la pared y no deja ningún escalón arriba. Abajo queda un escalón de 0,2 a
  0,3 mm mirando hacia arriba, en z = 3,2, que no es contrasalida.
- Margen sobre LimitX: 0,3 mm (configurable), es decir pasillo final ≥ 2,8.
- Cuándo: sólo si el pasillo medido es menor que LimitX; los modelos que
  ToothFORM acepta salen idénticos. Se hace sobre la encía, antes del vaciado
  (la cáscara de 1,8 sigue el contorno nuevo) y `-gum.stl` sale con la muesca.
  El log dice de qué lado, cuánto de profundo y cuánto de alto.

**Tolerancia y límites (error "Clip limit exceeded" con el detalle):**

1. **Toca diente: no va.** Si la cuña intersecta cualquier pieza de dientes
   (volumen de la intersección > 0) o queda a menos de 2 mm del diente más
   cercano, error. En los casos reales la muesca queda a 3,5–6,4 mm.
2. **Tolerancia de profundidad: LimitX − pasillo ≤ 1,0 mm** (propuesto;
   configurable). Los rechazados reales piden 0,05–0,15 mm, que con el margen
   son muescas de 0,15 a 0,45 mm. Más de 1 mm quiere decir que el QR quedó
   mal ubicado respecto de la arcada, y la muesca sería un tajo: error.
3. **Altura: la cuña tiene que salir de la pared a menos de 8 mm sobre el
   piso** (propuesto; configurable). Si la pared no se abre, la cuña sube
   hasta el margen gingival aunque la profundidad sea chica: error. En los
   casos reales sale a 2,8–4,8 mm.

**Medido en los casos reales** (margen 0,3; 2°; piso 3,2; lado −Y en todos):

| Modelo | Pasillo | Prof. al piso | Sale en z | Alto | Sacado | Diente más cercano | ToothFORM |
|---|---|---|---|---|---|---|---|
| BZ254-U00-B | 2,45 | 0,20 mm | 8,0 | 4,8 mm | 2,4 mm³ | 5,1 mm | Clip |
| BZ254-U01-A | 2,44 | 0,15 mm | 6,0 | 2,8 mm | 0,7 mm³ | 6,4 mm | Clip |
| BZ219-U00-B | 2,37 | 0,26 mm | 6,0 | 2,8 mm | 2,1 mm³ | 3,7 mm | aceptó por 0,05 |

Con el piso en z = 0 la cuña es más profunda (0,35–0,43) y más alta (6–8,4
mm), por eso el piso a la altura del QR. La cuña de 2° es 1,5 a 2 veces más
alta que un corte vertical, pero más fina arriba.

Parámetros (params extra del tool, como los de ParameterSettings):
`AbrirPasillo` 0/1 (apagado por defecto hasta validar), `PasilloMargen` 0,3,
`PasilloGrados` 2, `PasilloPiso` 3,2, `PasilloTolerancia` 1,0,
`PasilloAltoMax` 8, `PasilloDiente` 2. En Type3 (sin dientes separados) la
muesca no se abre salvo `PasilloEnType3` 1: lo decide el workflow, y entonces
no se comprueba la distancia a los dientes. En el log, el "pasillo QR" de cada
modelo sigue siendo el de antes de la muesca (el que se compara con
ToothFORM); el aviso dice de qué lado, cuánto de profundo y de alto, cuánto
sacó y cuánto queda desde el piso. La cuña pasa el margen más allá de x = 2
para que la pared que queda en ese plano no siga contando.

## Type3: un solo shell más `.pts` (0.3.0, hipotético)

Con `tipo=Type3` el tool hace lo que hace ToothFORM con ese tipo, leído del
binario (`Toothform.exe` 0x418d50 y 0x41d8c1–0x41de77; ver `enkukador/toothform.py`):

- Entrada: un STL de un solo shell (encía y dientes fundidos) y `<modelo>.pts`
  al lado. Sin el `.pts`: estado "Pts file not found!".
- Al cargar se descartan las componentes de menos de 100 caras. No se
  separan dientes: la pieza y `-gum.stl` salen del shell entero. No hay
  `-tooth.ply`.
- El `.pts` es texto, un punto por línea, campos por espacios (o por
  tabulaciones si no llega a 3); los tres primeros son x y z, el resto se
  ignora. Se le aplica la misma matriz que al modelo y sale como
  `<modelo>-pts.pts`, "x y z" con 6 cifras significativas y CRLF.
- `-mark.txt` (ImportExtPos=1) no está: la instalación lo tiene en 0.

Falta validarlo con un caso real (STL, `.pts` y la salida de ToothFORM).

## Analizar y sanar mallas (0.4.0)

Dos tools para después de `exportar` (o de cualquier paso que deje STL), porque el nesting y
el slicer fallan con una malla que no está totalmente cerrada. Los dos aceptan `archivo` (una),
`archivos` (una lista) o `carpeta` (todas, con `subcarpetas` por defecto); en una carpeta dejan
afuera `-gum.stl`, `-tooth.ply` y `-att.stl` salvo `todas=1`, porque el `-gum.stl` de Type1 es
abierto a propósito.

- **`analizar mallas`** mide cada malla como la lee el proceso siguiente (STL con los vértices
  unidos por posición) y no cambia nada. Totalmente cerrada es: ninguna arista de borde, ninguna
  arista con más de dos caras, ningún vértice non-manifold, ninguna cara de área 0, orientación
  consistente y volumen positivo. Salidas: `mallas` (el detalle de cada una), `abiertas_lista`,
  `todas_cerradas` ("si"/"no").
- **`sanar mallas`** cura las que no lo están, con la cura de model-arranger
  (`salud.curar`, copiada en `salud.py`): vértices casi coincidentes unidos, caras degeneradas y
  duplicadas afuera, fragmentos sueltos afuera, orientación corregida, agujeros tapados, y
  aristas de 4 caras separadas 2 um. keep-form agrega la separación de los vértices
  non-manifold (dos partes que se tocan en un punto), también 2 um (`mallas.py`). Después la
  relee y, si no quedó totalmente cerrada o la superficie cambió más de 1 % (un agujero que no
  es de escaneo, como un casquete de 16 mm), la deja como estaba y el tool da error
  `malla_abierta` con el motivo de cada una. Las que ya estaban cerradas no se tocan.
  `salida` vacía reemplaza el original; si no, deja las curadas ahí con las mismas subcarpetas.

La copia de `salud.py` es la de model-arranger (commit 17bcb9b) con un solo cambio: los
agujeros se triangulan por recorte de orejas propio en vez de mapbox_earcut, que keep-form no
tiene entre sus librerías curadas.

Medido: la pieza de ToothFORM de BZ056-L00-B (34 aristas y 68 vértices non-manifold, 1 cara
degenerada) y BZ254-U06-A rota a propósito (agujeros, duplicadas, una degenerada, un fragmento
suelto, la mitad de las caras al revés) quedan totalmente cerradas con el volumen igual a menos
de 0,001 %. Las piezas de keep-form de BZ056 traían 1 o 2 caras de área 0 de las booleanas: la
cura las quita. Unos 5 s por pieza de 200.000 caras.

Desde 0.4.1, `exportar` mide cada pieza antes de escribirla y, si no está totalmente cerrada, le
pasa la misma cura y lo avisa en el log. En BZ056-L00-B Type3 el vaciado dejaba un triángulo de
10 um de lado (área 0 para un test estricto): ahora sale cerrada. Si la cura no alcanza, la
pieza sale igual con el aviso, y `sanar mallas` después da el error.
