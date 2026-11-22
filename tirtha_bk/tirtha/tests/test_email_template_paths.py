"""
Every attribute an email template names has to exist on the model.

The admin's new-contribution notification read `contribution.mesh.name` for
years after migration 2.1 renamed `Mesh` to `Site`. Django renders a missing
attribute as the empty string, so the mail went out saying

    Mesh:  ()
    Location: , ,

and never once raised. Templates are the one place in this project where a
rename can go unfinished in complete silence: the Python would have thrown
`AttributeError` on the first send.

So this walks the dotted paths out of the email templates and resolves each one
against the real models. It is a cheap guard against a whole class of bug that
has already cost this project years of blank notifications.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

from tirtha.models import Contribution, Contributor

TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "tirtha" / "emails"

# The root name in a template maps to the model it is given at render time.
ROOTS = {
    "contribution": Contribution,
    "contributor": Contributor,
}


def _dotted_paths():
    for template in sorted(TEMPLATES.iterdir()):
        if template.suffix not in {".txt", ".html"}:
            continue
        text = template.read_text()
        for match in re.findall(r"\{\{\s*([a-z_][\w.]*)\s*[|}]", text):
            if "." in match:
                yield template.name, match


def _resolve(model, attrs):
    """Walk `attrs` down from `model`, returning the failing segment or None."""
    current = model
    for name in attrs:
        field = next((f for f in current._meta.get_fields() if f.name == name), None)
        if field is None:
            # Not a field: a property or method on the class is fine too.
            if hasattr(current, name):
                return None
            return name
        related = getattr(field, "related_model", None)
        if related is None:
            return None
        current = related
    return None


class EveryTemplatePathResolves(SimpleTestCase):
    def test_the_email_templates_name_fields_that_exist(self):
        broken = []
        for template, path in _dotted_paths():
            root, *rest = path.split(".")
            model = ROOTS.get(root)
            if model is None:
                # A plain context value like `site_admin_url`, not a model walk.
                continue
            missing = _resolve(model, rest)
            if missing:
                broken.append(f"{template}: {{{{ {path} }}}} — no '{missing}'")
        self.assertEqual(
            broken,
            [],
            "email templates name attributes that do not exist:\n  "
            + "\n  ".join(broken),
        )

    def test_the_check_is_actually_looking_at_something(self):
        """A guard that walks nothing would pass for the wrong reason."""
        paths = list(_dotted_paths())
        self.assertGreater(len(paths), 8, f"only found {len(paths)} paths to check")

    def test_it_would_catch_the_bug_it_was_written_for(self):
        """`contribution.mesh.name` — the exact path that shipped for years."""
        self.assertEqual(_resolve(Contribution, ["mesh", "name"]), "mesh")
        self.assertIsNone(_resolve(Contribution, ["site", "name"]))
