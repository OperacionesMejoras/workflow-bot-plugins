# 5. Exportar (agregar placa base) y QR code

[← Volver al índice](00-Indice.md)

## Pasos

1. Elegir si marcar el checkbox **QRcode**:
   - **Marcado**: la etiqueta grabada en la placa base es un **código QR**.
   - **Sin marcar**: la etiqueta es un **número** — es un modo de respaldo; el algoritmo de identificación digital del equipo todavía está en desarrollo, así que en general conviene dejar QRcode activado.
2. Click en **Export** para empezar a agregar la placa base. Se muestra una barra de progreso ("Exporting").
3. Al terminar aparece un log (`AAAAMMDD.HH.MM.SS.log`) con el resultado, por ejemplo:
   ```
   222222-U01-A   Export successfully
   Total 1 models, of which 1 succeeded and 0 failed
   ```
   Si algún modelo falla, ahí mismo indica el motivo.

Ver página 2 para los parámetros que afectan la exportación (`LimitR`, `LimitH`, `LimitX`, `ShellThickness`, etc.) — si un modelo excede esos límites, el export falla.
