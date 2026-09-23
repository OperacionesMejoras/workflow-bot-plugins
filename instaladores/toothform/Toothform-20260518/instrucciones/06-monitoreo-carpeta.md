# 6. Carpeta de datos exportados y monitoreo automático

[← Volver al índice](00-Indice.md)

## Qué queda en la carpeta de salida (`Stl folder`)

Por cada caso exportado se generan dos grupos de archivos, en la misma carpeta:

1. **Datos necesarios** (impresión + línea guía de corte), por ejemplo:
   - `222222-L01-A.stl` → dato de impresión
   - `222222-L01-A-att.stl`
   - `222222-L01-A-gum.stl`
   - `222222-L01-A-tooth.ply` → línea guía de corte
2. **Datos TXT**: `222222-L01-A-MatA.txt` — contiene el resultado de la exportación y motivos de fallo si los hubo.

## Monitoreo automático (Start monitor)

> ⚠️ **Según el manual del proveedor, esta función todavía tiene aspectos a optimizar y por ahora no se usa en producción.** Se documenta igual por si se habilita en una versión futura.

1. Click en **Start monitor** y elegir la carpeta a vigilar (ejemplo del manual: carpeta `A`).
2. Se puede seguir agregando STL (Type1–3, o la carpeta en el caso de 3shape) a esa carpeta vigilada; el programa les agrega la placa base automáticamente.
3. Los STL ya procesados quedan igual dentro de la carpeta vigilada — **no se mueven**.
4. Un mismo archivo **no se vuelve a procesar** dos veces.
5. **Stop monitor** detiene la vigilancia.
