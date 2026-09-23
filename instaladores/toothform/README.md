# ToothFORM, el release que este plugin espera

`Toothform-20260518/` es la copia portable del release del 18/05/2026 tal
como lo entrega el proveedor (`\\SERVER-NUEVO\Instaladores\TOOTHFORM`), con
sus instrucciones en castellano en `instrucciones/`. Está en el repo —sí,
binarios— para llevar el Bot y la app juntos a una PC nueva, pero fuera de
`toothform/`, a propósito: instalar el plugin copia su carpeta entera, y así
no arrastra 7 MB de `.exe`/`.dll` que el plugin no usa (y que el antivirus
revisa uno por uno en cada instalación). A la PC la app se lleva a mano.

**Lo que no está, a propósito:**

- `toothform.dat` — la licencia. Está atada al número de serie del disco de
  cada PC (`instrucciones/01-activacion-licencia.md`): copiarla de otra
  máquina no sirve. En una PC nueva: correr `ToothformAct.exe`, mandar el
  `act_info.txt` al proveedor y poner el `.dat` que devuelve al lado del
  `.exe`. Para pasar de versión en la **misma** PC alcanza con copiar el
  `.dat` ya activado.
- `act_info.txt` — es de la máquina donde se generó.

## Qué cambió respecto del release anterior (20260123) y qué asume el plugin

Verificado el 14/09/2026 en Windows 10, corriendo el `.exe` a mano:

- **Modo línea de comandos**: `Toothform.exe <ruta a un .json>` carga
  `FilesToProcess.OpenFolder` con `FilesToProcess.Type`, exporta y termina,
  sin abrir la ventana (27 s con un STL). Abierto sin argumento sigue siendo
  la UI de siempre, con los mismos controles (`Open file`, `Open folder`,
  `Export`, `QRcode`, `Type1..3`). El JSON de ejemplo es el `Toothform.json`
  del release; `toothform.exportar` escribe el suyo.
- La UI lee **sólo** el `.ini`; el modo cmd lee **sólo** el `.json`
  (`CHANGELOG-20260518-original-zh.txt`). Admite varias instancias por cmd,
  incluso con la UI abierta.
- En el `.ini`, `FilePath` pasó a ser dos claves: `ExportPrintPath` (ahí caen
  el log `<fecha>.<hora>.log`, `debug.txt` y `<nombre del dato>\<dato>.stl`, el
  imprimible) y `ExportFilePath` (`<nombre del dato>\` con `-gum.stl`,
  `-tooth.ply`, `-MatA.txt`). Con las dos iguales queda todo junto en
  `<salida>\<nombre del dato>\`, igual que antes. `<nombre del dato>` es el
  prefijo del STL antes del primer `-`.
- El **exit code es siempre 2**, también con un JSON inexistente o una carpeta
  que no existe. El resultado se lee del log, que no cambió de formato:
  `<dato>    Export successfully` y `Total N models, of which X succeeded and
  Y failed`.
- Una ruta con caracteres fuera de ASCII en el JSON (una `ó`) hace que no
  exporte nada ni deje log, escrita en UTF-8 o en ANSI. Rutas ASCII, o una
  junction (`mklink /J C:\Users\<u>\BotQA_casos "<fs_root>\casos"`).
- El `Toothform.ini` del proveedor apunta a una carpeta del servidor (UNC);
  para el Bot va adentro del `fs_root` de la instalación.
