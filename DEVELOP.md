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

Tirtha is two repositories. Clone them **side by side**: `pyproject.toml` installs the reconstruction package from `../tirtha-recon` as an editable path dependency.

```bash
git clone https://github.com/project-tirtha/tirtha-public.git
git clone https://github.com/project-tirtha/tirtha-recon.git
cd tirtha-public
uv sync --frozen          # creates .venv from uv.lock, including ../tirtha-recon
```

`tirtha-recon` ([github.com/project-tirtha/tirtha-recon](https://github.com/project-tirtha/tirtha-recon)) holds the Django-free reconstruction code (COLMAP, the Gaussian Splatting backends, AliceVision wrappers). This repository holds the Django site, the Celery tasks and the React frontend.

Then, you can use the helper script `start-dev.sh` which performs the following (user-local installs; no sudo required):

- Installs Miniconda to `$HOME/miniconda3` (if missing) and creates a conda env with `colmap` for pipeline binaries.
- Installs `nvm`/Node and global `obj2gltf` (if missing) and installs `gltfpack` to `$HOME/.local/bin`.
- Creates `.venv` with `uv sync --frozen` (it stops early if `../tirtha-recon` is missing).
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

If you do not need the AliceVision (photogrammetry) path, comment out the `wget` of `MR2021.1.0.zip` near the top of `start-dev.sh`. The `tirtha_bk/nn_models/vggt` submodule is only needed for the experimental VGGT path, which is off by default.

Celery (task queue) local development:

For quick development using `start-dev.sh` (which sets `TIRTHA_DEV=1`), Celery tasks run eagerly and synchronously inside the Django process - RabbitMQ is not required.

If you need to test real asynchronous behaviour (retries, acks, isolation, long-running jobs), run a broker and a worker.

Database and other services:

- `start-dev.sh` uses SQLite for a fast local setup; Postgres is not required for most site/UI edits.
- For production-like testing or pipeline runs you should run Postgres + RabbitMQ (locally or via containers). The `build/` scripts show the production setup - review them before using on a development machine.

## If you're changing the site (UI-only)

The public site is a React application in `frontend/`, served by Django through a small server-rendered shell (`tirtha_bk/tirtha/templates/tirtha/shell.html`) that reads the Vite manifest.

- Components, pages and hooks: `frontend/src/components/`, `frontend/src/pages/`, `frontend/src/hooks/`
- Styles: `frontend/src/styles/` (CSS custom properties in `theme-variables.css`, Tailwind 4)
- Translations (English, Odia, Hindi): `frontend/src/i18n/locales/` and `frontend/src/locales/`
- Server-rendered pages (documents, error pages, embed, emails): `tirtha_bk/tirtha/templates/tirtha/`
- The Django admin is the operator UI: `tirtha_bk/tirtha/admin/`

Workflow for frontend changes:

```bash
cd frontend
npm ci
npm run dev        # Vite dev server with HMR
npm run lint
npx vitest run
npm run build      # writes dist/ and the manifest Django reads
```

Use Node 24 (see `frontend/package.json`).

## If you're changing backend pipelines or core logic

For changes that affect data processing, reconstruction pipelines, background jobs, APIs, or long-running tasks, inspect these areas:

- The Django app `tirtha_bk/tirtha/`: `models.py`, `views/`, `api/`, `admin/`, `management/commands/`.
- Celery: `tirtha_bk/tirtha/tasks.py` (tasks and the beat schedule) and `tirtha_bk/tirtha/celery.py`. There are two queues, `ingest` and `recon`.
- Pipeline orchestration: `tirtha_bk/tirtha/workers/` (`gsops.py` for COLMAP + Gaussian Splatting, `meshops.py` for AliceVision, `runner.py` for what the tasks call) and `tirtha_bk/tirtha/scheduler.py`.
- Calls to external tools (COLMAP, FastGS/FRGS/splatfacto, splat-transform, AliceVision) live in the sibling `tirtha-recon` package.

Backend changes often require migrations and running Celery workers. Use `python manage.py migrate` and start workers with Celery when developing pipeline logic.

## Linting & formatting

- Python is linted and formatted with [ruff](https://docs.astral.sh/ruff/) only (configuration in `pyproject.toml`). CI pins ruff 0.16.5:

  ```bash
  uvx ruff@0.16.5 check .
  uvx ruff@0.16.5 format --check .
  ```

- `pre-commit run --all-files` runs ruff plus a few hygiene hooks and `gitleaks`. Run `pre-commit install` once so they fire on every commit.
- The frontend uses ESLint: `cd frontend && npm run lint`.

## Running the tests

The backend suite runs against SQLite via `TIRTHA_DEV=1`, so it needs no database, broker or GPU, and no `local_settings.py`:

```bash
cd tirtha_bk
TIRTHA_DEV=1 ../.venv/bin/python manage.py test tirtha
```

Run it serially (no `--parallel`): two modeltranslation tests are order-dependent, and the parallel runner reports that as an unhelpful pickling error.

A few tests check deployment scripts that are not part of the public repository; they are skipped when those files are absent.

Frontend tests:

```bash
cd frontend
npx vitest run
```

`tirtha-recon` has its own pytest suite: `cd ../tirtha-recon && ../tirtha-public/.venv/bin/python -m pytest tests/`.

## Quick repo map

- Django project: `tirtha_bk/` (`manage.py`, settings package `tirtha_bk/tirtha_bk/`)
- The app: `tirtha_bk/tirtha/` (models, views, API, admin, tasks, workers, templates, tests)
- Frontend: `frontend/` (React + Vite)
- Display settings read by both: `config/display.json`
- Build and deployment scripts: `build/`, nginx/systemd templates in `tirtha_bk/config/`
- Reconstruction package: `../tirtha-recon`

## Misc notes

- The repository contains `build/` scripts that perform production-style setup (see `build/build.sh`, `build/tirtha.env.example`, `build/start.sh`). Review them before running on a development machine.
- **Secrets never go in git.** Copy `build/tirtha.env.example` to
  `build/tirtha.env` and fill it in; the real file and `local_settings.py` are
  gitignored, and a `gitleaks` pre-commit hook guards against committing
  secrets. Run `pre-commit install` once so the hook actually fires.
- For GPU-heavy development, ensure drivers and CUDA are correctly installed; refer to `DEPLOY.md`.

