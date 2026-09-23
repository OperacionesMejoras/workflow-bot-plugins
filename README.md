# workflow-bot-plugins (Operaciones y Mejoras)

Catálogo **curado y con código** de plugins para el Bot (la webapp sobre
[`workflow-bot-core`](https://github.com/EasyIndustry/workflow-bot-core)).
Es un fork del índice público `EasyIndustry/workflow-bot-plugins` con la
misma estructura —un `plugins/<name>.json` por plugin, validado contra
`schema/plugin.schema.json`— y una diferencia: acá **el código vive en el
repo**, en la carpeta que cada entrada declara en `path`. Las máquinas de
planta no tienen PyPI; un tarball de GitHub, sí.

## Cómo se usa

El Bot se instala **sin plugins**. En la app, Plug ins → *Plugins en línea*
lista las entradas de este repo, se elige la rama, y cada plugin se instala
con un click: la webapp baja la rama, saca la carpeta `path`, la valida en
otro proceso (como cualquier archivo subido) y la copia a `plugins_dir` con
un `.procedencia.json` (repo, rama, commit, versión). Ver
`webapp/plugin_catalog.py` en el repo del Bot.

Si el repo es privado, la instalación necesita un token de GitHub con
lectura, cargado en Config → Variables como `PLUGINS_GITHUB_TOKEN` (secreto).

## Ramas

- **`cured`**: lo curado. Lo que se instala en producción.
- **`draft`**: lo que todavía no terminó. La app lo avisa al elegirla.

Un plugin nace en `draft`, se prueba contra una instalación real y pasa a
`cured` con un merge. La rama que usa cada instalación queda guardada en
la instalación.

## Estructura

```
plugins/<name>.json     # la entrada: qué es, qué ports pide, dónde está (path)
<name>/                 # el código: paquete con __init__.py que exporta PLUGIN
schema/plugin.schema.json
docs/curar-un-plugin.md
```

## Reglas de los plugins

Las del Bot (`CLAUDE.md` del repo del Bot): genéricos, con nombre de
herramienta —nunca de cliente ni de sistema externo—; una llamada HTTP
guardada es una Action de `connections`, no un plugin; los ports que piden
son exactamente los que usan; tests propios (`<name>/tests/`) con los fakes
del núcleo; comentarios y commits en castellano que explican el por qué.

## Plugins

| name | qué hace | ports |
|---|---|---|
| `archivos` | mover, copiar, eliminar, renombrar y buscar (etiqueta o regex) | fs |
| `procesos` | saber si un programa está corriendo | process |
| `toothform` | exportar STL con QR en ToothFORM (cmd o ventana) y leer su log; el release de la app está aparte, en `instaladores/toothform/`, para que no viaje con el plugin | process, fs, clock, window |
| `bots` | hablar con otros Bots de la red: qué hacen, mandarles un caso sin esperar, esperar el resultado; la API que usa un agente remoto (ver `bots/README.md`) | http, clock |
