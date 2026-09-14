# 2. Configuración `Toothform.ini`

[← Volver al índice](00-Indice.md)

Se abre con doble click (es un archivo de texto, se edita en el Bloc de notas). **Hay que reiniciar `Toothform.exe` para que los cambios tomen efecto.**

```ini
[General]
FilePath=D:\Desktop\1

[ParameterSettings]
ShellThickness=1.8
BottomPlaneThickness=1.0
LimitR=98.0
LimitH=23.0
LimitX=2.5
ToothMinVolume=30.0
Boolean=1
QrSize=15.0
```

## Parámetros

| Parámetro | Qué es | Default | Notas |
|---|---|---|---|
| `FilePath` | Carpeta donde se guardan los datos después de agregar la placa base | — | **No puede contener caracteres en chino.** Cambiarlo requiere reiniciar `Toothform.exe`. |
| `ShellThickness` | Espesor de pared del modelo | 1.8 | Rango recomendado 1.8–2.2. Si se pone muy grande, falla la exportación. |
| `BottomPlaneThickness` | Espesor de la placa/plato de la base | 1.0 | Generalmente no se cambia. |
| `LimitR` | Diámetro límite del modelo | 98.0 | Modelos que superan este valor no se pueden exportar. Depende del equipo. |
| `LimitH` | Altura límite del modelo | 23.0 | Modelos que superan esta altura no se pueden exportar. Depende del equipo. |
| `LimitX` | Límite de la pinza/clamp | 3.0 (esta instalación usa 2.5) | Modelos por debajo de este valor no se pueden exportar. Depende del equipo, el valor bajo no afecta el uso normal. |
| `ToothMinVolume` | Volumen mínimo para considerar que algo es un accesorio (attachment) | 30.0 | |
| `Boolean` | Si los dientes y encías hacen operación booleana entre sí | 1 | |
| `QrSize` | Tamaño del QR impreso en la base | 15.0 | Solo aplica si se exporta con QR code activado (ver página 5). |

## Reglas geométricas relacionadas

- **`LimitR`**: círculo que limita el diámetro máximo aceptado del modelo.
- **`LimitX`**: distancia mínima de la pinza a cada lado del modelo.
- El extremo inferior de la línea gingival del premolar **no debe ser menor a 6 mm**. Por debajo de esa altura, el orificio de posicionamiento agregado sobresale y genera colisión/interferencia con la herramienta durante el corte.
