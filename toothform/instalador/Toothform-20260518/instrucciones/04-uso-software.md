# 4. Uso del software: cargar datos y tipos

[← Volver al índice](00-Indice.md)

## Antes de abrir el programa

1. Doble click en `Toothform.ini` y completar `FilePath` con la carpeta donde se guardan los datos.
2. **Si se cambia esta ruta, hay que reiniciar `Toothform.exe`** para que tome efecto (no alcanza con guardar el .ini con el programa abierto).

## Cargar datos

Abrir `Toothform.exe`. La ventana tiene:

- **Open file**: carga un único archivo STL.
- **Open folder**: carga una carpeta con varios STL en lote (o una carpeta 3shape).
- **Export**: agrega la placa base a lo cargado (ver página 5).
- Selector de tipo de dato (**Type1 / Type2 / Type3 / 3shape / onyxceph**).
- Checkbox **QRcode**.
- **Stl folder**: abre la carpeta de datos exportados.
- **Start monitor / Stop monitor**: carpeta vigilada para procesamiento automático (ver página 6).

## Tipos de dato — cuál elegir

| Tipo | Descripción |
|---|---|
| **Type1** | Datos multi-shell (dientes, encías y accesorios como piezas separadas, sin operación booleana), con **cierre gingival** — la encía no tiene agujero, base plana y sólida. |
| **Type2** | Igual que Type1 (multi-shell, sin booleana) pero **sin cierre gingival** — la encía necesita un agujero por diente, base plana y sólida. |
| **Type3** | Un solo shell de datos + PTS. |
| **3shape** | Datos diseñados con el software 3shape (se carga como carpeta, ver nombrado en página 3). |
| **onyxceph** | Datos diseñados con el software onyxceph. Es un único STL que contiene la encía, los dientes y el relleno de cera (wax) como piezas separadas, sin operación booleana; la base del diente es lisa y sólida. |

Si no estás seguro de cuál corresponde, fijate cómo vino armado el archivo de origen (con o sin agujero en la encía, separado o no) antes de exportar — elegir mal el tipo produce errores o resultados incorrectos en el paso de exportación.
