"""
Accessibility regressions the audit fixed (internal design notes).

These guard the *structural* findings, which are the ones that silently come
back. Screen-reader testing and the conformance statement are human work and
are recorded as outstanding rather than pretended at.

Contrast is checked here as arithmetic rather than by eye: `opacity-60` on the
paper background measures 4.46:1, just under the 4.5:1 AA threshold for normal
text, and "looks fine" is exactly how that ships.
"""

import json
import re
from pathlib import Path

from django.test import TestCase

FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"

# The front end is JSX now, not TSX. These tests assert accessibility
# properties of the shipped UI, not of a particular file layout, so they look
# for components by extension rather than by name wherever they can -- the
# previous version named files and broke when a component moved, which teaches
# people to stop moving components.
COMPONENT_GLOBS = ("*.jsx", "*.tsx")


def strip_comments(text: str) -> str:
    """
    Source with comments removed.

    Every grep-style test here should use it. Twice now a comment explaining why
    something was removed has failed the test asserting it was removed -- the
    test reading prose as if it were code. Explaining a fix should not break the
    check that the fix happened.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def components(root=None):
    root = root or FRONTEND
    for pattern in COMPONENT_GLOBS:
        yield from root.rglob(pattern)


def source_of(*names):
    """The first component matching any of these basenames, as text."""
    wanted = {n.lower() for n in names}
    for path in components():
        if path.stem.lower() in wanted:
            return path.read_text()
    raise AssertionError(f"no component named any of {sorted(wanted)}")


def app_source():
    for name in ("App", "main", "Approutes"):
        for path in components():
            if path.stem == name:
                return path.read_text()
    raise AssertionError("no application entry component found")


def stylesheet():
    for candidate in ("index.css", "App.css", "main.css"):
        path = FRONTEND / candidate
        if path.is_file():
            return path.read_text()
    sheets = sorted(FRONTEND.rglob("*.css"), key=lambda p: -p.stat().st_size)
    if sheets:
        return "\n".join(s.read_text() for s in sheets)
    raise AssertionError("no stylesheet found")


def _luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def composite(fg: str, bg: str, alpha: float) -> str:
    f = [int(fg.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    b = [int(bg.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(
        round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3)
    )


class ContrastTests(TestCase):
    """
    WCAG 1.4.3 Contrast (Minimum), AA: 4.5:1 for normal text, 3:1 for UI.

    **Against the palette the app actually ships**, parsed out of
    `theme-variables.css`. The previous version hardcoded `#1a1a1a` on `#faf8f5`
    with a `#b45309` accent -- the palette of the *older* front end, deleted when
    this UI landed. So it reported that the shipped colours passed without ever
    having looked at them, and the thing it missed was not marginal: white on
    `--btn-primary-bg: #4caf50` is 2.78:1, and that is the primary action on
    every screen -- Contribute, Send request, Upload.

    A test that names its own colours can only ever describe a design nobody is
    using.
    """

    THEME_CSS = FRONTEND / "styles" / "theme-variables.css"

    @classmethod
    def _blocks(cls):
        """
        The light and dark variable blocks, as `{name: hex}`.

        Split on the dark selector rather than parsed properly: the file is one
        `:root` and one `[data-theme="dark"]`, and a CSS parser here would be
        more machinery than the question needs.
        """
        text = cls.THEME_CSS.read_text()
        marker = text.index('[data-theme="dark"]')

        def variables(chunk):
            return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})", chunk))

        return variables(text[:marker]), variables(text[marker:])

    def test_the_stylesheet_is_where_we_think_it_is(self):
        """Guards every test below from passing on an empty dict."""
        light, dark = self._blocks()
        self.assertIn("text-primary", light)
        self.assertIn("text-primary", dark)

    def test_body_text_passes_in_both_themes(self):
        for name, block in zip(("light", "dark"), self._blocks()):
            with self.subTest(theme=name):
                self.assertGreater(
                    contrast(block["text-primary"], block["bg-primary"]), 4.5
                )

    def test_secondary_text_passes_in_both_themes(self):
        # The muted text: captions, help lines, the place label under a site
        # name. Still normal text, so still 4.5:1.
        for name, block in zip(("light", "dark"), self._blocks()):
            with self.subTest(theme=name):
                self.assertGreater(
                    contrast(block["text-secondary"], block["bg-primary"]), 4.5
                )

    def test_the_primary_button_passes_in_both_themes(self):
        """
        The finding this class existed to have caught: 2.78:1, on Contribute.
        """
        for name, block in zip(("light", "dark"), self._blocks()):
            with self.subTest(theme=name):
                self.assertGreater(
                    contrast(block["btn-primary-text"], block["btn-primary-bg"]), 4.5
                )

    def test_every_button_pairing_passes(self):
        for name, block in zip(("light", "dark"), self._blocks()):
            for kind in ("primary", "secondary", "tertiary"):
                fg = block.get(f"btn-{kind}-text")
                bg = block.get(f"btn-{kind}-bg")
                if not fg or not bg:
                    continue
                with self.subTest(theme=name, button=kind):
                    self.assertGreater(contrast(fg, bg), 4.5)

    def test_hover_does_not_drop_below_the_minimum(self):
        # A hover state is still text on a background, and a button people are
        # about to press is a bad place to lose legibility.
        for name, block in zip(("light", "dark"), self._blocks()):
            for kind in ("primary", "tertiary"):
                fg = block.get(f"btn-{kind}-text")
                hover = block.get(f"btn-{kind}-hover")
                if not fg or not hover:
                    continue
                with self.subTest(theme=name, button=kind):
                    self.assertGreater(contrast(fg, hover), 4.5)

    def test_text_tokens_pass_in_both_themes(self):
        # Including the quietest one. `--text-tertiary` was 2.85:1 on white,
        # used for the upload counts beside contributor names.
        for name, block in zip(("light", "dark"), self._blocks()):
            for token in (
                "text-primary",
                "text-secondary",
                "text-tertiary",
                "error-text",
                # `index.css` colours every link on the site with these two.
                "color-primary",
                "color-secondary",
            ):
                with self.subTest(theme=name, token=token):
                    self.assertGreater(
                        contrast(block[token], block["bg-primary"]), 4.5, token
                    )

    @staticmethod
    def _resolve(chunk, block, token):
        """
        A token's colour, following one hop of `var()`.

        `_blocks` matches `--token: #hex` only, so a token defined as
        `var(--other)` is simply absent from it -- and a contrast check that
        reads a missing key either crashes or, worse, silently falls back to
        some other colour. Half the sidebar's tokens are indirect now, so
        resolving them is the difference between measuring the panel and
        measuring nothing.

        Returns a list because a gradient legitimately has several.
        """
        declaration = re.search(rf"--{token}:\s*([^;]+);", chunk)
        if declaration is None:
            return []
        value = declaration.group(1).strip()

        reference = re.fullmatch(r"var\(\s*--([a-z0-9-]+)\s*\)", value)
        if reference:
            target = block.get(reference.group(1))
            return [target] if target else []
        return re.findall(r"#[0-9a-fA-F]{6}", value)

    def test_the_sidebar_text_passes_against_the_panel_it_sits_on(self):
        """
        The sidebar's own background, not the page's.

        The panel's text is checked against `bg-primary` by the rest of this
        class -- a colour it never actually sits on. This measured the two
        stops of a cream gradient until the panel became a flat surface; the
        gradient is gone, the question is not.

        The links are 12px, so they need 4.5:1 rather than the 3:1 large text
        is allowed.
        """
        source = self.THEME_CSS.read_text()
        split = source.index('[data-theme="dark"]')

        for name, block, chunk in (
            ("light", self._blocks()[0], source[:split]),
            ("dark", self._blocks()[1], source[split:]),
        ):
            backgrounds = self._resolve(chunk, block, "sidebar-bg")
            self.assertTrue(backgrounds, f"{name}: --sidebar-bg resolves to no colour")

            for foreground in ("sidebar-link", "sidebar-text"):
                colours = self._resolve(chunk, block, foreground)
                self.assertTrue(
                    colours, f"{name}: --{foreground} resolves to no colour"
                )

                for colour in colours:
                    for background in backgrounds:
                        with self.subTest(theme=name, fg=foreground, bg=background):
                            self.assertGreater(
                                contrast(colour, background), 4.5, foreground
                            )

    def test_no_stylesheet_hardcodes_a_colour_the_tokens_replaced(self):
        """
        A literal hex outside `theme-variables.css` is a colour nothing checks.

        This class parses the token file, so the two blues it had already ruled
        out survived by being written straight into a component's stylesheet:
        `.beta-badge` was white on `#2196f3` (3.12:1) and `.stat-value` was
        `#1976d2`, which is 4.60:1 on white and 2.99:1 on the dark theme's card.
        Both were invisible to every test here.
        """
        retired = {
            "#4caf50": "--btn-primary-bg (2.78:1 with white)",
            "#2196f3": "--btn-tertiary-bg (3.12:1 with white)",
            "#1976d2": "2.99:1 on the dark theme's card",
        }
        offenders = []
        for sheet in sorted((FRONTEND / "styles").glob("*.css")):
            body = re.sub(r"/\*.*?\*/", "", sheet.read_text(), flags=re.S)
            for colour, why in retired.items():
                if colour in body.lower():
                    offenders.append(f"{sheet.name}: {colour} -- {why}")

        self.assertEqual(
            offenders,
            [],
            "these stylesheets hardcode a colour the tokens replaced:\n  "
            + "\n  ".join(offenders),
        )

    def test_no_stylesheet_carries_an_unwrapped_dark_override(self):
        """
        A dark-theme rule outside a media query or `[data-theme]` block applies
        always.

        `StatisticsCard.css` had four of them: the `@media
        (prefers-color-scheme: dark)` that wrapped them was lost, so they landed
        after the light rules they were meant to replace. `.contributor-name`
        became `#ffffff` on a white card -- every contributor's name invisible,
        in the default theme.

        Detected by brace balance, which is what actually went wrong: a block
        whose opener disappeared leaves the file balanced but its rules
        promoted, so nothing complains.
        """
        for sheet in sorted((FRONTEND / "styles").glob("*.css")):
            body = re.sub(r"/\*.*?\*/", "", sheet.read_text(), flags=re.S)
            # A rule indented at top level is the signature: CSS ignores
            # indentation, so an indented selector at depth 0 is a block that
            # lost its wrapper.
            depth = 0
            for line in body.splitlines():
                stripped = line.strip()
                if depth == 0 and line.startswith("  ") and stripped.endswith("{"):
                    self.fail(
                        f"{sheet.name}: '{stripped}' is indented but at top level "
                        "-- a nested block has lost its wrapper"
                    )
                depth += line.count("{") - line.count("}")

    def test_no_component_dims_text_below_the_minimum(self):
        # 60% ink on the light background is 4.46:1. The class of mistake is
        # dimming secondary text for aesthetics.
        light, _ = self._blocks()
        self.assertLess(
            contrast(
                composite(light["text-primary"], light["bg-primary"], 0.60),
                light["bg-primary"],
            ),
            4.5,
        )
        offenders = [p.name for p in components() if "opacity-60" in p.read_text()]
        self.assertEqual(offenders, [])


class StructureTests(TestCase):
    def test_a_skip_link_exists(self):
        # WCAG 2.4.1 Bypass Blocks: without it a keyboard user tabs the header
        # on every page before reaching content.
        self.assertIn("skip-link", app_source())

    def test_the_skip_link_is_never_display_none(self):
        # display:none removes it from the tab order, defeating the point.
        css = stylesheet()
        block = css[css.index(".skip-link") : css.index(".skip-link:focus")]
        self.assertNotIn("display: none", block)

    def test_focus_is_made_visible_explicitly(self):
        # Tailwind's reset removes the browser default on some elements.
        self.assertIn("focus-visible", stylesheet())

    def test_motion_preferences_are_respected(self):
        self.assertIn("prefers-reduced-motion", stylesheet())

    def test_every_page_has_a_top_level_heading(self):
        """
        WCAG 1.3.1 / 2.4.6: the Explore page had none at all.

        Reads through the page's own local imports rather than only its own
        text. Explore's heading lives in the panel component it renders, and a
        test that only grepped the page file would call that a regression when
        the rendered page is correct.
        """
        for page in components(FRONTEND / "pages"):
            with self.subTest(page=page.name):
                self.assertIn("<h1", _render_closure(page))

    def test_the_map_is_labelled(self):
        self.assertIn("aria-label", source_of("MapView", "SiteMap"))


def _render_closure(page):
    """A component's source plus that of the local components it imports."""
    text = page.read_text()
    sources = [text]
    # Both quote styles: the previous front end was TSX and used double
    # quotes, this one is JSX and uses single. A regex that only matched one
    # silently followed no imports at all and every closure test passed on an
    # empty result.
    for spec in re.findall(r'from [\'"](\.[^\'"]+)[\'"]', text):
        # `.js` and `.ts` as well: shared behaviour that is a hook rather than a
        # component belongs in `hooks/`, with a `.js` extension, and a resolver
        # that only followed component files silently stopped following the
        # focus trap the moment it moved there -- failing three tests for code
        # that had improved.
        for suffix in (".jsx", ".tsx", ".js", ".ts"):
            candidate = (page.parent / f"{spec}{suffix}").resolve()
            if candidate.is_file():
                sources.append(candidate.read_text())
                break
    return "\n".join(sources)


class DialogTests(TestCase):
    """
    `role="dialog" aria-modal="true"` is a promise about keyboard behaviour.

    Four things a modal owes a keyboard user: Escape closes it (WCAG 2.1.2),
    focus moves inside on open, Tab stays inside while it is open (2.4.3), and
    focus returns to whatever opened it on close.

    These assert the promise is kept *somewhere in the dialog's own import
    closure*, not that a particular file contains a particular string. An
    earlier version named `ContributeModal.tsx` and failed the moment the
    handling was extracted to be shared -- the behaviour was intact and better
    placed, and three tests went red. A test that fails when code moves, rather
    than when it stops working, teaches people to stop moving code.
    """

    def _dialogs(self):
        """Every component that declares itself a dialog, with its imports."""
        found = {}
        for path in components(FRONTEND / "components"):
            if ".test." in path.name:
                continue
            text = path.read_text()
            if 'role="dialog"' in text:
                found[path.name] = _render_closure(path)
        return found

    def test_at_least_one_dialog_exists(self):
        """Guards the three below from passing vacuously."""
        self.assertTrue(self._dialogs(), "no component declares role=dialog")

    def test_escape_closes_it(self):
        for name, source in self._dialogs().items():
            with self.subTest(component=name):
                self.assertTrue(
                    '"Escape"' in source or "'Escape'" in source,
                    "no Escape handler in this dialog or its imports",
                )

    def test_focus_is_trapped_while_open(self):
        for name, source in self._dialogs().items():
            with self.subTest(component=name):
                self.assertIn("shiftKey", source)

    def test_focus_returns_to_whatever_opened_it(self):
        for name, source in self._dialogs().items():
            with self.subTest(component=name):
                self.assertIn("opener", source)

    def test_the_behaviour_is_shared_not_copied(self):
        """
        One focus trap, not one per modal.

        Four hand-written copies is how three of them quietly stop working. Each
        dialog may own its markup -- they have different layouts and animations
        -- but the keyboard handling comes from `useModalBehaviour`.
        """
        for path in components(FRONTEND / "components"):
            if ".test." in path.name:
                continue
            text = path.read_text()
            if 'role="dialog"' not in text:
                continue
            with self.subTest(component=path.name):
                self.assertIn(
                    "useModalBehaviour",
                    text,
                    f"{path.name} declares a dialog without the shared behaviour",
                )


class LocalisationTests(TestCase):
    """Accessible names must translate too, or they are English-only labels."""

    def _load(self, code):
        return json.loads(
            (FRONTEND / "i18n" / "locales" / f"{code}.json").read_text("utf-8")
        )

    def test_a11y_strings_exist_in_every_language(self):
        for code in ("en", "or", "hi"):
            with self.subTest(lang=code):
                self.assertIn("a11y", self._load(code))
                self.assertIn("skipToContent", self._load(code)["a11y"])

    def test_no_accessible_name_is_hardcoded_english(self):
        """
        Every accessible name comes from the locale files.

        This passed vacuously for the whole of the port: it globbed `*.tsx`, and
        the front end became `.jsx` when the newer UI landed, so it walked an
        empty file list and asserted nothing about the seven hardcoded
        aria-labels that were actually there. A test that cannot fail is worse
        than no test -- it reports that the property holds.

        An `aria-label` left in English is an English-only label for anyone using
        a screen reader in Odia or Hindi, which is the whole point of §25.
        """
        offenders = []
        for path in components():
            if ".test." in path.name:
                continue
            source = strip_comments(path.read_text())
            # A literal name, in either quote style. `aria-label={t('...')}` and
            # `aria-label={variable}` are what should be there instead.
            for match in re.findall(r"""aria-label=["']([^"'{]{4,})["']""", source):
                offenders.append(f"{path.name}: {match}")

        self.assertEqual(
            offenders,
            [],
            "these accessible names are hardcoded English:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_check_above_is_looking_at_something(self):
        """
        Guards it from passing vacuously again, which is how it failed before.
        """
        found = [p for p in components() if "aria-label" in p.read_text()]
        self.assertTrue(found, "no component contains an aria-label at all")


class MapControlTests(TestCase):
    """
    The map's pins are controls, so they must be controls.

    Markers and clusters were `<div>`s with click handlers: not focusable, no
    accessible name, invisible to a screen reader. A visitor who cannot use a
    mouse could not reach a single site on a map-first site (WCAG 2.1.1, 4.1.2).
    """

    def _map_source(self):
        return strip_comments(source_of("MapView", "SiteMap"))

    def _status_source(self):
        """
        The pin vocabulary, which is no longer local to the map.

        It moved to `utils/siteStatus.js` when the sidebar began listing sites:
        two surfaces showing the same three states must not each invent a
        palette for them, which is exactly how the old legend came to describe
        the pins with a different green, amber and blue from the pins.
        """
        path = FRONTEND / "utils" / "siteStatus.js"
        self.assertTrue(path.is_file(), "utils/siteStatus.js has moved or gone")
        return strip_comments(path.read_text())

    def test_markers_are_buttons(self):
        """
        A marker is a control, so it is a `<button>`.

        The "not a div" half of this used to be a multi-line needle --
        `"createElement('div')\n  el.className = 'custom-marker'"` -- which
        matched only if the two statements stayed adjacent with exactly that
        indentation. Any reformatting made it pass without checking anything.
        Asserting what the code *must* contain is the half that cannot rot;
        asserting the absence of one particular spelling of the bug is the half
        that can.
        """
        source = self._map_source()
        self.assertIn("createElement('button')", source)

        # And no *interactive* element carrying a marker class is built as a
        # div, however the statements happen to be laid out. A div is fine for
        # decoration -- the highlight on the already-selected site conveys
        # nothing the panel beside it does not say in words -- but it has to
        # declare itself as such, which is what `aria-hidden` does. Anything
        # else that answers to a click is a control and must be a button.
        blocks = re.findall(
            r"createElement\(['\"]div['\"]\)((?:(?!createElement).){0,400}?"
            r"className\s*=\s*['\"`][^'\"`]*marker[^'\"`]*['\"`])",
            source,
            flags=re.S,
        )
        interactive = [b for b in blocks if "aria-hidden" not in b]
        self.assertEqual(
            interactive,
            [],
            "a marker that is not marked decorative is still being built as a div",
        )

    def test_markers_carry_an_accessible_name(self):
        self.assertIn("aria-label", self._map_source())

    def test_colour_is_not_the_only_channel_on_the_map(self):
        """
        WCAG 1.4.1: colour must not be the only visual means of conveying
        information.

        The three marker fills are 4.37, 4.38 and 5.84:1 against a light
        basemap -- each legible on its own, and that was what the comment beside
        them measured. Against *each other* they are 1.0, 1.34 and 1.33:1: green
        and amber at identical luminance, which is the same pin to a visitor with
        deuteranopia or protanopia. Telling those three states apart is the
        entire purpose of this map.

        So each pin carries a glyph as well as a hue, and the key that says what
        the glyphs mean is rendered -- its strings existed in all three languages
        and nothing displayed them.
        """
        source = self._status_source()
        self.assertIn("MARKER_GLYPHS", source, "the pins differ only by colour")
        for status in ("published", "in_progress", "awaiting_run", "needs_images"):
            with self.subTest(status=status):
                self.assertRegex(
                    source,
                    rf"MARKER_GLYPHS[\s\S]{{0,600}}{status}",
                    f"no glyph for {status}",
                )

    def test_the_legend_is_rendered(self):
        source = self._map_source()
        self.assertIn("map.legend.title", source)
        self.assertIn("map-legend", source)

    def test_the_marker_fills_are_distinguishable_without_colour(self):
        """
        Belt and braces: if someone later gives two statuses the same glyph,
        this is what catches it.
        """
        source = self._status_source()
        block = re.search(r"const MARKER_GLYPHS = \{(.+?)\n\};", source, re.S)
        self.assertIsNotNone(block, "MARKER_GLYPHS is not a literal any more")

        # Compare each status's whole expression, not the quoted fragments
        # inside it. Matching fragments needed a regex that excluded the quote
        # characters SVG attributes are full of, so it found pieces rather than
        # glyphs and compared the wrong things -- and when the glyphs became
        # template literals it would have found nothing at all and passed on an
        # empty list.
        bodies = {}
        for status in ("published", "in_progress", "awaiting_run", "needs_images"):
            match = re.search(
                rf"{status}:(.+?)(?=\n  [a-z_]+:|\Z)", block.group(1), re.S
            )
            self.assertIsNotNone(match, f"no glyph for {status}")
            bodies[status] = re.sub(r"\s+", "", match.group(1))

        self.assertEqual(
            len(set(bodies.values())),
            len(bodies),
            f"two statuses share the same glyph: {bodies}",
        )

    def test_the_status_vocabulary_matches_the_api(self):
        """
        Marker colour comes from `map_status`, not from guessing.

        It used to be decided by matching a free-text location string against a
        hardcoded list of Indian place names.
        """
        source = self._map_source()
        for value in ("published", "in_progress", "needs_images"):
            with self.subTest(status=value):
                self.assertIn(value, source)

    def test_no_basemap_claims_to_be_something_it_is_not(self):
        """
        Three styles were labelled "Google Roadmap", "Google Satellite" and
        "Google Hybrid" while serving OpenFreeMap vector tiles -- the menu
        offered imagery it did not have and credited the wrong project.
        """
        self.assertNotIn("Google Satellite", self._map_source())
        self.assertNotIn("Google Hybrid", self._map_source())
        self.assertNotIn("Google Roadmap", self._map_source())


class MapDataBinding(TestCase):
    """
    The sites reach the map even when they arrive before it is ready.

    This is a one-line mistake that empties the whole map, silently. The effect
    that hands the fetched sites to MapLibre used to be:

        if (map.isStyleLoaded()) { apply(); } else { map.once('style.load', apply); }

    `style.load` fires **once**. The sites API is same-origin and the basemap is
    a CDN away, so by the time the sites arrive the style has usually loaded
    already -- and `isStyleLoaded()` still returns false whenever tiles are in
    flight. Both branches then miss: the immediate one because the check says
    "not ready", the deferred one because the event it waits for is already
    past. The features are never applied, no error is raised, and the map sits
    empty beside a correctly populated site list.

    `styledata` fires repeatedly, so retrying on it cannot be missed. The same
    trap held the India outline *and* `fitBounds`, so the map did not even frame
    the country.
    """

    def _map_source(self):
        return strip_comments(source_of("MapView", "SiteMap"))

    def test_map_data_is_not_bound_to_a_one_shot_event(self):
        source = self._map_source()
        self.assertNotIn(
            "once('style.load'",
            source,
            "a one-shot style listener silently empties the map when it has "
            "already fired -- retry on `styledata` instead",
        )

    def test_it_retries_on_a_repeating_event(self):
        source = self._map_source()
        self.assertIn("on('styledata'", source)

    def test_source_lookup_does_not_gate_on_isstyleloaded(self):
        """
        `sourceOf` guards against MapLibre throwing before a style exists. It
        must not do that by asking `isStyleLoaded()`, which also goes false
        whenever tiles are loading -- reporting "no source" for a source that is
        present and usable, which is how the data binding above got lost.
        """
        source = self._map_source()
        helper = re.search(r"function sourceOf\(map, id\) \{(.+?)\n\}", source, re.S)
        self.assertIsNotNone(helper, "sourceOf has moved or gone")
        self.assertNotIn("isStyleLoaded", helper.group(1))
        self.assertIn("try", helper.group(1))
