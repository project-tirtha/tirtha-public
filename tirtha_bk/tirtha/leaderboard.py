"""
Leaderboard and badges (internal design notes).

**Rank on accepted contribution, never raw upload count.** If the board counts
uploaded images, contributors will upload junk -- the incentive is that direct.
An image counts only when it survived moderation, was not rejected by an
operator, and belongs to a site that actually got reconstructed. That ties the
score to delivered value and is spam-resistant by construction.

**The headline metric is sites unlocked, not images.** Someone whose 40 photos
completed a site that had none contributed more than someone who added their
900th angle of Konark.

**Video frames are weighted down.** A ten-minute clip yielding 200 frames must
not outrank 50 deliberate photographs (§16.5). The exact formula is an open
question in §26; `VIDEO_FRAME_WEIGHT` is a setting so it can be tuned without
touching this logic.

Recomputed on a schedule, never per request: it changes only when a
reconstruction completes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from tirtha.models import (
    Badge,
    ContributorBadge,
    Image,
    LeaderboardEntry,
    Site,
)

PERIOD_ALL = LeaderboardEntry.PERIOD_ALL

# Statuses that mean a run produced something publishable.
SUCCESSFUL_RUN_STATUSES = ("Archived", "Manual")


def _video_frame_weight() -> float:
    return float(getattr(settings, "VIDEO_FRAME_WEIGHT", 0.2))


@dataclass
class Tally:
    sites: set = field(default_factory=set)
    images: int = 0
    frames: int = 0

    @property
    def score(self) -> float:
        """
        Weighted contribution volume. **Not** the ranking key.

        Video frames count for less than deliberate photographs (§16.5).
        """
        return self.images + self.frames * _video_frame_weight()

    @property
    def ranking_key(self) -> tuple[int, float]:
        """
        Sites first, volume only as a tie-break.

        A blended score cannot express this. An earlier version scored
        `sites * 100 + images`, under which one site with 200 images outranked
        two sites with 20 each -- the exact inversion the headline metric
        exists to prevent. Lexicographic ordering says what was meant: a
        contributor who unlocked more sites is ahead, whatever the volumes.
        """
        return (len(self.sites), self.score)


def accepted_images(period: str = PERIOD_ALL):
    """
    Images that count.

    `safety_status="safe"` and not operator-rejected. Note this is *not*
    `Run.images`: 18 of 81 production runs have an empty M2M (§8), so joining
    through it would erase real contributions. Membership is derived from the
    site having a successful run instead.
    """
    queryset = Image.objects.filter(
        contribution__site__in=_reconstructed_sites(),
        # Never an upload still arriving; ImageOps has not seen it either.
        contribution__finalized_at__isnull=False,
    )
    queryset = queryset.filter(safety_status="safe").exclude(admin_verdict="reject")
    if period != PERIOD_ALL:
        year, _, month = period.partition("-")
        queryset = queryset.filter(
            created_at__year=int(year), created_at__month=int(month)
        )
    return queryset.select_related("contribution__contributor")


def _reconstructed_sites():
    return Site.objects.filter(
        runs__status__in=SUCCESSFUL_RUN_STATUSES, runs__hidden=False
    ).distinct()


def compute(period: str = PERIOD_ALL) -> list[LeaderboardEntry]:
    """Recompute one board and store it. Returns the entries, ranked."""
    tallies: dict[str, Tally] = {}

    for image in accepted_images(period).iterator(chunk_size=1000):
        contributor = image.contribution.contributor
        tally = tallies.setdefault(contributor.ID, Tally())
        tally.sites.add(image.contribution.site_id)
        if image.source_video_id:
            tally.frames += 1
        else:
            tally.images += 1

    ranked = sorted(
        tallies.items(),
        # Negated for descending; the contributor ID breaks exact ties so the
        # order is stable across recomputes rather than arbitrary.
        key=lambda item: (-item[1].ranking_key[0], -item[1].ranking_key[1], item[0]),
    )

    entries = []
    with transaction.atomic():
        LeaderboardEntry.objects.filter(period=period).delete()
        for position, (contributor_id, tally) in enumerate(ranked, start=1):
            entries.append(
                LeaderboardEntry(
                    contributor_id=contributor_id,
                    period=period,
                    sites_unlocked=len(tally.sites),
                    images_accepted=tally.images,
                    frames_accepted=tally.frames,
                    score=tally.score,
                    rank=position,
                )
            )
        LeaderboardEntry.objects.bulk_create(entries, batch_size=500)
    return entries


def current_period(now=None) -> str:
    """
    This month, in the deployment's time zone.

    Local, not UTC: `accepted_images` filters `created_at__month` in the
    current time zone, so the period name has to be computed in it too.
    Between 00:00 and 05:30 IST on the 1st, UTC is still in the old month.
    """
    now = timezone.localtime(now)
    return f"{now.year:04d}-{now.month:02d}"


def previous_period(now=None) -> str:
    """
    Last month, in the deployment's time zone.

    Recomputed on every refresh alongside the current month
    (review 2026-11-22 §4): the refresh runs at 06:00, so images accepted after
    06:00 on a month's last day were first seen by a refresh that only rebuilt
    the *next* month's board.
    """
    now = timezone.localtime(now)
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return f"{year:04d}-{month:02d}"


# Criteria live here rather than in a database column: they are predicates over
# aggregates, and a column holding a rule expression is a small language nobody
# wants to maintain.
BADGE_RULES = {
    "first-contribution": lambda e: e.images_accepted + e.frames_accepted >= 1,
    "site-unlocker": lambda e: e.sites_unlocked >= 1,
    "five-sites": lambda e: e.sites_unlocked >= 5,
    "hundred-images": lambda e: e.images_accepted >= 100,
    "thousand-images": lambda e: e.images_accepted >= 1000,
}


def award_badges() -> int:
    """
    Award badges from the all-time board.

    **Awards are never revoked.** A badge records that something was true once;
    taking it back because a site was later hidden would punish a contributor
    for an operator's decision.
    """
    awarded = 0
    known = {badge.slug: badge for badge in Badge.objects.all()}
    for entry in LeaderboardEntry.objects.filter(period=PERIOD_ALL).select_related(
        "contributor"
    ):
        for slug, rule in BADGE_RULES.items():
            badge = known.get(slug)
            if badge is None or not rule(entry):
                continue
            _, created = ContributorBadge.objects.get_or_create(
                contributor=entry.contributor, badge=badge
            )
            awarded += int(created)
    return awarded


def public_board(period: str = PERIOD_ALL, limit: int = 50):
    """
    The board as it may be shown.

    Only contributors who opted into a public profile appear (§11.2). Ranks are
    *not* recomputed after filtering: a rank is a fact about the whole field,
    and renumbering would tell a contributor they placed higher than they did.
    """
    return (
        LeaderboardEntry.objects.filter(period=period, contributor__profile_public=True)
        .select_related("contributor")
        .order_by("rank")[:limit]
    )
