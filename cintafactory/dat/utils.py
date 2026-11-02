# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from typing import Any, Dict

from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify

from cintafactory.storage.seaweedfs_storage import SeaweedFSStorage

logger = logging.getLogger(__name__)

def format_user_display(user) -> str:
    """
    Provide a readable display for any user-like object.
    """
    if user is None:
        return "-"
    if hasattr(user, "get_full_name"):
        full_name = user.get_full_name()
        if full_name:
            return full_name
    if hasattr(user, "get_username"):
        return user.get_username()
    return str(user)


def serialize_user(user) -> Dict[str, Any] | None:
    if user is None:
        return None
    username = user.get_username() if hasattr(user, "get_username") else ""
    full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
    return {
        "id": str(getattr(user, "pk", "")) if getattr(user, "pk", None) is not None else None,
        "username": username,
        "full_name": full_name or "",
        "email": getattr(user, "email", "") or "",
        "display": format_user_display(user),
    }


def serialize_role(role) -> Dict[str, Any] | None:
    if role is None:
        return None
    return {
        "id": str(getattr(role, "pk", "")) if getattr(role, "pk", None) is not None else None,
        "slug": getattr(role, "slug", None),
        "name": getattr(role, "name", None),
    }


def localize_datetime(value, fmt: str = "%d/%m/%Y à %Hh%M") -> str:
    if not value:
        return ""
    reference = timezone.localtime(value)
    return reference.strftime(fmt)


def isoformat_datetime(value) -> str | None:
    if not value:
        return None
    reference = timezone.localtime(value)
    return reference.isoformat()


def _dat_pdf_export_basename(dat) -> str:
    base = slugify(getattr(dat, "reference", "") or getattr(dat, "title", "")) or f"dat-{dat.pk}"
    return f"{base}.pdf"


def get_dat_pdf_export_path(dat) -> str:
    basename = _dat_pdf_export_basename(dat)
    return os.path.join("dat_exports", str(dat.pk), basename)


def validated_dat_pdf_export_path(dat) -> str | None:
    """Accept historical PDF filenames, but only within this DAT's namespace."""
    path = dat.pdf_export_path or get_dat_pdf_export_path(dat)
    if not isinstance(path, str):
        return None
    parts = path.split("/")
    if (
        len(parts) != 3
        or parts[:2] != ["dat_exports", str(dat.pk)]
        or not parts[2].lower().endswith(".pdf")
        or parts[2] in {".pdf", "..pdf"}
        or any(character in path for character in ("\\", "%"))
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        logger.warning("Rejected invalid PDF export reference for DAT %s", dat.pk)
        return None
    return path


@lru_cache(maxsize=1)
def get_dat_export_storage() -> SeaweedFSStorage:
    return SeaweedFSStorage()


def store_dat_pdf_export(dat, content: bytes, *, refresh_modified: bool = True) -> str:
    path = get_dat_pdf_export_path(dat)
    storage = get_dat_export_storage()
    if storage.exists(path):
        if refresh_modified:
            storage.delete(path)
        else:
            new_hash = hashlib.sha256(content).hexdigest()
            try:
                with storage.open(path, "rb") as existing:
                    current_hash = hashlib.sha256(existing.read()).hexdigest()
            except Exception:
                current_hash = None
            if current_hash and current_hash == new_hash:
                return path
            storage.delete(path)
    storage.save(path, ContentFile(content))
    dat.pdf_export_path = path
    dat.pdf_export_size = len(content or b"")
    dat.pdf_export_content_type = "application/pdf"
    dat.save(update_fields=["pdf_export_path", "pdf_export_size", "pdf_export_content_type", "updated_at"])
    return path


def dat_pdf_export_exists(dat) -> bool:
    path = validated_dat_pdf_export_path(dat)
    if path is None:
        return False
    storage = get_dat_export_storage()
    try:
        return storage.exists(path)
    except Exception as exc:  # pragma: no cover - defensive guard for runtime storage outages
        logger.warning("Unable to check PDF export availability for DAT %s: %s", dat.pk, exc)
        return False


def dat_pdf_export_modified_at(dat):
    path = validated_dat_pdf_export_path(dat)
    if path is None:
        return None
    storage = get_dat_export_storage()
    try:
        exists = storage.exists(path)
    except Exception as exc:  # pragma: no cover - defensive guard for runtime storage outages
        logger.warning("Unable to check PDF export modified time for DAT %s: %s", dat.pk, exc)
        return None
    if not exists:
        return None
    try:
        modified = storage.get_modified_time(path)
    except (OSError, NotImplementedError) as exc:
        logger.warning("Unable to read PDF export modified time for DAT %s: %s", dat.pk, exc)
        return None
    return timezone.localtime(modified)


def open_dat_pdf_export(dat):
    path = validated_dat_pdf_export_path(dat)
    if path is None:
        return None
    storage = get_dat_export_storage()
    try:
        exists = storage.exists(path)
    except Exception as exc:
        logger.warning("Unable to check PDF export before opening DAT %s: %s", dat.pk, exc)
        return None
    if not exists:
        return None
    try:
        return storage.open(path, "rb")
    except Exception as exc:
        logger.warning("Unable to open PDF export for DAT %s: %s", dat.pk, exc)
        return None
