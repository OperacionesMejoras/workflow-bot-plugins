# Cómo proponer un plugin

## Requisitos previos

Antes de abrir un PR, confirmá que tu plugin cumple esto:

- **Es genérico.** Reutilizable por cualquier instalación de `workflow-bot-core`, no atado a un negocio, cliente o sistema externo puntual. Si tu plugin solo tiene sentido para una instalación específica, no va acá: quedátelo privado.
- **Implementa el contrato real.** Expone un `PluginManifest` válido según `backend/core/contract.py` de `workflow-bot-core`, con sus `Tool`/`Action`, y declara explícitamente los ports que necesita (`http`, `fs`, `process`, `clock`, `browser`, `window`) — nunca importa esas librerías por afuera del contrato.
- **Tiene tests propios.** El paquete del plugin (en su propio repo) trae su propia suite de tests. Si no los tiene, quien cure el PR va a tener que escribir un test mínimo de humo antes de aprobar (ver `docs/curar-un-plugin.md`) — pero es mucho mejor que ya vengan.
- **Tiene licencia clara.** El repo del plugin declara una licencia (idealmente un identificador SPDX estándar: MIT, Apache-2.0, etc.) en su propio `LICENSE`.
- **Está publicado e instalable.** `source` debe ser un nombre de paquete PyPI publicado, o una URL `git+https://...` que apunte a un commit/tag estable (no a una rama en desarrollo activo).

## Pasos

1. Fork o rama sobre este repo.
2. Agregá **un solo archivo nuevo**: `plugins/<name>.json`, donde `<name>` coincide con el campo `"name"` adentro del JSON. No edites otros archivos de `plugins/` en el mismo PR salvo que sea una actualización de ese mismo plugin.
3. Completá todos los campos requeridos por `schema/plugin.schema.json`:
   - `name`, `description`, `source`, `ports`, `maintainer`, `repo_url`, `compatible_core`, `license`.
4. Abrí el PR. El workflow de CI (`.github/workflows/validate.yml`) valida automáticamente que tu archivo cumple el schema. Si falla, corregí y volvé a pushear.
5. Un maintainer humano hace la curaduría manual descrita en `docs/curar-un-plugin.md`: instala tu paquete contra un release real de `bot-core` y corre (o escribe) tests antes de aprobar.

## Por qué la curaduría es manual y no automática

Instalar un paquete propuesto en un PR significa ejecutar código arbitrario (`pip install` corre `setup.py`/build hooks del paquete, y el propio plugin corre en el proceso del core). Automatizar esa instalación en CI sobre PRs externos sería exponer el pipeline a supply-chain attacks: cualquiera podría abrir un PR con un `source` malicioso y lograr ejecución de código en la infraestructura de CI.

Por eso:

- El CI de este repo (`validate.yml`) **solo valida estructura** — que el JSON cumple el schema. Nunca instala ni ejecuta el paquete propuesto.
- La instalación real, la carga del plugin y la corrida de tests pasa siempre por una persona (o un agente bajo supervisión humana) en un entorno aislado, siguiendo `docs/curar-un-plugin.md`, antes de aprobar el merge.

## Actualizar un plugin ya listado

Mismo flujo: PR que modifica su `plugins/<name>.json` (por ejemplo, para actualizar `compatible_core` tras un nuevo release de `bot-core`, o corregir `repo_url`). Pasa por la misma validación de schema y la misma curaduría manual antes de mergear.
