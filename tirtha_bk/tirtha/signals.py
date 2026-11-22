import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_migrate, post_save, pre_save
from django.dispatch import receiver

# Local imports
from .models import Contribution, Contributor, Image, Run, Site

# ARCHIVE = Path(settings.ARCHIVE_ROOT)
DEFAULT_SITE_NAME = settings.DEFAULT_SITE_NAME
DEFAULT_SITE_ID = settings.DEFAULT_SITE_ID
ADMIN_NAME = settings.ADMIN_NAME
ADMIN_MAIL = settings.ADMIN_MAIL

# Setup logging
logger = logging.getLogger(__name__)


def _static() -> Path:
    """
    `STATIC_ROOT` as a path, read when the receiver runs.

    These were `STATIC = Path(settings.STATIC_ROOT)` at module level.
    `pre_save_image` already had to stop reading MEDIA_ROOT that way -- bound at
    import, `override_settings` is ineffective and a caller that reconfigures
    storage silently operates on the wrong tree -- and the consequence is far
    worse here: `post_del_site` rmtrees whole directories under both roots, so
    "the wrong tree" is a deployment's real static and media store.
    """
    return Path(settings.STATIC_ROOT)


def _media() -> Path:
    """`MEDIA_ROOT` as a path, read when the receiver runs. See `_static`."""
    return Path(settings.MEDIA_ROOT)


@receiver(post_migrate)
def post_migrate_create_defaults(sender, **kwargs):
    """
    Creates the default site & contributor post migration.

    """
    if (
        sender.name == "tirtha"
        and Site.objects.count() == 0
        and Contributor.objects.count() == 0
    ):
        site_ID = DEFAULT_SITE_ID

        default_desc = "This is the meditation center, atop a small hill, inside the NISER campus at Khordha, Odisha."

        static_paths = [
            _static() / f"models/{site_ID}/avcache",
            _static() / f"models/{site_ID}/published",
        ]
        for static_path in static_paths:
            if not static_path.exists():
                static_path.mkdir(parents=True)

        # Copy default site file to STATIC / models / site_ID / published
        source = _static() / f"{site_ID}"
        fname = f"{site_ID}__default.glb"
        src = source / f"{fname}"

        # Nothing staged to copy from: a deployment that has never had the
        # default artefacts placed, or the scratch STATIC_ROOT a test run gets.
        # The download below writes *into* this directory and the preview and
        # thumbnail beside it are not on the network at all, so there is nothing
        # to be gained by fetching -- and a test suite has no business making
        # the request on every migrate.
        if not source.is_dir():
            logger.info(
                "No default site artefacts under %s; skipping default site creation.",
                source,
            )
            return

        # Check if fname file exists at src
        # If not, download it from https://smlab.niser.ac.in/project/tirtha/static/artifacts/9zpT9kVZwP9XxAbG__default.glb
        if not src.exists():
            try:
                import requests

                url = "https://smlab.niser.ac.in/project/tirtha/static/artifacts/9zpT9kVZwP9XxAbG__default.glb"
                response = requests.get(url)
                response.raise_for_status()  # Raise an error for bad responses
                with open(src, "wb") as f:
                    f.write(response.content)
                logger.info(f"Downloaded default site file to {src}")
            except Exception as e:
                logger.error(f"Failed to download default site file: {e}")
                return  # Exit the function if download fails

        dest = _static() / f"models/{site_ID}/published/{fname}"
        shutil.copy2(src, dest)

        # Copy default site thumbnail and preview images from STATIC to MEDIA
        source /= "to_media"
        srcs = [source / f"{site_ID}_thumb.jpg", source / f"{site_ID}_prev.jpg"]
        dest = _media() / f"models/{site_ID}/"
        dest.mkdir(parents=True, exist_ok=True)
        for src in srcs:
            shutil.copy2(src, dest)

        # Create default site - shown on homepage
        site, _ = Site.objects.get_or_create(
            ID=site_ID, name=DEFAULT_SITE_NAME, hidden=True
        )
        site.description = default_desc
        site.preview = f"models/{site_ID}/{site_ID}_prev.jpg"
        site.thumbnail = f"models/{site_ID}/{site_ID}_thumb.jpg"
        site.minObsAng = 70
        site.save()

        # Create default contributor
        Contributor.objects.create(name=ADMIN_NAME, email=ADMIN_MAIL)


# Connect the signal
post_migrate.connect(post_migrate_create_defaults)


@receiver(post_save, sender=Contributor)
def notify_admin_new_contributor(sender, instance, created, **kwargs):
    """
    Tell the admins a new contributor signed up and needs activating.

    Only for newly created, inactive contributors, and never for the admin
    address itself (assumed admin-created).

    **Enqueued, not sent.** This used to talk SMTP from inside the sign-in
    request (`_authenticate_user` -> `get_or_create`), so a mail host that
    accepted the connection and then stalled held a gunicorn worker for the
    length of the sign-in (review 2026-11-22 §4). The mail is now
    `email_utils.send_new_contributor_email`, run on `ingest` once the row is
    committed.
    """
    if not (created and not instance.active and instance.email != settings.ADMIN_MAIL):
        return
    logger.info(
        f"New contributor created: {instance.email} - queueing admin notification"
    )

    contributor_id = str(instance.ID)

    def _enqueue():
        try:
            from .tasks import notify_admin_new_contributor_task

            notify_admin_new_contributor_task.delay(contributor_id)
        except Exception as exc:  # noqa: BLE001 - a sign-in must not fail on mail
            logger.error(
                f"Could not queue the sign-up notification for {contributor_id}: {exc}"
            )

    transaction.on_commit(_enqueue)


@receiver(post_save, sender=Contribution)
def notify_admin_new_contribution(sender, instance, created, **kwargs):
    """
    Records that a contribution arrived. **Sends nothing.**

    This used to build and send the admin notification from here, and it could
    not have been right: the receiver runs from `Contribution.objects.create`
    in `views.upload`, and the photographs are stored on the *next* line. So
    `images.count()` was 0 every time, and every admin notification this
    project has sent has read "Number of Images: 0". `transaction.on_commit`
    did not help either -- with no atomic block around the view the INSERT
    commits immediately and the callback runs straight away, still before a
    single file is written.

    It also sent SMTP from the request thread, so a mail host that accepted a
    connection and then stopped responding held a gunicorn worker for the
    length of the upload response.

    `views.upload` enqueues `notify_admin_new_contribution_task` once the images
    are on disk; the mail itself is `email_utils.send_new_contribution_email`,
    beside every other message this project sends.
    """
    if not created:
        return
    logger.info(
        f"Contribution {instance.ID} created by {instance.contributor.email}; "
        "the view will notify the admins once its images are stored."
    )


@receiver(post_save, sender=Site)
def post_save_site(sender, instance, **kwargs):
    """
    Creates corresponding directories in filesystem

    """
    site_ID = instance.ID

    # Create these folders in MEDIA, if they don't exist
    # `good` and `bad` are gone with Image.label (internal design notes) -- quality is
    # a measurement now, not a folder. `nsfw` stays: rejected content has to be
    # physically separated from the served media path.
    to_create = ["images", "images/nsfw"]
    for folder in to_create:
        path = _media() / f"models/{site_ID}/{folder}"
        if not path.exists():
            path.mkdir(parents=True)  # Makes both these & site_ID folders

    static_paths = [
        _static() / f"models/{site_ID}/avcache",
        _static() / f"models/{site_ID}/published",
    ]
    for static_path in static_paths:
        if not static_path.exists():
            static_path.mkdir(parents=True)  # Makes both cache & site_ID folders

    # LATE_EXP: if site.status == "Live", then:
    # 1. Create preview image
    # 2. Assign it to site.preview
    # FIXME: Do this per `Run` instead.


@receiver(post_delete, sender=Site)
def post_del_site(sender, instance, **kwargs):
    """
    Deletes corresponding directories from filesystem post Site deletion.

    """
    site_ID = instance.ID
    src = _media() / f"models/{site_ID}"
    dest = _static() / f"models/{site_ID}"

    # Guarded, as `post_del_run` is: a site whose directories were never created
    # -- a root that has moved since the row was saved, a site created before
    # this signal existed -- otherwise raises FileNotFoundError out of the
    # delete, which is a 500 in admin over a directory that is already gone.
    for path in (src, dest):
        if path.exists():
            shutil.rmtree(path)


@receiver(post_delete, sender=Image)
def post_del_image(sender, instance, **kwargs):
    """
    Delete *this image's* contribution once nothing is left in it.

    NOTE: Deletion from MEDIA is handled by django-cleanup.

    This was `Contribution.objects.filter(images__isnull=True).delete()` --
    unscoped. Not to this image's contribution, not to its site, not to its
    contributor: deleting any one image swept every contribution in the database
    that had zero images at that instant. Two populations sit in that state
    legitimately:

      - An upload in flight. `views.upload` creates the `Contribution` *before*
        the image loop, so a concurrent upload is imageless until its first
        image saves.
      - A video-only contribution. Frames are extracted by
        `extract_video_frames_task`, which is minutes of CPU work; until it
        finishes the contribution has videos and no images. Deleting it cascades
        its `SourceVideo` rows and the uploaded footage with them.

    "Empty" means neither images nor videos -- the same rule `views.upload`
    applies to its own result.
    """
    contribution_id = instance.contribution_id
    if contribution_id is None:
        return
    Contribution.objects.filter(
        ID=contribution_id, images__isnull=True, videos__isnull=True
    ).delete()


@receiver(pre_save, sender=Image)
def pre_save_image(sender, instance, **kwargs):
    """
    Quarantine an image when moderation rejects it, and release it when cleared.

    This used to sort images into `good`/`bad`/`nsfw` folders from `Image.label`.
    That field is gone (internal design notes) and so is most of the sorting: quality
    is a measurement now, and "is this image usable" is a query over numbers,
    not a directory an image lives in.

    Safety is the exception, and the reason this signal survives at all.
    Rejected content has to be physically separated -- kept out of the served
    media path, out of exported datasets, and somewhere a moderator can look.
    A database column alone cannot do that.
    """
    if not instance.pk:
        return

    try:
        old_instance = Image.objects.get(pk=instance.pk)
    except Image.DoesNotExist:
        return

    # Who decided. A status change that did not come from `ImageOps` is a
    # person's decision -- an admin action, the change form, a shell -- and is
    # recorded so the classifier never overwrites it (review 2026-11-22 §1 #6).
    # Recorded here rather than in each admin action so no path can forget it.
    if old_instance.safety_status != instance.safety_status and not getattr(
        instance, "_safety_from_classifier", False
    ):
        from django.utils import timezone

        instance.safety_moderated_at = timezone.now()

    was_rejected = old_instance.safety_status == "rejected"
    is_rejected = instance.safety_status == "rejected"
    if was_rejected == is_rejected:
        return

    # Read MEDIA_ROOT here rather than using the module-level constant: bound at
    # import, it makes `override_settings` ineffective, so a test -- or any
    # caller that reconfigures storage -- silently operates on the wrong tree.
    media = Path(settings.MEDIA_ROOT)
    image_root = f"models/{instance.contribution.site.ID}/images/"
    fname = instance.image.name.split("/")[-1]
    src = media / instance.image.name
    relative = f"{image_root}nsfw/{fname}" if is_rejected else f"{image_root}{fname}"
    dest = media / relative

    if src == dest:
        return
    if not src.exists():
        # The row can be re-saved after the file has been archived or cleaned
        # up. Moving nothing is correct; raising would block moderation.
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dest)
    instance.image.name = relative


@receiver(post_save, sender=Run)
def post_save_run(sender, instance, **kwargs):
    """
    Creates the run folder, when a `Run` instance is created.

    """
    run_path = _static() / f"models/{instance.directory}"

    if not run_path.exists():
        run_path.mkdir(parents=True)

    # A run has started, so the site is no longer merely queued
    # (review 2026-11-22 §2). `update`, not `save`: the instance in hand may be
    # stale and this must not write anything else.
    if kwargs.get("created"):
        Site.objects.filter(ID=instance.site_id, recon_queued_at__isnull=False).update(
            recon_queued_at=None
        )


def published_files(site_id, run_id) -> list[Path]:
    """
    The files a run published under `STATIC/models/<site>/published/`.

    Matched by name: `<site>_<run>` followed by one or more suffixes
    (`.sog`, `.splat`, `.splat.gz`, `.compressed.ply`, `.glb`, ...). Symlinks
    and directories are left alone.
    """
    published = _static() / "models" / str(site_id) / "published"
    if not published.is_dir():
        return []
    prefix = f"{site_id}_{run_id}."
    return sorted(
        path
        for path in published.iterdir()
        if path.name.startswith(prefix) and path.is_file() and not path.is_symlink()
    )


@receiver(post_delete, sender=Run)
def post_del_run(sender, instance, **kwargs):
    """
    Deletes the run folder, when a `Run` instance is deleted.

    """
    runID = instance.ID

    if instance.status != "Archived":
        run_dir = _static() / f"models/{instance.directory}"

        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
                print(
                    f"post_del_run | Run ID: {runID} | Deleted run directory {run_dir}"
                )
            except Exception as e:
                print(
                    f"post_del_run | Run ID: {runID} | Error deleting run directory {run_dir}: {e}"
                )

    else:
        siteID = instance.site.ID

        # 1. Deletes the published files for a run.
        #
        # This derived one path from the kind -- `.glb` or `.splat` -- but a
        # GS run publishes `.sog` as the primary artifact (`out_map` in
        # `workers.run_finalize`), a `.splat` sibling, a `.compressed.ply` and
        # `.gz` copies of those, all named `<site>_<run>` with different
        # suffixes. Only the legacy `.splat` was ever removed, so every other
        # file of a deleted run stayed published (review 2026-11-22 §4).
        #
        # Every file in `published/` whose name is exactly `<site>_<run>` plus
        # suffixes. The run ID is a fixed-length ShortUUID, so the `.` after it
        # makes the match exact: no other run's files can share the prefix.
        for pub_fpath in published_files(siteID, runID):
            try:
                pub_fpath.unlink()
                print(
                    f"post_del_run | Run ID: {runID} | Deleted published file {pub_fpath}"
                )
            except Exception as e:
                print(
                    f"post_del_run | Run ID: {runID} | Error deleting published file {pub_fpath}: {e}"
                )

        # 2. Deletes the cached folder for a run
        # ARCHIVE/{siteID}/{kind}cache/{instance.started_at.strftime("%Y-%m-%d-%H-%M-%S")}__{instance.ID}
        cache_dir = Path(instance.directory)

        if cache_dir.exists():
            try:
                shutil.rmtree(cache_dir)
                print(
                    f"post_del_run | Run ID: {runID} | Deleted cache directory {cache_dir}"
                )
            except Exception as e:
                print(
                    f"post_del_run | Run ID: {runID} | Error deleting cache directory {cache_dir}: {e}"
                )

        # 3. The ARK stays.
        #
        # This used to call `instance.ark.delete()`, so one admin click on a Run
        # destroyed a persistent identifier -- against the declaration on the
        # field itself, which reads `on_delete=models.DO_NOTHING  # NOTE: DO NOT
        # DELETE SUCCESSFUL RUNS - THIS VIOLATES THE ARK SPEC`. The FK was
        # written to protect the ARK from exactly this and a signal reached
        # around it.
        #
        # An ARK is a promise that a URL keeps resolving. Deleting the row makes
        # every citation of it a dead link, and the deletion is not recoverable
        # from anything else in the database. Leaving an ARK whose run is gone
        # is the lesser harm and the resolver handles it (`views.resolveARK`
        # falls through to the global resolver for an orphan).
        if instance.ark_id:
            print(
                f"post_del_run | Run ID: {runID} | Kept ARK {instance.ark_id}; "
                "an identifier outlives the run that minted it"
            )


@receiver(post_migrate)
def seed_badges(sender, **kwargs):
    """
    Create the badge rows the award rules refer to (internal design notes).

    Criteria live in `leaderboard.BADGE_RULES`; these rows carry the presentable
    part, so wording and icons can change without revoking anyone's award.
    """
    if getattr(sender, "name", None) != "tirtha":
        return

    from tirtha.models import Badge

    defaults = [
        (
            "first-contribution",
            "First contribution",
            "Your first accepted image.",
            "\N{SEEDLING}",
        ),
        (
            "site-unlocker",
            "Site unlocker",
            "Helped complete a site's first reconstruction.",
            "\N{KEY}",
        ),
        (
            "five-sites",
            "Five sites",
            "Helped reconstruct five sites.",
            "\N{CLASSICAL BUILDING}",
        ),
        (
            "hundred-images",
            "Hundred images",
            "A hundred accepted photographs.",
            "\N{CAMERA}",
        ),
        (
            "thousand-images",
            "Thousand images",
            "A thousand accepted photographs.",
            "\N{TROPHY}",
        ),
    ]
    for slug, name, description, icon in defaults:
        Badge.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "description": description, "icon": icon},
        )
