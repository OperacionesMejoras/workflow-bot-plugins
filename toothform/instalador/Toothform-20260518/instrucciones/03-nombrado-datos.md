# 3. Requisitos de nombrado de archivos de datos

[← Volver al índice](00-Indice.md)

## Datos Type1 / Type2 / Type3 (archivos STL sueltos)

Mismo formato de nombre para los tres tipos:

```
NombreDato-L/U01-numero_de_cilindro_diafragma
```

Ejemplos vistos en la instalación: `222222-L01-A.stl`, `222222-U01-A.stl`

- **`NombreDato`**: máximo 8 caracteres.
- **`L01`**: datos mandibulares (arcada inferior).
- **`U01`**: datos maxilares (arcada superior).
- **`A` / `B`**: número de cilindro 1.
- **`R`**: número de cilindro 2.
- **`T`**: número de cilindro 3.
- **`C`**: número de cilindro 4.

## Datos 3shape (carpetas)

El nombre es de una **carpeta**, no de un archivo:

```
NombreDato-pasos_de_correccion-numero_de_cilindro_diafragma
```

Ejemplos vistos: `111111-51-A`, `111111-52-A`, `111111-53-A`

- **`NombreDato`**: máximo 8 caracteres, igual que arriba.
