"""Server-issued LikeC4 identity and bounded source handling."""

import hashlib
import re

from django.conf import settings
from django.core.exceptions import ValidationError

from cintafactory.storage.seaweedfs_storage import SeaweedFSStorage


REVISION_RE = re.compile(r"^[a-f0-9]{64}$")


def source_limit():
    return int(getattr(settings, "LIKEC4_MAX_SOURCE_BYTES", 1024 * 1024))


def read_source(diagram):
    with SeaweedFSStorage().open(diagram.storage_path, "rb") as source:
        raw = source.read(source_limit() + 1)
    if len(raw) > source_limit():
        raise ValidationError("LikeC4 source exceeds configured limit.")
    return raw


def source_revision(raw):
    return hashlib.sha256(raw).hexdigest()


def refresh_revision(diagram):
    """Also reconcile a successful storage write followed by an interrupted response."""
    raw = read_source(diagram)
    revision = source_revision(raw)
    if diagram.revision != revision:
        type(diagram).objects.filter(pk=diagram.pk).update(revision=revision, size=len(raw))
        diagram.revision = revision
        diagram.size = len(raw)
    return revision


def scope_headers(diagram, operation, *, revision=None):
    revision = diagram.revision if revision is None else revision
    if not REVISION_RE.fullmatch(revision or ""):
        raise ValidationError("Missing LikeC4 source revision.")
    return {
        "X-LikeC4-Token": str(getattr(settings, "LIKEC4_API_TOKEN", "")),
        "X-LikeC4-Diagram-Id": str(diagram.pk),
        "X-LikeC4-Storage-Path": diagram.storage_path,
        "X-LikeC4-Revision": revision,
        "X-LikeC4-Operation": operation,
    }


def png_prefix(diagram, revision=None):
    revision = revision or diagram.revision
    if not REVISION_RE.fullmatch(revision or ""):
        raise ValidationError("Missing LikeC4 source revision.")
    return f"diagrams/likec4/{diagram.pk}/views/{revision}/"


def valid_png_path(diagram, path, revision=None):
    if not isinstance(path, str) or not path.startswith(png_prefix(diagram, revision)):
        return False
    suffix = path[len(png_prefix(diagram, revision)):]
    return bool(suffix and suffix.endswith(".png") and all(
        part and part not in {".", ".."} and not any(c in part for c in "\\\x00%:")
        for part in suffix.split("/")
    ))
