# 1. Activación / licencia de la instalación

[← Volver al índice](00-Indice.md)

La instalación es portable (una sola carpeta, sin instalador). La licencia queda atada al **disco de la PC** donde se activa, así que este proceso se repite en cada máquina nueva.

## Pasos

1. Doble click en **`ToothformAct.exe`**.
2. Aparece un cuadro de diálogo — click en **OK** ("Information has been saved to act_info.txt").
3. Enviar el archivo **`act_info.txt`** generado al técnico/proveedor para que lo decodifique.
4. El técnico devuelve un archivo **`toothform.dat`** — colocarlo en la misma carpeta que `Toothform.exe`.

Listo, la instalación queda activada.

## Actualizaciones de versión

Para pasar a una versión nueva del software **no hay que repetir la activación**: simplemente copiar el `toothform.dat` ya activado de la instalación vieja a la carpeta de la instalación nueva.

## Notas técnicas (verificadas en esta PC)

- `ToothformAct.exe` es internamente `TgetInfo.exe` (nombre visible en su PDB, proyecto "Ortholink"). Solo genera el código de máquina a partir del número de serie del volumen del disco (`GetVolumeInformationA`) — no tiene lógica de red ni de "ingresar clave"/"revocar" incorporada.
- El intercambio código-de-máquina → `toothform.dat` lo resuelve el proveedor manualmente (por WhatsApp en nuestro caso), no es un proceso automático online.
- Como la licencia depende del disco, **copiar el `toothform.dat` de una PC a otra no funciona** — hay que generar un `act_info.txt` nuevo en cada máquina destino y pedir su propio `.dat`.
