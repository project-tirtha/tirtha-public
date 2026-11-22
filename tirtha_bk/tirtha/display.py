"""
The display settings a maintainer can change without touching code.

`config/display.json` holds them; this reads it, lets a deployment override any
key from `local_settings.DISPLAY_CONFIG`, and hands the result to the shell
template, which publishes it to the React app.

**Why a file rather than settings.** Every value in there is something we
changed our minds about during this upgrade -- whether the hover tile animates,
whether the Beta badge shows, which languages the switcher offers, how close
pins must be to merge. They are decisions, not constants, and a decision that
needs a code change to revisit is one nobody revisits.

**Why not just read the JSON in the template.** A missing file, a trailing
comma, or a key renamed in one place and not the other would then be a
site-wide failure at render time. Everything here fails towards the defaults
below and says so in the log.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "display.json"

# The shipped answer for every key, and the schema: a key absent from here is
# not published, so a typo in the JSON cannot invent a setting the client then
# silently ignores.
DEFAULTS = {
    "animatedPreviews": True,
    "showBetaBadge": None,  # None: decide from the deployment prefix.
    "offeredLanguages": ["en", "or"],
    "mapClusterRadius": 28,
}


def _from_file() -> dict:
    """
    The file's values, or nothing at all.

    The file wraps each setting as `{"_doc": [...], "value": ...}` so the
    documentation lives beside the thing it documents rather than in a separate
    place that goes stale. Keys beginning with `_` are commentary.
    """
    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # A malformed config must not take the site down; it must be loud.
        logger.error("config/display.json could not be read (%s); using defaults", exc)
        return {}

    values = {}
    for key, entry in raw.items():
        if key.startswith("_"):
            continue
        if isinstance(entry, dict) and "value" in entry:
            values[key] = entry["value"]
        else:
            # Tolerated so the file can be written the short way if preferred.
            values[key] = entry
    return values


@lru_cache(maxsize=1)
def display_config() -> dict:
    """
    Defaults, then the file, then the deployment. Later wins.

    Cached: this is read on every shell render, and the file does not change
    under a running process. `refresh-prod.sh` reloads the workers, which is
    what picks up an edit -- the same step that picks up a changed template.
    """
    config = dict(DEFAULTS)

    for key, value in _from_file().items():
        if key not in DEFAULTS:
            logger.warning(
                "config/display.json sets unknown key %r; the client will ignore it",
                key,
            )
            continue
        config[key] = value

    # A deployment's own override, for anything that must differ per site
    # rather than per branch.
    for key, value in (getattr(settings, "DISPLAY_CONFIG", None) or {}).items():
        if key in DEFAULTS:
            config[key] = value

    return config


def display_config_json() -> str:
    """The config as the shell embeds it, safe inside an HTML attribute."""
    return json.dumps(display_config(), separators=(",", ":"))
