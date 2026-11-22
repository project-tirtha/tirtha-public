"""
The durable per-site COLMAP working directory (internal design notes).

**Two directories, not one.** The media store is canonical and never renamed;
the COLMAP working directory is a symlink farm over it, refreshed per run.
Conflating them was an earlier draft's mistake, and an expensive one to make:
COLMAP stores *relative* image paths in `database.db`, so once an image is
registered its path can never change. Renaming ~19,300 archived files to give
them ordered names would also run through `django_cleanup`, which deletes the
old file whenever a `FileField` value changes -- a rename migration could have
destroyed source images that cannot be recovered.

Symlinking sidesteps that entire class of problem. `seq` is a database column,
the media store is never touched, and the link names are stable across runs
because `seq` is stable per image.

**The workspace persists between runs.** That is what continual reconstruction
depends on (10.3): `database.db` and `sparse/` accumulate, and each run only
*adds* links. Nothing is ever removed, because the database still references
every image it has registered.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from tirtha_recon import ImageRef, materialise

from tirtha.models import Image, Site


def workspace_root() -> Path:
    """
    Where per-site COLMAP workspaces live.

    Deliberately outside both MEDIA_ROOT and the run directories: it belongs to
    the *site*, outlives any single run, and must never be swept up by run
    archival (`run_cleanup` moves run folders to NFS).
    """
    configured = getattr(settings, "COLMAP_WORKSPACE_DIR", None)
    return Path(configured) if configured else Path(settings.PROD_DIR) / "colmap_ws"


@dataclass(frozen=True)
class Workspace:
    """Paths COLMAP and FRGS are handed."""

    root: Path
    images: Path
    masks: Path
    database: Path
    sparse: Path

    @classmethod
    def for_site(cls, site_id: str) -> Workspace:
        root = workspace_root() / str(site_id)
        return cls(
            root=root,
            images=root / "images",
            masks=root / "masks",
            database=root / "colmap" / "database.db",
            sparse=root / "colmap" / "sparse",
        )

    def ensure(self) -> Workspace:
        for path in (self.images, self.masks, self.database.parent, self.sparse):
            path.mkdir(parents=True, exist_ok=True)
        return self


def reconstructable_images(site: Site):
    """
    The images a reconstruction may use, in `seq` order.

    Excludes anything moderation withheld and anything not yet checked -- an
    unchecked image is not publishable, so it must not silently end up in a
    published model (internal design notes).

    Rows with no `seq` sort last rather than crashing: `seq` is backfilled by
    migration 0020, but an image ingested by older code could still arrive
    without one, and refusing the whole run over that would be worse than
    appending it.
    """
    return (
        Image.objects.filter(
            contribution__site=site,
            safety_status="safe",
            # Never part of an upload still arriving (batched uploads).
            contribution__finalized_at__isnull=False,
        )
        .exclude(image="")
        # The moderator's verdict counts here too. It was honoured everywhere
        # else -- the leaderboard, the admin's "Good Image Count" column, the
        # dataset export -- and ignored by the one consumer that puts pixels
        # into a published model. So an admin marked an image bad, watched the
        # count drop everywhere they could see, and the next reconstruction
        # used it anyway.
        .exclude(admin_verdict="reject")
        .order_by("seq", "created_at", "ID")
    )


def image_refs(site: Site) -> list[ImageRef]:
    """Database rows → the reference objects the materialiser consumes."""
    media = Path(settings.MEDIA_ROOT)
    refs = []
    for position, img in enumerate(reconstructable_images(site)):
        refs.append(
            ImageRef(
                # Fall back to position for a row that never got a seq, so the
                # link name is still ordered and still unique.
                seq=img.seq if img.seq is not None else position,
                image_id=str(img.ID),
                source=media / img.image.name,
            )
        )
    return refs


def materialise_masks(site: Site, workspace: Workspace) -> int:
    """
    Link masks into the workspace under the names COLMAP expects.

    COLMAP looks for `<image filename>.png` inside `--ImageReader.mask_path`,
    so the mask for `00000042_<uuid>.jpg` must be `00000042_<uuid>.jpg.png`.
    That naming is not negotiable and is the reason masks are materialised
    rather than read from the media store directly (internal design notes).

    Returns how many masks are present. Like images, links are added and never
    removed.
    """
    media = Path(settings.MEDIA_ROOT)
    workspace.masks.mkdir(parents=True, exist_ok=True)
    linked = 0

    for position, img in enumerate(reconstructable_images(site)):
        if not img.mask:
            continue
        seq = img.seq if img.seq is not None else position
        suffix = Path(img.image.name).suffix
        link = workspace.masks / f"{seq:08d}_{img.ID}{suffix}.png"
        source = media / img.mask.name
        if link.exists() or link.is_symlink():
            linked += 1
            continue
        if not source.exists():
            continue
        link.symlink_to(source)
        linked += 1

    return linked


def withdrawn_links(site: Site, workspace) -> set[str]:
    """
    Links in the workspace that `reconstructable_images` no longer allows.

    The symlink farm is append-only, and deliberately so: COLMAP's database
    references every image it has registered by path, so pruning would break a
    continuation rather than tidy it. The cost is that **moderation cannot
    withdraw an image from a site that has been reconstructed once** -- a
    moderator rejects a photograph, the count drops everywhere it is displayed,
    and the next run reads the link that is still sitting in the workspace.

    Measured when this was written: Fontaine Bartholdi had 73 links against 0
    reconstructable images, so every photograph retired from that site would
    have gone straight back into its next reconstruction.

    Naming them is what lets `run_colmap` refuse to continue from a workspace
    that no longer matches the database's idea of the site.
    """
    images_dir = Path(workspace.images)
    if not images_dir.is_dir():
        return set()
    on_disk = {p.name for p in images_dir.iterdir() if p.is_symlink() or p.is_file()}
    allowed = {ref.link_name for ref in image_refs(site)}
    return on_disk - allowed


def materialise_site(site: Site, *, strict: bool = False):
    """
    Refresh the symlink farm for one site and return the workspace.

    `strict=False` by default: a missing source file is a real problem, but it
    is one image out of hundreds, and failing the whole reconstruction over it
    loses more than it protects. The result reports what was missing.
    """
    workspace = Workspace.for_site(site.ID).ensure()
    result = materialise(image_refs(site), workspace.root, strict=strict)
    materialise_masks(site, workspace)
    return workspace, result
