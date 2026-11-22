"""The admin's ZIP downloads and the files they are built from."""

import logging
import os
import re
import zipfile

from django.conf import settings
from django.http import StreamingHttpResponse

# Local imports
from ..models import (
    Image,
)
from .common import _image_disposition


def _sanitize(s) -> str:
    """A string safe to use as part of a download's filename."""
    s = re.sub(r"\s+", "_", str(s))
    return re.sub(r"[^A-Za-z0-9_\-\.]+", "", s)


# Read size for one step of a ZIP stream. Also the most a download holds in
# memory at once, whatever the size of the archive.
ZIP_CHUNK = 1024 * 1024


class _ZipSink:
    """
    A write-only, unseekable file object for `zipfile` to write into.

    `zipfile` handles an unseekable target by writing a data descriptor after
    each member instead of seeking back to patch the local header, which is
    what lets an archive go out as it is built rather than after.
    """

    def __init__(self):
        self._chunks = []
        self._pos = 0

    def write(self, data):
        if data:
            self._chunks.append(bytes(data))
            self._pos += len(data)
        return len(data)

    def tell(self):
        return self._pos

    def flush(self):
        pass

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


def _iter_zip(file_tuples, label: str):
    """
    Yield a ZIP of `(source path, name in archive)` pairs, chunk by chunk.

    `ZIP_STORED`, not `ZIP_DEFLATED`. Nearly everything these archives carry
    is a JPEG, a `.splat`/`.sog` or a `.glb`, which are already compressed:
    deflating them spent a sync worker's CPU for a few percent, and a large
    site could run into gunicorn's timeout before the first byte went out.

    Streamed, not staged. The seven copies this replaced each wrote the whole
    archive to a temp file (one of them on a thread it then waited for) and
    only then started sending -- so the worker held the request for the whole
    build and the disk held a second copy of a site's photographs. At most
    `ZIP_CHUNK` bytes of one member are in memory here.

    A file that vanishes between listing and reading is skipped and logged:
    one missing photograph should not cost the operator the other thousand.
    """
    sink = _ZipSink()
    with zipfile.ZipFile(
        sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as zf:
        for src, arc in file_tuples:
            try:
                info = zipfile.ZipInfo.from_file(src, arc, strict_timestamps=False)
                handle = open(src, "rb")
            except OSError:
                logging.exception(f"Error adding file {src} to {label}")
                continue
            info.compress_type = zipfile.ZIP_STORED
            with handle, zf.open(info, "w") as dest:
                while chunk := handle.read(ZIP_CHUNK):
                    dest.write(chunk)
                    data = sink.drain()
                    if data:
                        yield data
            data = sink.drain()
            if data:
                yield data
    data = sink.drain()
    if data:
        yield data


def zip_response(file_tuples, fname: str, log_line: str) -> StreamingHttpResponse:
    """The one way the admin hands out a ZIP. See `_iter_zip`."""
    resp = StreamingHttpResponse(
        _iter_zip(list(file_tuples), fname), content_type="application/zip"
    )
    resp["Content-Disposition"] = f"attachment; filename={fname}"
    logging.info(log_line)
    return resp


def _image_files(contributions, *, prefix_site: bool):
    """
    `(path, arcname)` for every image of these contributions that is on disk.

    Laid out `<contribution>/<disposition>/<file>`, under `<site>/` as well
    when the archive spans sites. One query, not one per contribution.
    """
    images = (
        Image.objects.filter(contribution__in=contributions)
        .select_related("contribution__site")
        .order_by("contribution__contributed_at", "created_at")
    )
    for img in images:
        try:
            img_path = img.image.path
        except Exception:
            continue
        if not os.path.exists(img_path):
            continue
        parts = [
            str(img.contribution_id),
            _image_disposition(img),
            os.path.basename(img_path),
        ]
        if prefix_site:
            parts.insert(0, _sanitize(img.contribution.site.verbose_id)[:50])
        yield img_path, os.path.join(*parts)


def _models_root() -> str:
    return os.path.join(settings.STATIC_ROOT, "models")


def _published_files(run):
    """The published files for one run: `<siteID>_<runID>*` under `published/`."""
    pub_dir = os.path.join(_models_root(), str(run.site_id), "published")
    if not os.path.isdir(pub_dir):
        return []
    prefix = f"{run.site_id}_{run.ID}"
    return [
        os.path.join(root, f)
        for root, _, filenames in os.walk(pub_dir)
        for f in filenames
        if f.startswith(prefix)
    ]


def _run_dir(run):
    """Where a run's directory is: the archive for Archived runs, else `models/`."""
    if not run.directory:
        return None
    path = str(run.directory)
    if os.path.isabs(path):
        return path
    base = settings.ARCHIVE_ROOT if run.status == "Archived" else _models_root()
    return os.path.join(base, path)


def _walk(directory):
    for root, _, filenames in os.walk(directory):
        for f in filenames:
            yield os.path.join(root, f)
