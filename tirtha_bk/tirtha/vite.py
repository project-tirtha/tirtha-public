"""
Reading Vite's build manifest (internal design notes).

Django serves the React shell, so it has to name the hashed asset files Vite
produced. Rather than adding `django-vite`, this reads `manifest.json` -- the
whole job is a dict lookup and a cache.

The frontend does not exist yet (unit 6.2), and dev runs against the Vite dev
server rather than a build, so *both* of those have to be non-fatal: a missing
manifest returns no assets and the shell still renders its meta tags. That is
the case the unfurl crawlers care about anyway (§12.1).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


def _manifest_path() -> Path:
    configured = getattr(settings, "VITE_MANIFEST_PATH", None)
    if configured:
        return Path(configured)
    # BASE_DIR is `tirtha_bk/`; the frontend is its sibling at the repo root
    # (internal design notes), so this walks up one level.
    return (
        Path(settings.BASE_DIR).parent / "frontend" / "dist" / ".vite" / "manifest.json"
    )


@lru_cache(maxsize=1)
def _load(path_str: str, mtime: float) -> dict:
    # Keyed on mtime so a rebuild invalidates the cache without a restart,
    # while a hot path does not re-read the file on every request.
    try:
        return json.loads(Path(path_str).read_text())
    except (OSError, ValueError):
        return {}


def _normalise_url(url: str) -> str:
    """Collapse repeated slashes in the path and drop any trailing one."""
    scheme, sep, rest = url.partition("://")
    if sep:
        host, _, path = rest.partition("/")
        return f"{scheme}://{host}/" + re.sub(r"/{2,}", "/", path).strip("/")
    return "/" + re.sub(r"/{2,}", "/", url).strip("/")


def manifest() -> dict:
    path = _manifest_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    return _load(str(path), mtime)


# The entry key Vite writes into the manifest. It is the HTML entry for this
# build ("index.html"), not the script, because the app is built from an
# index.html rather than from a bare module.
DEFAULT_ENTRY = "index.html"


# Collected files, remembered; missing ones, never.
#
# This was an `lru_cache`, which cached the answer whichever way it went -- and
# a *negative* answer is the one that changes. The sequence that took the site
# down for 23 minutes: `npm run build` wrote new hashes, a request asked whether
# one was collected and cached `False`, `collectstatic` then copied it, and the
# shell went on serving the "not built" notice until gunicorn was restarted,
# because nothing would ask again.
#
# A file that has been collected cannot become uncollected inside one process's
# life, so caching `True` for ever is safe and keeps the hot path free of
# syscalls. A `False` costs one `is_file()` per request and only while the
# deployment is actually broken, which is exactly when it should be re-checking.
_COLLECTED: set[tuple[str, str]] = set()
_COLLECTED_CAP = 512


def _is_collected(static_root: str, relative: str) -> bool:
    """
    Whether the file the manifest names has actually been collected.

    `collectstatic` copies `frontend/dist` into `STATIC_ROOT`; the manifest is
    read from `dist` directly. Rebuild the frontend without collecting, and the
    two disagree: the shell emits a correct-looking URL for a hash that is not
    on disk, the browser 404s the only script on the page, and the result is a
    blank white screen with a 200 status and nothing in the server log.

    That is exactly what happened when a `npm run build` ran between a deploy
    and its `collectstatic` -- twice, because nothing said anything.
    """
    root = (static_root or "").strip()
    if not root:
        # No STATIC_ROOT configured (the dev server serves from the app dirs),
        # so there is nothing to be out of sync with.
        return True
    key = (root, relative)
    if key in _COLLECTED:
        return True
    if (Path(root) / relative).is_file():
        # Bounded: one entry per asset per build, and a worker that survives
        # many deploys would otherwise grow without limit. Clearing wholesale is
        # fine -- the cost of forgetting is one stat.
        if len(_COLLECTED) >= _COLLECTED_CAP:
            _COLLECTED.clear()
        _COLLECTED.add(key)
        return True
    return False


def _imported_chunks(
    data: dict, record: dict, seen: list[str] | None = None
) -> list[str]:
    """
    The chunks an entry statically imports, transitively, in Vite's own order.

    A chunk's imports are as eagerly needed as the entry's -- the module graph
    is fetched whole before the first line of the app runs -- and the browser
    cannot discover any of them earlier than the chunk that names them.
    """
    seen = [] if seen is None else seen
    for key in record.get("imports", []):
        if key in seen:
            continue
        seen.append(key)
        _imported_chunks(data, data.get(key, {}), seen)
    return seen


def entry_assets(entry: str = DEFAULT_ENTRY) -> dict[str, list[str]]:
    """
    The script, stylesheet and modulepreload URLs for one Vite entry point.

    Returns empty lists when there is no build, so the template renders a shell
    with meta tags and no bundle -- correct for a crawler, and visible as an
    empty page to a human, which is the right way for a missing build to fail.
    """
    data = manifest()
    record = data.get(entry)
    if not record:
        return {"js": [], "css": [], "preload": []}

    # STATIC_URL is built as "/" + PRE_URL + "/static/" and PRE_URL already
    # ends in a slash, so the configured value contains a doubled one. Normalise
    # rather than assume: a doubled slash is merely ugly behind nginx and a 404
    # on a stricter static server.
    static_url = _normalise_url(settings.STATIC_URL)

    # Refuse to emit a script tag for a file that is not there. Returning the
    # no-build shape makes the template render its "the bundle is not built"
    # message, which is a legible failure; a 404 on the only <script> is a white
    # screen that looks like a frontend crash.
    if not _is_collected(str(settings.STATIC_ROOT or ""), record["file"]):
        logger.error(
            "Vite manifest names %s but it is not under STATIC_ROOT (%s). "
            "The frontend was rebuilt without collectstatic, or was built for a "
            "different deployment's asset base. Run build/refresh-demo.sh (or "
            "collectstatic) before serving.",
            record["file"],
            settings.STATIC_ROOT,
        )
        return {"js": [], "css": [], "preload": []}

    js = [f"{static_url}/{record['file']}"]
    css = [f"{static_url}/{href}" for href in record.get("css", [])]

    # Vite lists shared chunks as imports; their CSS has to be included or the
    # page renders unstyled for a beat.
    #
    # Their *JavaScript* has to be named as well, as `<link rel="modulepreload">`
    # -- which Vite's own dist/index.html emits and this template did not. The
    # entry chunk is then the only file the HTML mentions, so the browser cannot
    # even discover maplibre, react and i18n until it has fetched and parsed it:
    # 1,236 kB of the 1,586 kB eager bundle (78%) waits a full extra round trip
    # behind a 350 kB file, and the map cannot begin constructing until it lands.
    preload = []
    for imported in _imported_chunks(data, record):
        chunk = data.get(imported, {})
        css += [f"{static_url}/{href}" for href in chunk.get("css", [])]

        # Same rule as the entry above: never name a file that is not under
        # STATIC_ROOT. A preload for a hash that was never collected spends a
        # request to produce a console error, from the same rebuilt-without-
        # collectstatic mismatch the guard exists for.
        chunk_file = chunk.get("file")
        if chunk_file and _is_collected(str(settings.STATIC_ROOT or ""), chunk_file):
            preload.append(f"{static_url}/{chunk_file}")

    return {"js": js, "css": css, "preload": preload}


def is_built() -> bool:
    return bool(manifest())
