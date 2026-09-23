# Toothform Software - Instrucciones de uso

Basado en: *V2.0-Toothform Software Use Instructions.pdf* (autor original: Chen Wenbing, v2.0, 2025/8/5).

Documentación dividida en páginas cortas para no tener que cargar el PDF completo cada vez — abrí solo la página que necesites.

## Índice

1. [Activación / licencia de la instalación](01-activacion-licencia.md) — `ToothformAct.exe`, `act_info.txt`, `toothform.dat`
2. [Configuración `Toothform.ini`](02-configuracion-ini.md) — todos los parámetros y qué hace cada uno
3. [Requisitos de nombrado de archivos de datos](03-nombrado-datos.md) — STL, 3shape, onyxceph
4. [Uso del software: cargar datos y tipos](04-uso-software.md) — Open file/folder, Type1/2/3/3shape/onyxceph
5. [Exportar (agregar placa base) y QR code](05-exportar-qrcode.md)
6. [Carpeta de datos exportados y monitoreo automático](06-monitoreo-carpeta.md)

## Resumen rápido (por si solo tenés 30 segundos)

- Instalación **portable**: todo vive en una sola carpeta (`Toothform.exe`, `Core.dll`, `LZMA.dll`, `font.ttf`, `Toothform.ini`, `ToothformAct.exe`, `toothform.dat`). No usa instalador ni registro de Windows.
- La licencia (`toothform.dat`) está **atada al disco de cada PC** — no se puede copiar de una PC a otra. Para instalar en una PC nueva hay que repetir la activación (ver página 1).
- Antes de abrir `Toothform.exe` por primera vez, configurar la ruta de guardado en `Toothform.ini` (ver página 2).
- Para actualizar de versión, sólo hay que copiar el `toothform.dat` ya activado a la carpeta de la versión nueva — **no** hace falta re-activar.
