# Developing Tirtha

This document shows how to set up the repository on Linux and where to look depending on the change you plan to make: site-only (templates/static) or backend/pipeline code. Please read the [DEPLOY.md](./DEPLOY.md) for production/deployment instructions.

**Recommended Specs**
- **OS**: Ubuntu 24.04 LTS (Other Linux distros may work, but are not tested)
- **RAM**: 8+ GB for small-sized image sets (~200 images)
- **VRAM**: 8+ GB; NVIDIA GPU required for CUDA support & Gaussian Splatting
- **CPU**: 6+ cores recommended
- **Storage**: 40+ GB free space recommended

> [!note]
> Please read the sections beyond setup & installation for pointers on where to look for different types of changes.


## Table of contents

- [Setup & installation](#setup--installation)
- [Read this if you're changing the site (User or Admin UI-only)](#if-youre-changing-the-site-ui-only)
- [Read this if you're changing backend pipelines](#if-youre-changing-backend-pipelines-or-core-logic)
- [Linting & formatting](#linting--formatting)
- [Quick repo map](#quick-repo-map)
- [Misc notes](#misc-notes)


## Setup & installation

First, clone the repository and `cd` to the project directory:

```bash
git clone https://github.com/project-tirtha/tirtha-public.git
cd tirtha-public
```

Then, you can use the helper script `start-dev.sh` which performs the following (user-local installs; no sudo required):

- Installs Miniconda to `$HOME/miniconda3` (if missing) and creates a conda env with `colmap` for pipeline binaries.
- Installs `nvm`/Node and global `obj2gltf` (if missing) and installs `gltfpack` to `$HOME/.local/bin`.
- Creates or activates a Python virtualenv `.venv` and installs Python requirements.
- Uses `TIRTHA_DEV=1` to enable the lightweight dev settings (SQLite, eager Celery), runs migrations, `collectstatic`, creates superuser, and starts the Django devserver.

Run the script _from repo root_:

```bash
./start-dev.sh
```

Notes:

- The script installs user-local tooling (`$HOME/miniconda3`, `$HOME/.nvm`, `$HOME/.local/bin`). If you prefer system packages, install Miniconda/Node/obj2gltf/gltfpack manually and re-run the script.
- `start-dev.sh` sets `TIRTHA_DEV=1` so Celery tasks run eagerly (no RabbitMQ required). To test real async behavior, run a RabbitMQ broker and a Celery worker separately (unset `TIRTHA_DEV`).
- If you already have `conda`, `node`/`npm`, or `gltfpack` available system-wide the script will skip installing them.

Start-dev prerequisites and notes

- The `start-dev.sh` script expects a few user tools to be available: `curl`, `git`, `wget`, `unzip`, and `python3.11` on PATH. The script installs Miniconda and Node locally if missing, but these system utilities are required to download and extract archives.
- `start-dev.sh` installs Miniconda to `$HOME/miniconda3` and creates a conda env named `tirtha-conda` with `colmap` from `conda-forge`.
- `gltfpack` is installed to `$HOME/.local/bin/gltfpack` and `obj2gltf` is installed globally via `npm` (managed by `nvm`). Ensure `$HOME/.local/bin` is in your `PATH` if you want to run `gltfpack` directly.
- The script attempts to create a Django superuser non-interactively. If the superuser already exists the `createsuperuser --no-input` step may fail; if that happens either remove the environment variables used for the superuser or create the user manually with:

```bash
python manage.py createsuperuser --username admin --email you@example.com
```

If you prefer to skip the ImageOps submodule and model downloads, edit `start-dev.sh` and comment out the top section that runs `git submodule update` and the `wget` lines.

Celery (task queue) local development:

For quick development using `start-dev.sh` (which sets `TIRTHA_DEV=1`), Celery tasks run eagerly and synchronously inside the Django process - RabbitMQ is not required.

If you need to test real asynchronous behaviour (retries, acks, isolation, long-running jobs), run a broker and a worker.

Database and other services:

- `start-dev.sh` uses SQLite for a fast local setup; Postgres is not required for most site/UI edits.
- For production-like testing or pipeline runs you should run Postgres + RabbitMQ (locally or via containers). The `build/` scripts show the production setup - review them before using on a development machine.

## If you're changing the site (UI-only)

If your change only affects presentation (HTML/CSS/JS served to users), work primarily in these locations:

- Templates: `templates/` folders in the Django apps under `tirtha_bk/` and `tirtha/`.
- Static assets (CSS/JS/images): `tirtha_bk/static`, `tirtha/static`, and production layout `build/tirtha/prod/static`.

Workflow for site-only changes:

1. Edit templates or static files.
2. Use the Django dev server for quick feedback (it serves static files when `DEBUG=True`).
3. For production-like testing, run `python manage.py collectstatic` and test with the production configuration.

Site edits normally do not require database migrations or changes to background workers.

## If you're changing backend pipelines or core logic

For changes that affect data processing, reconstruction pipelines, background jobs, APIs, or long-running tasks, inspect these areas:

- Django apps (`tirtha_bk/`, `tirtha/`): models, views, serializers, management commands, and utility modules.
- Celery tasks and workers: search for `@shared_task`, `task`, or `celery` references and check worker entry points.
- Pipeline and helper scripts: `build/` contains deployment and helper scripts; model-processing utilities and batch jobs may live under `tirtha/` or `tirtha_bk/`.
- External tool integrations: wrappers and command-invocation code for COLMAP, Meshroom, Gaussian Splatting, etc., live near the model-processing utilities.

Backend changes often require migrations and running Celery workers. Use `python manage.py migrate` and start workers with Celery when developing pipeline logic.

## Linting & formatting

- Please follow the [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guide for Python code.
- Before committing, run `tox -e reformat` *twice* or `pre-commit run --all-files` *once* to ensure formatting and hooks run correctly.

## Quick repo map

- Django app code: `tirtha_bk/`, `tirtha/` (templates, models, views, tasks)
- Build and deployment scripts: `build/`
- Static and media files: `tirtha_bk/static`, `tirtha_bk/prod/media`

## Misc notes

- The repository contains `build/` scripts that perform production-style setup (see `build/build.sh`, `build/tirtha.env`, `build/start.sh`). Review them before running on a development machine.
- For GPU-heavy development, ensure drivers and CUDA are correctly installed; refer to `DEPLOY.md`.

