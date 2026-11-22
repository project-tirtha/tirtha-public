"""
Client for the self-hosted Overpass mirror (internal design notes).

Three rules shape this module.

**Overpass is a seeding tool, never the runtime source.** The coordinate stored
on `Site` is what renders. A mirror syncs upstream diffs, so a live lookup could
move a marker because someone retagged a way last week; resolving is a human
action that gets persisted, and drift is *flagged*, never applied (internal design notes).

**It is optional infrastructure.** `OVERPASS_URL` unset means the seeding UI
says so and everything else works. Dev must not need a 15 GB mirror to show a
marker (internal design notes).

**What comes back is ODbL.** Anything written to the database from here sets
`location_source="osm"`, because share-alike attaches to OSM-derived rows and
the project publishes datasets (internal design notes).
"""

import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Any

import requests
from django.conf import settings

# Overpass QL is not SQL, but a name with a quote or a backslash in it still
# breaks out of the string literal it is interpolated into. Names come from the
# database, which admins edit, so escape rather than trust.
_QL_ESCAPE = re.compile(r'([\\"])')

# Tokens that carry no distinguishing power when matching our names against OSM.
# "Temple" is in 39 of 60 site names (internal design notes), so requiring it matches
# things that merely happen to be temples, and dropping it is what makes the
# fallback searches useful.
_WEAK_TOKENS = frozenset(
    {
        "temple",
        "mandir",
        "math",
        "matha",
        "the",
        "of",
        "shri",
        "sri",
        "sree",
        "shree",
        "shrine",
    }
)

# Romanised Indic names have no single spelling, and ours and OSM's disagree
# constantly: we store `Brahmeswara Temple` where OSM has `Brahmeswar Temple`,
# and `Bhaskareswar Temple` where OSM has `Bhaskaraswar temple`. Exact token
# matching sees neither, which is why the first run of this matcher reported
# "no candidates" for well-known temples that are in fact mapped.
#
# These rules fold the variants that actually differ in this data. They are not
# a transliteration scheme and are not meant to be one -- they only have to make
# two spellings of the same name compare as similar.
_PHONETIC_RULES = (
    ("ksh", "ks"),
    ("sh", "s"),
    ("ph", "f"),
    ("ck", "k"),
    ("ee", "i"),
    ("oo", "u"),
    ("w", "v"),
    ("y", "i"),
)

# How much of a token has to agree before it is worth searching on. Six
# characters keeps "bhaska" (matching both Bhaskareswar and Bhaskaraswar) while
# staying long enough not to match half the district.
_STEM_LEN = 6

# A candidate is more likely to be the heritage site we mean if OSM already
# thinks it is one of these. Scores are small on purpose: they break ties
# between name matches, they do not outrank them.
_INTEREST_TAGS = ("heritage", "historic", "tourism", "wikidata", "wikipedia")

# The tag union the discovery layer queries on (internal design notes).
#
# `heritage=*` is the tag that ought to be sufficient on its own, and in India
# it is not: it is sparse and applied inconsistently, so a map drawn from it
# alone would show a handful of pins across a state full of mapped temples.
# The union is what makes the layer non-empty -- `historic=*` catches what a
# mapper described rather than certified; `amenity=place_of_worship` **with**
# `building=temple` catches the living temples that are this project's actual
# subject, and the conjunction is load-bearing, because `place_of_worship`
# alone is every mosque, church and roadside shrine in the viewport; and
# `wikidata=*` catches anything notable enough that someone linked it.
#
# The order is meaningful. `discovery_kind` reports the *first* arm an element
# matched, so a pin can say why it is on the map, strongest evidence first.
DISCOVERY_FILTERS = (
    ("heritage", '["heritage"]'),
    ("historic", '["historic"]'),
    ("place_of_worship", '["amenity"="place_of_worship"]["building"="temple"]'),
    ("wikidata", '["wikidata"]'),
)

# A ceiling on what one viewport query may return. Not a page size -- there is
# no second page. A bbox over central Bhubaneswar matches far more than a
# browser should be asked to draw, and the alternative to truncating is a
# response that takes seconds to build and paints a solid blob of pins.
DISCOVERY_MAX_RESULTS = 300

# What the mirror is asked for, before ranking. Larger than what is returned on
# purpose.
#
# `out ... N` truncates the union *arbitrarily* -- Overpass has no notion of
# which arm matters more to us. In a city viewport the `wikidata=*` arm alone
# matches railway stations, hospitals and colleges, so an arbitrary cut at 300
# could drop genuine `heritage` and `historic` sites to make room for a
# hospital. Over-fetching and ranking here (see `DISCOVERY_KIND_RANK`) means the
# cut falls on the weakest evidence rather than on whatever the mirror happened
# to emit last.
DISCOVERY_FETCH_LIMIT = DISCOVERY_MAX_RESULTS * 3

# Strongest evidence first -- the order of `DISCOVERY_FILTERS`, as a lookup.
DISCOVERY_KIND_RANK = {kind: i for i, (kind, _) in enumerate(DISCOVERY_FILTERS)}


def discovery_kind(tags: dict) -> str:
    """
    Which arm of the union put this element on the map.

    A closed vocabulary -- `heritage`, `historic`, `place_of_worship`,
    `wikidata` -- and deliberately not the raw tag value. `historic=*` alone
    has hundreds of values in use upstream; a legend cannot be written against
    an open set, and the frontend has to style four things, not four hundred.
    """
    for kind, _ in DISCOVERY_FILTERS:
        if kind == "place_of_worship":
            if tags.get("amenity") == "place_of_worship":
                return kind
        elif tags.get(kind):
            return kind
    return ""


def discovery_ql(bbox, timeout: int, limit: int = DISCOVERY_MAX_RESULTS) -> str:
    """
    The viewport query, as Overpass QL.

    `bbox` is `(west, south, east, north)` in degrees -- the order a browser
    and MapLibre use. Overpass's own `[bbox:]` setting wants
    `south,west,north,east`, and transposing it wrongly yields a query that
    returns nothing rather than one that errors, so the swap happens exactly
    here and nowhere else.

    The bbox is a *global* setting rather than a per-statement filter, so it
    applies to all four arms of the union without being repeated four times.

    `out center tags` and **not** `meta`: discovery is the unpinned half of the
    Overpass split (internal design notes). Element versions exist so drift can be
    detected against coordinates we hard-pinned, and this layer pins nothing,
    so asking for them would cost mirror time to carry a field nobody reads.
    """
    west, south, east, north = bbox
    union = "\n".join(f"  nwr{expr};" for _, expr in DISCOVERY_FILTERS)
    return (
        f"[out:json][timeout:{timeout}]"
        f"[bbox:{south:.6f},{west:.6f},{north:.6f},{east:.6f}];\n"
        f"(\n{union}\n);\n"
        f"out center tags {int(limit)};"
    )


class OverpassUnavailable(RuntimeError):
    """
    The mirror is not configured, not reachable, or refused the query.

    Callers surface this to the operator instead of failing the request: an
    unreachable mirror must never be able to break the admin (internal design notes).
    """


@dataclass(frozen=True)
class Candidate:
    """One OSM element offered to a human for confirmation."""

    osm_type: str
    osm_id: int
    name: str
    latitude: float
    longitude: float
    version: int | None = None
    tags: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    # Which search found it: "district", "state" or "global". This is locality
    # evidence, and it is not interchangeable with score. A state-wide hit was
    # only reachable because the district -- the strongest locality signal we
    # hold -- was discarded, so it can score 100 on the name and still be three
    # hundred kilometres from the site (internal design notes).
    scope: str = ""

    @property
    def wikidata_qid(self) -> str:
        return self.tags.get("wikidata", "")

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"

    @property
    def summary(self) -> str:
        """The tags an operator actually needs to tell two candidates apart."""
        bits = [
            self.tags.get(k)
            for k in ("historic", "heritage", "religion", "denomination", "tourism")
        ]
        return ", ".join(b for b in bits if b)

    def __str__(self):
        return f"{self.name} ({self.osm_type}/{self.osm_id})"


def _escape(value: str) -> str:
    return _QL_ESCAPE.sub(r"\\\1", value or "")


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"\W+", (name or "").lower()) if t}


def _strong_tokens(name: str) -> set[str]:
    strong = _tokens(name) - _WEAK_TOKENS
    # A site genuinely called just "Temple" would otherwise reduce to nothing.
    return strong or _tokens(name)


def _normalise(name: str) -> str:
    return " ".join(sorted(_tokens(name)))


def _phonetic(text: str) -> str:
    """Fold spelling variants that romanisation leaves undecided."""
    t = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    for a, b in _PHONETIC_RULES:
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)  # doubled letters carry no information here
    t = re.sub(r"a\b", "", t)  # a trailing -a is optional in most of these names
    return " ".join(t.split())


def _phonetic_key(name: str) -> str:
    """Order-independent phonetic form of a name's distinguishing tokens."""
    return " ".join(sorted(_phonetic(" ".join(_strong_tokens(name))).split()))


def _stem(token: str) -> str:
    return token[:_STEM_LEN] if len(token) > _STEM_LEN else token


def _similarity(a: str, b: str) -> float:
    a_key, b_key = _phonetic_key(a), _phonetic_key(b)
    if not a_key or not b_key:
        return 0.0
    return SequenceMatcher(None, a_key, b_key).ratio()


class OverpassClient:
    """
    Thin wrapper over the mirror's `/api/interpreter`.

    Deliberately has no retry loop: this is called from a human-facing admin
    action, and an operator who sees "mirror timed out" and clicks again is a
    better retry policy than one that silently holds the request open for a
    minute and a half.
    """

    def __init__(self, url: str | None = None, timeout: int | None = None):
        self.url = url if url is not None else getattr(settings, "OVERPASS_URL", "")
        self.timeout = timeout or getattr(settings, "OVERPASS_TIMEOUT", 60)

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def query(self, ql: str) -> dict:
        if not self.configured:
            raise OverpassUnavailable(
                "OVERPASS_URL is not set. Overpass is optional -- see "
                "build/setup-overpass.sh to stand the mirror up."
            )
        try:
            response = requests.post(self.url, data={"data": ql}, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout as exc:
            raise OverpassUnavailable(
                f"Overpass timed out after {self.timeout}s. The query may be too "
                "broad, or the mirror may still be importing."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise OverpassUnavailable(f"Overpass request failed: {exc}") from exc
        except ValueError as exc:
            # Overpass reports its own errors as an HTML page with HTTP 200.
            raise OverpassUnavailable(
                "Overpass returned a non-JSON response, which usually means the "
                "query was rejected. Check the mirror's log."
            ) from exc

    # ------------------------------------------------------------------ parsing

    def _to_candidates(self, payload: dict) -> list[Candidate]:
        out = []
        for el in payload.get("elements", []):
            # `out center` puts a representative point on ways and relations;
            # nodes carry lat/lon directly.
            lat = el.get("lat", (el.get("center") or {}).get("lat"))
            lon = el.get("lon", (el.get("center") or {}).get("lon"))
            if lat is None or lon is None:
                # Without a coordinate it cannot seed anything, which is the
                # only thing we want it for.
                continue
            tags = el.get("tags") or {}
            out.append(
                Candidate(
                    osm_type=el.get("type", ""),
                    osm_id=el.get("id"),
                    name=tags.get("name", ""),
                    latitude=float(lat),
                    longitude=float(lon),
                    version=el.get("version"),
                    tags=tags,
                )
            )
        return out

    @staticmethod
    def rank(candidates: list[Candidate], name: str) -> list[Candidate]:
        """
        Order candidates so the operator's first choice is usually first.

        Scored on phonetic similarity rather than token equality, because the
        spellings genuinely differ: `Bhaskareswar` and `Bhaskaraswar` are the
        same temple and share no exact token. OSM's own heritage tagging only
        breaks ties.

        Ranking is a convenience -- the confirmation is still a human decision,
        because disambiguating two `Gopinath Temple` rows is not something a
        score can do (internal design notes).
        """
        target_exact = _normalise(name)
        target_strong = _strong_tokens(name)
        ranked = []
        for c in candidates:
            if _normalise(c.name) == target_exact and target_exact:
                score = 100.0
            else:
                score = 100.0 * _similarity(name, c.name)
                # A name that contains all of ours, plus more, is a strong
                # signal that similarity alone under-rates: "Mukteswar Temple"
                # against "Mukteswar Temple Complex".
                if target_strong and target_strong <= _tokens(c.name):
                    score += 15
            score += sum(2 for k in _INTEREST_TAGS if k in c.tags)
            # Ways and relations are usually the building or the complex;
            # a bare node is more often a duplicate POI marker.
            if c.osm_type in ("way", "relation"):
                score += 1
            ranked.append(Candidate(**{**c.__dict__, "score": round(score, 2)}))
        return sorted(ranked, key=lambda c: (-c.score, c.name))

    # ------------------------------------------------------------- the queries

    def _area_search(self, name: str, area_name: str, limit: int) -> str:
        """Name search scoped to an administrative area (a district or state)."""
        return f"""[out:json][timeout:{self.timeout}];
area["boundary"="administrative"]["name"="{_escape(area_name)}"]->.searchArea;
nwr["name"~"{_escape(name)}",i](area.searchArea);
out center meta {limit};"""

    def _global_search(self, name: str, limit: int) -> str:
        """
        Whole-extract fallback.

        Used when a district area does not exist in OSM under the name we store
        -- district naming is inconsistent, and the extract is one country, so
        a name-indexed search over it stays affordable.
        """
        return f"""[out:json][timeout:{self.timeout}];
nwr["name"~"{_escape(name)}",i];
out center meta {limit};"""

    def find_site(self, site, limit: int = 30) -> list[Candidate]:
        """
        Candidates for a `Site`, best first.

        Widens in three steps -- full name in the district, distinguishing
        tokens in the district, distinguishing tokens in the state -- and stops
        at the first step that finds anything. Widening past a hit would bury
        a good district match under state-wide noise.
        """
        name = (site.name or "").strip()
        if not name:
            return []
        strong = " ".join(sorted(_strong_tokens(name)))
        # Regex alternation over *stems*, so "Jagannath Labanagiri" also matches
        # a way tagged only "Jagannath", and "Brahmeswara" reaches OSM's
        # "Brahmeswar". Stemming is what makes the romanisation differences
        # findable at all -- scoring can only rank what the query returned.
        loose = "|".join(re.escape(_stem(t)) for t in sorted(_strong_tokens(name)))

        attempts = []
        if site.district:
            attempts.append(
                ("district", self._area_search(re.escape(name), site.district, limit))
            )
            if loose and strong.lower() != name.lower():
                attempts.append(
                    ("district", self._area_search(loose, site.district, limit))
                )
        if site.state:
            attempts.append(
                (
                    "state",
                    self._area_search(loose or re.escape(name), site.state, limit),
                )
            )
        if not attempts:
            attempts.append(("global", self._global_search(re.escape(name), limit)))

        for scope, ql in attempts:
            found = self._to_candidates(self.query(ql))
            if found:
                return self.rank([replace(c, scope=scope) for c in found], name)
        return []

    def fetch_element(self, osm_type: str, osm_id: int) -> Candidate | None:
        """
        Re-read one element by ref, for drift detection.

        Returns None when the element is gone -- a deleted way is exactly the
        case the reconciliation job exists to catch, and it must not look like
        an error (internal design notes).
        """
        if osm_type not in ("node", "way", "relation"):
            raise ValueError(f"Not an OSM element type: {osm_type!r}")
        ql = (
            f"[out:json][timeout:{self.timeout}];"
            f"{osm_type}({int(osm_id)});out center meta;"
        )
        found = self._to_candidates(self.query(ql))
        return found[0] if found else None

    def discover(self, bbox, limit: int = DISCOVERY_FETCH_LIMIT) -> list[Candidate]:
        """
        Everything in `bbox` that the tag union calls heritage (internal design notes).

        Not scored, because there is nothing to score against: the seeding flow
        ranks candidates by how well they match one site's name, and this one
        has no target name -- it is asking what is *there*. The caller does
        order the result by which arm of the union matched, which is a
        statement about evidence rather than about any particular site.

        The default limit is `DISCOVERY_FETCH_LIMIT`, not the number returned to
        a browser: Overpass truncates the union arbitrarily, so the caller needs
        more than it will keep in order to choose *what* to drop.

        Unnamed elements are dropped for the same reason `_to_candidates` drops
        elements with no coordinate: the pin exists to be tapped and turned
        into a site request (internal design notes), and "request an unnamed way" is
        not a request anyone can act on. It also removes most of what the
        `wikidata=*` arm sweeps up -- administrative boundaries and the like
        carry the tag without being a place a visitor can photograph.
        """
        found = self._to_candidates(self.query(discovery_ql(bbox, self.timeout, limit)))
        return [c for c in found if c.name]
