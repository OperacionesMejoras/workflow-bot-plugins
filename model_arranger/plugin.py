"""
Plugin `model-arranger` — DRAFT, todavia sin tools.

Acomoda automaticamente arcadas (STL de escaneos para alineadores) en la cama
de una impresora 3D y exporta la cama como un STL listo para imprimir. El
algoritmo (rasterizado + FFT propio, o sparrow para la mejor ocupacion) vive
en un repo aparte, `model-arranger` (OperacionesMejoras), no aca: ver
`arrange.py` (motor propio) y `sparrow_engine.py` (motor sparrow) de ese repo.
Esta carpeta es el scaffold para portarlo al contrato de plugin; falta
escribir los tools. Lo que sigue es lo que un agente armando el plugin
necesita saber antes de escribir el primero.

## Por que todavia no hay tools

`arrange.py`/`sparrow_engine.py` estan escritos para correr standalone (CLI +
servidor HTTP propio en `server.py`), con acceso directo al filesystem:
`Path(folder).glob("*.stl")`, `trimesh.load_mesh(path, process=False)`,
`Path.stat()` para el cache de huellas. Un plugin de este repo en cambio lee
archivos por el port `fs` (ver `convertidor/plugin.py`, que hace exactamente
este tipo de carga: `trimesh.load(io.BytesIO(fs.read_bytes(ruta)),
file_type="stl", force="mesh")`). Portarlo bien es:

- Reemplazar el `Path.glob`/`trimesh.load_mesh(path)` de `Arranger.__init__`
  y `master_for_file` por `fs.walk(carpeta)` + `fs.read_bytes` +
  `trimesh.load(io.BytesIO(...), file_type="stl", force="mesh")`.
- El cache de huellas maestras (`cache/*.npz`, keyeado por tamano+mtime del
  STL) usa `Path.stat()`; con `fs` hace falta el `stat()` del port
  (`fs.stat(ruta).modified_at`/tamano) en vez del de `os`.
- `plate_stl()` (exportar la cama final a STL) hoy escribe a un `BytesIO` y
  lo devuelve — eso ya encaja bien con `fs.write_bytes` sin cambios grandes.

## El problema que no tiene resolucion obvia todavia: sparrow

La estrategia que mejor ocupacion da (`sparrow_engine.py`, motor sparrow) no
es Python puro: corre un ejecutable Rust compilado aparte
(github.com/JeroenGar/sparrow, pineado a un commit exacto — ver
`tools/build_sparrow.sh` del repo `model-arranger`, la API externa de ese
proyecto ya cambio de formato una vez sin aviso). Dos problemas para el
plugin:

1. **Como se distribuye el binario.** No es un paquete pip (no hay wheel):
   no encaja en la convencion `requirements.txt` + hashes de
   `docs/curar-un-plugin.md` (d-bis), que es para dependencias de computo
   *Python*. El precedente mas cercano en este repo es como `toothform`
   trae el instalador de la app de escritorio aparte, en
   `instaladores/toothform/`, para que no viaje con el plugin — probablemente
   sparrow va por el mismo camino (`instaladores/model-arranger/` o similar),
   pero eso es una decision a tomar, no algo que ya este resuelto.
2. **Que plataforma.** `compatible_runtime` de los plugins con
   `requirements.txt` en este repo (ver `convertidor/requirements.txt`) se
   genera contra `win_amd64` — la maquina de planta real es Windows. El build
   de sparrow que se valido en esta sesion de devops es Linux (entorno de
   trabajo), solo sirve para probar el algoritmo; para el plugin hace falta
   compilarlo para `win_amd64` antes de darlo por listo.
3. `sparrow_engine.run_sparrow` hoy llama al ejecutable con
   `subprocess.Popen` directo; el tool del plugin tiene que llamarlo por el
   port `process` (`process.run([ejecutable, *args], cwd=..., timeout=...)`,
   ver `toothform/plugin.py:_exportar`) en vez de `subprocess` importado
   aparte — así no corre nada fuera del contrato.

## Por que la carpeta es `model_arranger` y el manifest dice `model-arranger`

`archivos/tests/test_buscar.py` y el resto de los tests de este repo importan
con `from plugins.archivos.plugin import build_plugin` — import punteado de
Python, que no acepta un guion. El schema de `plugins/<name>.json` ya lo
resuelve: `name` es kebab-case (`^[a-z0-9]+(-[a-z0-9]+)*$`, para el catalogo)
pero `path` exige un identificador valido de Python
(`^[A-Za-z_][A-Za-z0-9_]*...`, validado contra `schema/plugin.schema.json` en
esta sesion) — por eso `plugins/model-arranger.json` declara
`"path": "model_arranger"` en vez de `"model-arranger"`.

## Ports

`fs` (leer el lote de STL, escribir la cama exportada) y `process` (correr
sparrow). Si al final el motor sparrow queda fuera del plugin (por el punto
anterior) y solo se porta el motor propio (`arrange.py`, sin dependencias
externas), `process` podria no hacer falta — pero por ahora se declara
porque es la estrategia por defecto y la de mejor resultado.

## Que ya esta hecho (devops, repo `model-arranger`, commits recientes)

- Dependencias de computo instaladas y funcionando: numpy, scipy, trimesh,
  shapely, pillow, mapbox-earcut (ver `requirements.txt` de ac'a, portado
  del mismo).
- El bug que rompia la estrategia sparrow contra la version actual de la
  API externa (`min_item_separation` paso de flag de CLI a campo del JSON de
  instancia; cada item ahora exige `orientation`) esta arreglado y probado
  de punta a punta.
- Una optimizacion de computo (cachear la FFT de cada candidato por modelo
  en vez de recalcularla en cada ronda de greedy/compact/insert) ya esta
  aplicada al motor propio.
- `Arranger.arrange(folder, bed, count, margin, edge, rot_step, strategy,
  effort, progress) -> dict` es la funcion de entrada real, ya desacoplada
  del servidor HTTP — el tool principal del plugin es basicamente envolver
  esa llamada una vez resuelto el acceso a archivos por `fs`.

No hay datos reales (STL de arcadas) medidos todavia en esta maquina: faltan
detras de una VPN que el devops todavia no abrio. Los numeros de ocupacion
documentados en el README de `model-arranger` son de corridas anteriores en
la maquina original.
"""

from __future__ import annotations

from backend.core import ports as port_names
from backend.core.contract import Plugin, PluginManifest

MANIFEST = PluginManifest(
    name="model-arranger",
    label="Model Arranger",
    version="0.0.0",
    doc="Acomodar arcadas (STL) en la cama de una impresora 3D y exportar el STL de la cama. DRAFT: sin tools.",
    ports=(port_names.FS, port_names.PROCESS),
)

_TOOLS: tuple = ()  # TODO: portar Arranger.arrange() (ver docstring del modulo)


def build_plugin() -> Plugin:
    return Plugin(manifest=MANIFEST, tools=[])


PLUGIN = build_plugin()

__all__ = ["MANIFEST", "PLUGIN", "build_plugin"]
