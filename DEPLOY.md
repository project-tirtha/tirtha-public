# Deploying Tirtha

This document describes a manual, single-host deployment of Tirtha. Development setup is in [`DEVELOP.md`](./DEVELOP.md).

Placeholders used below:

| Placeholder | Meaning |
|---|---|
| `<HOST>` | the public host name, e.g. `tirtha.example.org` |
| `<USER>` | the Unix user that owns the checkout and runs the services |
| `<REPO_DIR>` | the `tirtha-public` checkout, e.g. `/home/<USER>/tirtha-public` |
| `<TIRTHA_ROOT>` | the deployment root (`TIRTHA_ROOT` in `build/tirtha.env`; default `/var/www/tirtha`) |

## Recommended Specs

- **OS**: Ubuntu 24.04 LTS (other Linux distributions may work, but are not tested)
- **RAM**: 32+ GB for moderately sized image sets (~500 images)
- **VRAM**: 16-24+ GB; an NVIDIA GPU with CUDA is required for Gaussian Splatting
- **CPU**: 16+ cores recommended
- **Storage**: 100+ GB free space recommended

## Software

- Python 3.11 and [uv](https://docs.astral.sh/uv/)
- Node.js >= 24 and npm >= 11 (frontend build, `obj2gltf`, `splat-transform`)
- COLMAP (3.11 or newer; built with CUDA for GPU feature extraction and matching)
- A Gaussian Splatting trainer in its own environment (see [Gaussian Splatting backend](#gaussian-splatting-backend))
- ffmpeg >= 4
- PostgreSQL, RabbitMQ and nginx (installed by `build/build.sh`)

## Repositories

Tirtha is split across two repositories that must be cloned **side by side**:

- [`tirtha-public`](https://github.com/project-tirtha/tirtha-public) (this one): the Django site, Celery tasks, frontend and deployment files.
- [`tirtha-recon`](https://github.com/project-tirtha/tirtha-recon): the Django-free reconstruction package (COLMAP, Gaussian Splatting backends, AliceVision wrappers).

`pyproject.toml` installs `tirtha-recon` as an editable path dependency from `../tirtha-recon`, so the directory layout matters:

```text
<PARENT>/
  tirtha-public/
  tirtha-recon/
```

## Manual deployment

1. Clone both repositories and `cd` into this one:

   ```bash
   git clone https://github.com/project-tirtha/tirtha-public.git
   git clone https://github.com/project-tirtha/tirtha-recon.git
   cd tirtha-public
   ```

2. Create the environment file from the example and fill it in. The real file is gitignored and must never be committed:

   ```bash
   cp build/tirtha.env.example build/tirtha.env
   ```

3. Review `build/build.sh` and adjust it to your system. Sections marked `CHANGEME` can be skipped, e.g. the AliceVision download if you only need Gaussian Splatting.

   `build.sh` copies `tirtha_bk/tirtha_bk/local_settings.manual.py` to `local_settings.py` (gitignored). That file holds only what a deployment sets for itself: secrets, paths, hosts, mail and tool paths. Everything the deployments share comes from `tirtha_bk/tirtha_bk/settings_base.py`, which must stay beside it. To change a shared default for one install, override it in `local_settings.py` rather than editing `settings_base.py`.

4. Default ports:

   - `8000` gunicorn
   - `5432` PostgreSQL
   - `15672` RabbitMQ management
   - `8003` the optional Overpass mirror (localhost only)

   Make sure they are free, or change them in `build/tirtha.env`, `tirtha_bk/gunicorn/gunicorn.conf.manual.py` and the nginx templates.

5. Run the build from the `build` directory:

   ```bash
   cd build
   sudo bash build.sh
   ```

   It installs system packages, runs `uv sync --frozen`, creates the database and RabbitMQ user, renders `tirtha_bk/config/*.nginx.template` into `tirtha.docker.nginx` and `tirtha.ssl.nginx`, installs the plain-HTTP config into nginx, renders the static error pages, runs migrations and creates the superuser.

6. Build the frontend (Django serves the React shell from the Vite manifest):

   ```bash
   cd <REPO_DIR>/frontend
   npm ci
   npm run build
   cd ../tirtha_bk && ../.venv/bin/python manage.py collectstatic --no-input
   ```

7. Start the application:

   ```bash
   cd <REPO_DIR>/build
   bash start.sh
   ```

   `start.sh` starts gunicorn and the two Celery workers in `tmux`. For a long-running deployment, run the same three commands as systemd services instead (see below).

8. Open `http://<HOST>:8000/project/tirtha/`. The Django admin is at `/project/tirtha/admin/`.

9. Logs are in `<TIRTHA_ROOT>/prod/logs/`. RabbitMQ and PostgreSQL logs are in `journalctl`.

## Services (systemd)

Run three services as `<USER>` from `<REPO_DIR>/tirtha_bk`, all using `<REPO_DIR>/.venv`:

- gunicorn, on a Unix socket for nginx: `gunicorn --config gunicorn/gunicorn.conf.py --bind unix:/run/tirtha.sock tirtha_bk.wsgi:application`
- the reconstruction worker, which also runs the beat schedule: `celery -A tirtha worker -l INFO -Q recon -c 1 -P threads --beat -n recon@%h`
- the ingest worker: `celery -A tirtha worker -l INFO -Q ingest -c 8 -P threads -n ingest@%h`

Keep `-P threads`: the AliceVision path uses `multiprocessing.Pool`, which prefork's daemonic workers cannot start. Only one worker may carry `--beat`.

Example unit and socket files are in `tirtha_bk/config/` (`tirthad.docker.service`, `tirthad.docker.socket`). For TLS, use the rendered `tirtha_bk/config/tirtha.ssl.nginx` and point `proxy_pass` at the gunicorn socket.

The public URL prefix (`PRE_URL`, default `project/tirtha/`) should not change once models are published: ARK identifiers store absolute URLs.

## Gaussian Splatting backend

`GS_BACKEND` in `local_settings.py` selects the trainer. Each one runs as a subprocess from its own environment, so its CUDA/PyTorch stack never touches the web application's:

- `"fastgs"` (recommended): [FastGS](https://github.com/fastgs/FastGS). Set `FASTGS_PYTHON` (the Python of an environment with FastGS and its CUDA extensions built) and `FASTGS_REPO` (the FastGS checkout). FastGS itself is MIT-licensed, but its rasteriser derives from the Inria 3D Gaussian Splatting code and is under the Inria/MPII **non-commercial** licence; check that your use complies.
- `"frgs"`: set `FRGS_PATH` to the `frgs` executable of its environment.
- `"splatfacto"`: nerfstudio; set `SPLATFACTO_ENV` to the environment's prefix.

Trained splats are converted for the web viewer with [`splat-transform`](https://github.com/playcanvas/splat-transform) (`SPLAT_TRANSFORM_PATH`, installed with `npm install -g @playcanvas/splat-transform`).

## Overpass mirror (optional)

Tirtha can seed site coordinates from a **self-hosted OpenStreetMap Overpass mirror**.
It is optional: with `OVERPASS_URL` unset, the admin's *Find on OpenStreetMap* action
says so and everything else works unchanged. Nothing in dev needs it.

```bash
bash build/setup-overpass.sh            # start the import (detached; hours)
bash build/setup-overpass.sh --status   # progress
bash build/setup-overpass.sh --verify   # once it is serving
```

Then set, in your `local_settings.py` or environment:

```
OVERPASS_URL=http://127.0.0.1:8003/api/interpreter
```

Notes:

- **Keep it bound to localhost.** The browser must never reach it directly.
- The import needs `OVERPASS_META=yes` (the script sets it). Without element
  versions there is nothing to compare a stored reference against, so
  `manage.py check_osm_drift` cannot detect that upstream has changed.
- Geofabrik publishes India as `.osm.pbf` only; the container's importer wants
  bzip2ed XML, so the script transcodes on the way in.
- Coordinates taken from OSM are **ODbL**. Share-alike attaches to any dataset
  published containing them, which is why every located site records
  `location_source`.

## Compression

Ubuntu's stock `nginx.conf` enables gzip but leaves `gzip_types` commented out, so nginx compresses `text/html` only. Add the following to the `http` block of `/etc/nginx/nginx.conf`, then run `nginx -t && systemctl reload nginx`:

```nginx
gzip on;
gzip_vary on;
gzip_proxied any;
gzip_comp_level 6;
gzip_min_length 256;
gzip_types text/plain text/css text/xml text/javascript application/json
           application/javascript application/xml application/wasm image/svg+xml;
```

## Batched uploads (migration 0032)

The contribute dialog sends a visit's files in batches (≤ 20 files or ≤ 150 MB
each) to `POST /project/tirtha/upload/` with `batched=1`, then calls
`POST /project/tirtha/upload/finish/` (or `.../upload/discard/` on Cancel, and
by `navigator.sendBeacon` if the tab is closed mid-upload).
Requests without `batched=1` are the old one-POST upload, unchanged, so a
cached old frontend keeps working.

- **nginx:** no change is required. The 1 GB body limit sits on the exact
  location `= /project/tirtha/upload/`, which still carries every batch (and
  legacy uploads). `upload/finish/` and `upload/discard/` are small bodies
  that fall through to the general `/project/tirtha/` location and its default
  limit. Once no cached legacy client remains, the 1 GB limit could come down
  to ~300 MB -- except that a lone video is exempt from the batch cap below
  and still travels whole.
- **Settings** (all optional; defaults shown):
  - `UPLOAD_RATE_LIMIT` 20 / `UPLOAD_RATE_WINDOW` 600 s count *contributions
    created*; `UPLOAD_BATCH_RATE_LIMIT` 60 / `UPLOAD_BATCH_RATE_WINDOW` 600 s
    count batch, finish and discard requests (a 1000-file upload is 50 batches
    and a finish). Both are per contributor, over the shared `DatabaseCache`.
  - `UPLOAD_MAX_FILES_PER_CONTRIBUTION` 1000 and
    `UPLOAD_MAX_BYTES_PER_CONTRIBUTION` 3 GiB (3221225472), enforced under the
    contribution's row lock: a batch past either is refused whole with 413
    (`too_many_files` / `too_large`).
  - `UPLOAD_MAX_BYTES_PER_BATCH` 300 MiB (314572800), from `CONTENT_LENGTH`
    and the file sizes, 413 `too_large`. A batch holding one video and nothing
    else is exempt, as before; its ceiling stays nginx's body limit (and the
    client's own per-video limit).
  - `UPLOAD_MAX_OPEN_PER_CONTRIBUTOR` 3: a fourth open upload (one not
    finished and not idle past the TTL) is refused with 429 `too_many_open`;
    a first batch that resumes an existing upload by its file keys is not a
    new one.
  - `UPLOAD_OPEN_TTL_MINUTES` 120 is how long an open upload may sit idle.
- **Beat:** `finalize_abandoned_uploads_task` runs every 15 minutes on `ingest`
  (registered by `setup_periodic_tasks`, so restarting the beat-carrying `recon`
  worker picks it up). For an idle open upload it **deletes** one whose
  contributor is banned or inactive, or whose client never acknowledged a
  batch (`confirmed_batches = 0`: the first batch's answer may never have
  arrived, e.g. Cancel or a closed tab during it); **finalizes** any other that
  holds files; deletes an empty one.
- **Order:** deploy the new backend code first, *then* run `migrate`. 0032
  only adds columns, and each carries a database default
  (`finalized_at DEFAULT now()`, the two counters `DEFAULT 0`), so old code
  still running while it applies -- a worker not yet restarted -- inserts
  finalized rows that nothing mistakes for open uploads. Deploy the new
  frontend last; until 0032 is applied it must not be served, since batched
  requests need the new columns.
- **Rollback:** the reverse order. Deploy the previous frontend first (it never
  sends `batched=1`). Close any open uploads -- run the sweep with
  `UPLOAD_OPEN_TTL_MINUTES=0`, or inspect
  `Contribution.objects.filter(finalized_at=None)` -- then **reverse 0032
  (`migrate tirtha 0031`) while the new backend code is still in place**, and
  only then restore the previous backend. Old code against the 0032 schema is
  safe (the database defaults above), but the new code against the 0031 schema
  is not, so the migration must go back before the code does.

## Admin access (Google sign-in)

The Django admin (`/project/tirtha/admin/`) is reached by **Sign in with
Google** on its login page. The button goes through the contributor sign-in
(`signin` -> Google -> `verifyToken`) with `next=` the admin page, so it uses
the existing OAuth client and redirect URI; nothing changes in the Google Cloud
console. After the usual ID-token checks (issuer, audience, nonce,
`email_verified`), `tirtha/admin_auth.py:link_admin_user` logs the matching
Django `User` in. Sign-ins and refusals are logged with the email address.

- **Settings** (in `local_settings.py`; see `local_settings.manual.py`):
  - `ADMIN_GOOGLE_EMAILS` -- list of addresses allowed into the admin through
    Google, compared case-insensitively. Default empty (nobody). An address
    gets in only if **exactly one active `User` with `is_staff=True`** has it;
    Users are never created, and a non-staff or inactive User is refused.
  - `ADMIN_PASSWORD_LOGIN` -- default `True` (dev, tests and beta keep the
    username/password form). `False` removes the form from the login page and
    answers a password POST to `admin/login/` with 403. There is no password
    reset route and no DRF/browsable-API login. `admin/logout/` is unaffected.
- **Recommended production values:**

  ```python
  ADMIN_GOOGLE_EMAILS = ["admin@example.org"]  # your superusers' User.email values
  ADMIN_PASSWORD_LOGIN = False
  ```

  Staff accounts not on the list keep their rows but can no longer reach the
  admin. Signing in to the public site with a non-allowlisted Google account
  still works and ends any admin session held under another address.
- **Emergency access** (Google down, or the allowlist misconfigured). This
  **requires shell access to the server** as the user that runs the site:

  ```bash
  cd <REPO_DIR>/tirtha_bk
  ../.venv/bin/python manage.py admin_login_link <username>
  # optional: --base-url https://<HOST>
  ```

  It prints a URL under `/project/tirtha/admin/login/link/...`, valid for
  **10 minutes** and **once**, for an **active staff** user only. Opening it
  logs that user in and lands on the admin, whatever `ADMIN_PASSWORD_LOGIN` is.
  The token is a salted `TimestampSigner` signature; single use is enforced by
  a nonce in the shared cache (`DatabaseCache`, `tirtha_cache`) and also by
  binding the user's `last_login`, so any later login voids it. Treat the URL
  like a password and do not paste it into chat or mail.
- **Rollback:** set `ADMIN_PASSWORD_LOGIN = True` (or delete both lines) and
  restart the gunicorn service; the password form is back.

## Troubleshooting

- If ports are in use, change them in `build/tirtha.env` and the matching config files.
- If services fail to start, check `journalctl -u <service>` and the logs under `<TIRTHA_ROOT>/prod/logs/`.
- `uv sync --frozen` fails with "Distribution not found" for `tirtha-recon`: clone it beside this repository as `../tirtha-recon`.
- Open an issue or a discussion in this repository if you get stuck.
