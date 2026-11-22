"""
Deciding when a site is worth reconstructing (internal design notes).

Replaces "a fixed delay after every contribution". That trigger could not
batch: three contributions in an afternoon meant three full reconstructions,
and **GS training has no incremental mode** -- it retrains from scratch for the
full epoch budget whether 3 images were added or 300. COLMAP is incremental
(§10.3), so the SfM half is cheap either way; it is the training half that
batching amortises.

The policy: reconstruct when a site has **≥N new images** or has been **waiting
≥T days**, whichever comes first. That bounds waste and latency together --
without the day trigger a site with 4 new images would wait forever, and
without the count trigger a busy site would rebuild constantly.

`N` and `T` are settings, not architecture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from tirtha.models import Contribution, Site


def _min_new_images() -> int:
    return int(getattr(settings, "RECON_MIN_NEW_IMAGES", 25))


def _max_wait_days() -> float:
    return float(getattr(settings, "RECON_MAX_WAIT_DAYS", 14))


def _max_consecutive_failures() -> int:
    """How many failed attempts in a row start the cool-down. 0 disables it."""
    return int(getattr(settings, "RECON_MAX_CONSECUTIVE_FAILURES", 3) or 0)


def _cooldown_hours() -> float:
    """The first cool-down, in hours; each further failure doubles it."""
    return float(getattr(settings, "RECON_FAILURE_COOLDOWN_HOURS", 24))


def _cooldown_max_hours() -> float:
    """The ceiling on the doubling."""
    return float(getattr(settings, "RECON_FAILURE_COOLDOWN_MAX_HOURS", 168))


# Two Error runs of one scheduled task: `recon_runner_task(..., "all")` runs aV
# and then GS in the same task, so the GS run starts moments after the aV run
# ended. Runs record no task id, so the pair is recognised by that shape.
ATTEMPT_GAP = timedelta(minutes=10)


def _queued_ttl_hours() -> float:
    """
    How long `Site.recon_queued_at` is trusted.

    A marker exists only between the enqueue and the start of the task, and
    the task clears it. One older than this means the message was lost (a
    purged queue, a broker rebuilt), and the site must not be skipped for ever.
    """
    return float(getattr(settings, "RECON_QUEUED_TTL_HOURS", 72))


@dataclass(frozen=True)
class Candidate:
    """A site the scheduler thinks is ready, and why."""

    site: Site
    contribution_id: str
    pending_images: int
    waiting_days: float
    reason: str


def pending_images(site: Site):
    """
    Usable images the site's last reconstruction did not include.

    "Last reconstruction" is the most recent run that did not end in Error. Its
    `Run.images` M2M -- written when the run starts, from the same
    `reconstructable_images` -- is the authoritative record of what it used
    (review 2026-11-22 §2). Comparing upload time against `Run.started_at`
    lost every image *uploaded* before a run started but *checked* after it:
    it was too new for that run and too old for the next, so it never counted
    again. The same was true of an image a moderator released later.

    18 of 81 production runs have an empty M2M (§8). For those -- and only
    those -- the old rule applies, so they do not look permanently
    un-reconstructed and rebuild every night.

    Only images a reconstruction may actually use count -- `safe`, and not
    withheld by a moderator. An unchecked image is not publishable, so it must
    not trigger a reconstruction that would publish it; and an image an admin
    rejected must not trigger one either, which counting `safe` alone did. A
    site whose every image had been rejected still looked like it had work
    waiting, so the scheduler re-enqueued it nightly and the run failed on an
    empty workspace each time.
    """
    from tirtha.workspace import reconstructable_images

    queryset = reconstructable_images(site)
    last_run = site.runs.exclude(status="Error").order_by("-started_at").first()
    if last_run is None:
        return queryset
    if last_run.images.exists():
        return queryset.exclude(runs=last_run)
    return queryset.filter(created_at__gt=last_run.started_at)


def _counted_error_runs(site: Site):
    """
    The site's runs that bear on the back-off, newest first.

    Runs marked Error by the orphan sweeps or the `task_failure` handler are
    left out entirely -- neither counted nor treated as a success. They carry
    `celery.RELEASED_RUN_TAG` in their notes: the worker died, or a deploy
    restarted it, which says nothing about whether the site can reconstruct.
    """
    from tirtha.celery import released_run_q

    runs = site.runs.exclude(released_run_q())
    if site.recon_failures_reset_at:
        runs = runs.filter(started_at__gt=site.recon_failures_reset_at)
    return runs.order_by("-started_at")


def _failed_attempts(site: Site) -> list[datetime]:
    """
    One entry per failed *scheduled task* in the current streak, newest first:
    the instant that attempt's last run ended.

    Counting stops at the first run that did not fail -- a success resets the
    back-off by itself -- and ignores runs started before
    `Site.recon_failures_reset_at`, which is the operator's manual reset.

    **Per task, not per Run.** An "all" task can leave an aV Error and a GS
    Error, and that is one failed reconstruction. Runs record no task id, so an
    Error run is folded into the newer attempt when it is the aV half of an
    aV-then-GS pair: a different kind from every run already in the attempt,
    ending no more than `ATTEMPT_GAP` before the attempt's oldest run started.
    The recon queue is a single slot and a task runs each kind at most once,
    so two runs of the same kind are always two attempts, and a GS run older
    than an aV run is never one task's pair. The only shape this can merge
    wrongly is an aV-only task followed within minutes by a GS-only task, which
    the scheduler never enqueues.
    """
    attempts: list[dict] = []
    for status, kind, started, ended in _counted_error_runs(site).values_list(
        "status", "kind", "started_at", "ended_at"
    )[:100]:
        if status != "Error":
            break
        finished = ended or started
        current = attempts[-1] if attempts else None
        if (
            current is not None
            and kind == "aV"
            and kind not in current["kinds"]
            and current["kinds"] == {"GS"}
            and current["earliest_start"] - finished <= ATTEMPT_GAP
        ):
            current["kinds"].add(kind)
            current["earliest_start"] = started
            continue
        attempts.append({"kinds": {kind}, "earliest_start": started, "ended": finished})
    return [a["ended"] for a in attempts]


def consecutive_failures(site: Site) -> int:
    """How many scheduled reconstructions of this site failed in a row."""
    return len(_failed_attempts(site))


@dataclass(frozen=True)
class FailureState:
    """Where a site stands with the back-off."""

    failures: int
    last_failure_at: datetime | None
    # None when no cool-down applies (too few failures, or disabled).
    cooldown_until: datetime | None

    def cooling_down(self, now: datetime | None = None) -> bool:
        if self.cooldown_until is None:
            return False
        return (now or timezone.now()) < self.cooldown_until


def cooldown_for(failures: int) -> timedelta | None:
    """
    The cool-down after `failures` failed attempts in a row, or None.

    `RECON_FAILURE_COOLDOWN_HOURS` at the limit, doubling for each failure
    beyond it, capped at `RECON_FAILURE_COOLDOWN_MAX_HOURS`. Never permanent:
    when it expires the site is eligible again, and a further failure starts a
    longer one from that failure's end.
    """
    limit = _max_consecutive_failures()
    if limit <= 0 or failures < limit:
        return None
    cap = _cooldown_max_hours()
    # Bounded exponent: the cap is reached long before 2**32.
    hours = min(_cooldown_hours() * (2 ** min(failures - limit, 32)), cap)
    return timedelta(hours=max(hours, 0))


def recon_failure_state(site: Site) -> FailureState:
    """The back-off state, measured from the last counted failure's end."""
    ends = _failed_attempts(site)
    failures = len(ends)
    last = ends[0] if ends else None
    cooldown = cooldown_for(failures)
    until = (last + cooldown) if (cooldown is not None and last) else None
    return FailureState(failures=failures, last_failure_at=last, cooldown_until=until)


def _eligible_sites():
    return Site.objects.exclude(hidden=True).exclude(completed=True)


def backed_off_sites() -> list[tuple[Site, FailureState]]:
    """Sites skipped this pass because they are cooling down after failures."""
    if _max_consecutive_failures() <= 0:
        return []
    now = timezone.now()
    out = []
    for site in _eligible_sites().filter(runs__status="Error").distinct():
        state = recon_failure_state(site)
        if state.cooling_down(now):
            out.append((site, state))
    return out


def sites_awaiting_reconstruction() -> list[Candidate]:
    """
    Candidates, **oldest-waiting-first** so a popular site cannot starve the
    rest.

    Skipped (review 2026-11-22 §2): a site already queued (`recon_queued_at`
    within `RECON_QUEUED_TTL_HOURS`), a site currently Processing, and a site
    whose last `RECON_MAX_CONSECUTIVE_FAILURES` scheduled attempts all
    failed, until its cool-down (`cooldown_for`) has run out. Without the
    back-off a site that always fails kept first place -- it is always the
    oldest waiter -- and took a slot on every pass. New contributions do not
    shorten the cool-down; its expiry always lets the site run again.
    """
    min_new = _min_new_images()
    max_wait = _max_wait_days()
    max_failures = _max_consecutive_failures()
    now = timezone.now()
    candidates: list[Candidate] = []

    sites = (
        _eligible_sites()
        .exclude(status="Processing")
        .exclude(recon_queued_at__gt=now - timedelta(hours=_queued_ttl_hours()))
    )
    for site in sites:
        if max_failures > 0 and recon_failure_state(site).cooling_down(now):
            continue

        queryset = pending_images(site)
        count = queryset.count()
        if count == 0:
            continue

        oldest = queryset.order_by("created_at").first()
        waited = (now - oldest.created_at).total_seconds() / 86400

        if count >= min_new:
            reason = f"{count} new images >= {min_new}"
        elif waited >= max_wait:
            reason = f"waited {waited:.1f}d >= {max_wait}d"
        else:
            continue

        # The reconstruction is attributed to the most recent contribution:
        # the ops classes take their site from it, and it is who gets told the
        # model is live.
        contribution = (
            Contribution.objects.filter(site=site, images__in=queryset)
            .order_by("-contributed_at")
            .first()
        )
        if contribution is None:
            continue

        candidates.append(
            Candidate(
                site=site,
                contribution_id=contribution.ID,
                pending_images=count,
                waiting_days=waited,
                reason=reason,
            )
        )

    return sorted(candidates, key=lambda c: -c.waiting_days)
