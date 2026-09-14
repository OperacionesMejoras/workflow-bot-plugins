# plugins-registry

Índice curado y público de plugins reutilizables para [`workflow-bot-core`](https://github.com/EasyIndustry/workflow-bot-core), el motor que ejecuta flujos de trabajo escritos en Mermaid.

## Qué es esto (y qué no es)

Este repo **no aloja código de plugins**. Es un índice: cada plugin es un paquete Python instalable (PyPI o `git+https://...`) que vive en su propio repositorio, con su propio ciclo de releases, issues y mantenedor. Acá solo se registra metadata que permite descubrirlo e instalarlo.

Un plugin es un paquete que expone un `PluginManifest` (definido en `backend/core/contract.py` de `workflow-bot-core`) con las `Tool`/`Action` que ofrece, y que declara qué **ports** del core necesita (`http`, `fs`, `process`, `clock`, `browser`, `window`) en lugar de importar esas librerías directamente. El core lo carga en runtime vía entry point.

**Solo entran acá plugins genéricos**: reutilizables por cualquier instalación de `workflow-bot-core` (manejo de archivos, conexiones HTTP genéricas, etc). Plugins atados a un negocio o cliente puntual (por ejemplo, integrados con un sistema externo específico de una instalación) son privados y no se indexan acá.

## Estructura

```
plugins/<name>.json     # una entrada por plugin (ver schema/plugin.schema.json)
schema/plugin.schema.json
docs/curar-un-plugin.md
```

Un archivo por plugin, no un índice monolítico: así cada PR que agrega o actualiza un plugin toca un solo archivo y el diff queda legible.

## Instalar un plugin desde este índice

1. Buscá el archivo correspondiente en `plugins/<name>.json`. Por ejemplo, `plugins/archivos.json`:

   ```json
   {
     "name": "archivos",
     "description": "Tools genéricas de lectura, escritura y manejo de archivos locales...",
     "source": "workflow-bot-plugin-archivos",
     "ports": ["fs"],
     "maintainer": "EasyIndustry",
     "repo_url": "https://github.com/EasyIndustry/workflow-bot-plugin-archivos",
     "compatible_core": ">=0.3.0",
     "license": "MIT"
   }
   ```

2. Instalá el paquete que declara `source`:

   ```bash
   # si source es un nombre de paquete PyPI
   pip install workflow-bot-plugin-archivos

   # si source es una URL git+https
   pip install "git+https://github.com/org/repo.git"
   ```

3. Verificá que tu versión de `workflow-bot-core` cumple el constraint declarado en `compatible_core` antes de habilitarlo en producción.

4. Registrá el plugin según el mecanismo de entry points de `workflow-bot-core` (ver la documentación de ese repo) y confirmá que los `ports` que pide son los que esperás — son la superficie de acceso que le estás dando.

No hay instalación automática ni un comando propio de este repo: es una referencia, no un gestor de paquetes.

## Proponer un plugin nuevo

Ver [`CONTRIBUTING.md`](./CONTRIBUTING.md). En resumen: un PR que agrega `plugins/<name>.json` validado contra `schema/plugin.schema.json`, para un plugin genérico, con tests propios y licencia clara. La curaduría final es manual (ver [`docs/curar-un-plugin.md`](./docs/curar-un-plugin.md)) — nadie mergea sin instalar el paquete propuesto y correr sus tests contra un release real de `bot-core`.

## Licencia

Este índice se distribuye bajo la licencia indicada en [`LICENSE`](./LICENSE). Cada plugin listado tiene su propia licencia, declarada en su entrada (`license`) y en su propio repositorio.
