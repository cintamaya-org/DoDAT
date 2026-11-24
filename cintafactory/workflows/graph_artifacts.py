# SPDX-License-Identifier: AGPL-3.0-only
"""Preserve the file versions reviewed, even after a source is replaced or deleted."""

import hashlib
from time import monotonic
from tempfile import SpooledTemporaryFile

from django.conf import settings
from django.core.files import File

from cintafactory.storage.seaweedfs_storage import SeaweedFSStorage


def snapshot_files(instance, content, *, deadline=None):
    files = []
    for section in content.get("sections", []):
        for kind in ("attachments", "drawio", "likec4"):
            for item in section[kind]:
                if item.get("path"):
                    files.append((section["slug"], kind, item))
    if not files:
        return []
    if len(files) > 128:
        raise ValueError("A review snapshot can contain at most 128 files.")
    deadline = deadline if deadline is not None else monotonic() + 15
    storage = SeaweedFSStorage(timeout=5)
    remaining = int(getattr(settings, "WORKFLOW_SNAPSHOT_MAX_BYTES", 64 * 1024 * 1024))
    frozen = []
    for section, kind, item in files:
        if monotonic() >= deadline:
            raise ValueError("Review snapshot time budget exceeded.")
        path = item["path"]
        if path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")) or any(char in path for char in "\\\x00%"):
            raise ValueError("Invalid review source path.")
        checksum = hashlib.sha256()
        size = 0
        with SpooledTemporaryFile(max_size=8 * 1024 * 1024) as copy, storage.open(path, "rb") as source:
            while chunk := source.read(min(1024 * 1024, remaining + 1)):
                if monotonic() >= deadline:
                    raise ValueError("Review snapshot time budget exceeded.")
                size += len(chunk)
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("Review files exceed the configured snapshot size limit.")
                checksum.update(chunk)
                copy.write(chunk)
            sha256 = checksum.hexdigest()
            if kind in {"likec4", "drawio"} and item.get("revision") and sha256 != item["revision"]:
                raise ValueError("Diagram source changed before its revision was recorded.")
            destination = f"workflow-snapshots/{instance.object_id}/{sha256}"
            if not storage.exists(destination):
                copy.seek(0)
                storage.save(destination, File(copy))
        name = item.get("name") or f"{kind}-{item['id']}.{'drawio' if kind == 'drawio' else 'c4'}"
        frozen.append({"section": section, "kind": kind, "source_id": item["id"], "name": name, "sha256": sha256, "size": size, "path": destination})
    return frozen
