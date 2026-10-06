# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.text import get_valid_filename, slugify

from cintafactory.operations.slo_baseline import emit_baseline_metric
from cintafactory.storage.seaweedfs_storage import SeaweedFSStorage

from .utils import format_user_display

ALLOWED_ATTACHMENT_EXTENSIONS = {
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
    ".svg",
    ".ico",
    ".txt",
    ".doc",
    ".docx",
    ".odt",
    ".rtf",
    ".pdf",
    ".md",
    ".xls",
    ".xlsx",
    ".ods",
    ".csv",
    ".tsv",
    ".ppt",
    ".pptx",
    ".odp",
    ".vsdx",
    ".drawio",
}

MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENT_SIZE_LABEL = "25 MB"
ATTACHMENT_STORAGE_SUBDIR = "dat_attachments"
ATTACHMENT_NAME_MAX_LENGTH = 200
logger = logging.getLogger(__name__)
QUARANTINE_STORAGE_SUBDIR = "dat_attachments_quarantine"

ALLOWED_ATTACHMENT_MIME_TYPES: dict[str, set[str]] = {
    ".jpeg": {"image/jpeg"},
    ".jpg": {"image/jpeg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
    ".svg": {"image/svg+xml", "text/xml", "application/xml"},
    ".ico": {"image/x-icon", "image/vnd.microsoft.icon"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".pdf": {"application/pdf"},
    ".csv": {"text/csv", "text/plain", "application/csv"},
    ".tsv": {"text/tab-separated-values", "text/plain"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    ".odt": {"application/vnd.oasis.opendocument.text", "application/zip", "application/octet-stream"},
    ".rtf": {"application/rtf", "text/rtf"},
    ".xls": {"application/vnd.ms-excel", "application/octet-stream"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/octet-stream",
    },
    ".ods": {"application/vnd.oasis.opendocument.spreadsheet", "application/zip", "application/octet-stream"},
    ".ppt": {"application/vnd.ms-powerpoint", "application/octet-stream"},
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "application/octet-stream",
    },
    ".odp": {"application/vnd.oasis.opendocument.presentation", "application/zip", "application/octet-stream"},
    ".vsdx": {
        "application/vnd.visio",
        "application/vnd.ms-visio.drawing",
        "application/vnd.openxmlformats-officedocument.visio.drawing",
        "application/zip",
        "application/octet-stream",
    },
    ".drawio": {
        "application/xml",
        "text/xml",
        "text/plain",
    },
}


class AttachmentSecurityError(ValidationError):
    def __init__(self, message: str, *, failure_state: str, quarantine_path: str = "") -> None:
        super().__init__(message, code=failure_state)
        self.failure_state = failure_state
        self.quarantine_path = quarantine_path


@dataclass(frozen=True)
class AttachmentMetadata:
    original_name: str
    display_name: str
    extension: str
    size: int
    content_type: str


def get_allowed_attachment_extensions() -> list[str]:
    return sorted(ALLOWED_ATTACHMENT_EXTENSIONS)


def get_allowed_attachment_extensions_display() -> str:
    return ", ".join(get_allowed_attachment_extensions())


def get_allowed_attachment_extensions_accept() -> str:
    return ",".join(get_allowed_attachment_extensions())


def build_attachment_ui_context() -> dict[str, object]:
    max_size_bytes = get_max_attachment_size_bytes()
    return {
        "attachments_allowed_extensions": get_allowed_attachment_extensions(),
        "attachments_allowed_extensions_display": get_allowed_attachment_extensions_display(),
        "attachments_accept": get_allowed_attachment_extensions_accept(),
        "attachments_max_size_bytes": max_size_bytes,
        "attachments_max_size_label": human_readable_size(max_size_bytes),
    }


def sanitize_attachment_name(filename: str) -> str:
    base_name = os.path.basename(filename or "").strip().replace("\x00", "")
    if not base_name:
        return ""
    safe_name = get_valid_filename(base_name)
    safe_name = safe_name.strip().strip(".")
    if not safe_name:
        return ""
    name_root, ext = os.path.splitext(safe_name)
    if not name_root:
        return ""
    ext = ext.lower()
    safe_name = f"{name_root}{ext}"
    if len(safe_name) > ATTACHMENT_NAME_MAX_LENGTH:
        max_root = ATTACHMENT_NAME_MAX_LENGTH - len(ext)
        safe_name = f"{name_root[:max_root]}{ext}"
    return safe_name


def build_attachment_metadata(uploaded_file) -> AttachmentMetadata:
    if uploaded_file is None:
        raise AttachmentSecurityError("Aucun fichier transmis.", failure_state="missing_file")
    size = int(getattr(uploaded_file, "size", 0) or 0)
    max_size_bytes = get_max_attachment_size_bytes()
    if size > max_size_bytes:
        raise AttachmentSecurityError(
            f"Le fichier dépasse la taille maximale autorisée ({human_readable_size(max_size_bytes)}).",
            failure_state="file_too_large",
        )
    original_name = str(getattr(uploaded_file, "name", "") or "")
    display_name = sanitize_attachment_name(original_name)
    if not display_name:
        raise AttachmentSecurityError("Le nom du fichier est invalide.", failure_state="invalid_name")
    extension = os.path.splitext(display_name)[1].lower()
    if not extension or extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise AttachmentSecurityError("L'extension du fichier n'est pas autorisée.", failure_state="extension_not_allowed")
    content_type = str(getattr(uploaded_file, "content_type", "") or "")
    validate_attachment_mime_type(extension, content_type)
    return AttachmentMetadata(
        original_name=original_name,
        display_name=display_name,
        extension=extension,
        size=size,
        content_type=content_type,
    )


def validate_attachment_mime_type(extension: str, content_type: str) -> None:
    declared = str(content_type or "").strip().lower()
    if not declared:
        return
    declared = declared.split(";", 1)[0].strip()
    expected = ALLOWED_ATTACHMENT_MIME_TYPES.get(extension.lower(), set())
    if expected and declared not in expected:
        raise AttachmentSecurityError(
            "Le type MIME du fichier n'est pas autorisé pour cette extension.",
            failure_state="mime_not_allowed",
        )


def build_attachment_storage_name(dat_id: int, section_slug: str, display_name: str) -> str:
    token = uuid.uuid4().hex
    safe_slug = slugify(section_slug or "section") or "section"
    return f"{ATTACHMENT_STORAGE_SUBDIR}/{dat_id}/{safe_slug}/{token}_{display_name}"


def build_download_filename(display_name: str, extension: str | None = None) -> str:
    base, ext = os.path.splitext(display_name)
    ext = (extension or ext or "").lower()
    safe_base = slugify(base) or "piece-jointe"
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    return f"{safe_base}{ext}"


def _get_int_setting(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _get_float_setting(name: str, default: float) -> float:
    try:
        return float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def get_max_attachment_size_bytes() -> int:
    configured = _get_int_setting("DAT_ATTACHMENT_MAX_SIZE_BYTES", MAX_ATTACHMENT_SIZE_BYTES)
    return configured if configured > 0 else MAX_ATTACHMENT_SIZE_BYTES


def human_readable_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        value = size_bytes / (1024 * 1024)
        if float(value).is_integer():
            return f"{int(value)} MB"
        return f"{value:.1f} MB"
    if size_bytes >= 1024:
        value = size_bytes / 1024
        if float(value).is_integer():
            return f"{int(value)} KB"
        return f"{value:.1f} KB"
    return f"{int(size_bytes)} B"


def quarantine_rejected_upload(uploaded_file, *, reason: str) -> str:
    enabled = str(getattr(settings, "ATTACHMENT_QUARANTINE_ENABLED", "1")).lower() in {"1", "true", "yes", "on"}
    if not enabled or uploaded_file is None:
        return ""
    size = int(getattr(uploaded_file, "size", 0) or 0)
    max_quarantine_bytes = _get_int_setting("ATTACHMENT_QUARANTINE_MAX_BYTES", 10 * 1024 * 1024)
    if max_quarantine_bytes > 0 and size > max_quarantine_bytes:
        return ""
    display_name = sanitize_attachment_name(str(getattr(uploaded_file, "name", "") or "upload.bin")) or "upload.bin"
    safe_reason = slugify(reason or "rejected") or "rejected"
    token = uuid.uuid4().hex
    storage_name = f"{QUARANTINE_STORAGE_SUBDIR}/{safe_reason}/{token}_{display_name}"
    storage = get_attachment_storage()
    uploaded_file.seek(0)
    try:
        stored_name = storage.save(storage_name, uploaded_file)
    finally:
        uploaded_file.seek(0)
    return stored_name


def _send_clamav_command(host: str, port: int, timeout: int, command: bytes) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(command)
        return sock.recv(4096)


def _probe_clamav(host: str, port: int, timeout: int) -> None:
    for label, command in (
        ("PING", b"PING\n"),
        ("VERSION", b"VERSION\n"),
        ("COMMANDS", b"COMMANDS\n"),
    ):
        try:
            response = _send_clamav_command(host, port, timeout, command)
            text = response.decode("utf-8", errors="replace").strip()
            print(f"[ClamAV] {label} response: {text}", flush=True)
        except OSError as exc:
            print(f"[ClamAV] {label} failed: {exc}", flush=True)


def _scan_file_with_scan_command(uploaded_file, host: str, port: int, timeout: int) -> bytes:
    scan_dir = getattr(settings, "CLAMAV_SCAN_DIR", "") or ""
    if not scan_dir:
        raise ValidationError("Le scan antivirus n'est pas configuré (CLAMAV_SCAN_DIR manquant).")
    os.makedirs(scan_dir, exist_ok=True)
    try:
        os.chmod(scan_dir, 0o700)
    except OSError:
        pass
    filename = f"upload_{uuid.uuid4().hex}"
    path = os.path.join(scan_dir, filename)
    uploaded_file.seek(0)
    try:
        with open(path, "wb") as target:
            if hasattr(uploaded_file, "chunks"):
                for chunk in uploaded_file.chunks():
                    target.write(chunk)
            else:
                target.write(uploaded_file.read())
    finally:
        uploaded_file.seek(0)
    print(f"[ClamAV] SCAN path: {path}", flush=True)
    try:
        command = f"SCAN {path}\n".encode("utf-8")
        return _send_clamav_command(host, port, timeout, command)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def scan_file_with_clamav(uploaded_file) -> None:
    started_at = time.perf_counter()
    outcome = "unknown"

    def _emit(success: bool, result: str) -> None:
        emit_baseline_metric(
            "upload.clamav.scan",
            duration_ms=(time.perf_counter() - started_at) * 1000.0,
            success=success,
            dimensions={
                "outcome": result,
                "file_size_bytes": getattr(uploaded_file, "size", None),
            },
        )

    host = getattr(settings, "CLAMAV_HOST", "clamav")
    port = _get_int_setting("CLAMAV_PORT", 3310)
    timeout = _get_int_setting("CLAMAV_TIMEOUT", 30)
    retries = _get_int_setting("CLAMAV_RETRY_COUNT", 5)
    delay = _get_float_setting("CLAMAV_RETRY_DELAY", 1.0)
    response = b""
    last_error: Exception | None = None
    response_text = ""
    _probe_clamav(host, port, timeout)
    for attempt in range(retries + 1):
        try:
            print(
                f"[ClamAV] scan attempt {attempt + 1}/{retries + 1} -> {host}:{port} (SCAN)",
                flush=True,
            )
            response = _scan_file_with_scan_command(uploaded_file, host, port, timeout)
            response_text = response.decode("utf-8", errors="replace")
            break
        except ValidationError:
            raise
        except OSError as exc:
            last_error = exc
            logger.warning(
                "ClamAV scan attempt %s/%s failed: %s",
                attempt + 1,
                retries + 1,
                exc,
            )
            print(
                f"[ClamAV] scan attempt {attempt + 1}/{retries + 1} failed: {exc}",
                flush=True,
            )
            if attempt >= retries:
                break
            time.sleep(delay)
    uploaded_file.seek(0)

    if last_error and not response:
        print("[ClamAV] scan failed: service unavailable.", flush=True)
        if isinstance(last_error, (socket.timeout, TimeoutError)):
            outcome = "scanner_timeout"
            _emit(False, outcome)
            raise AttachmentSecurityError(
                "Le scan antivirus a expiré (délai dépassé).",
                failure_state=outcome,
            ) from last_error
        outcome = "scanner_unavailable"
        _emit(False, outcome)
        raise AttachmentSecurityError(
            "Impossible de vérifier le fichier (antivirus indisponible).",
            failure_state=outcome,
        ) from last_error

    if not response_text:
        response_text = response.decode("utf-8", errors="replace")
   
    if "ERROR" in response_text and "No such file or directory" in response_text:
        outcome = "file_unreachable"
        _emit(False, outcome)
        raise AttachmentSecurityError(
            "Le fichier n'est pas accessible par l'antivirus (volume partage manquant).",
            failure_state=outcome,
        )
    if "FOUND" in response_text:
        print(f"[ClamAV] scan result: {response_text.strip()}", flush=True)
        outcome = "infected"
        _emit(False, outcome)
        raise AttachmentSecurityError(
            "Le fichier est infecté et a été refusé.",
            failure_state=outcome,
        )
    if "OK" not in response_text:
        logger.warning("ClamAV response unexpected: %s", response_text.strip())
        print(f"[ClamAV] scan unexpected response: {response_text.strip()}", flush=True)
        outcome = "unexpected_response"
        _emit(False, outcome)
        raise AttachmentSecurityError(
            "La vérification antivirus a échoué.",
            failure_state=outcome,
        )
    print(f"[ClamAV] scan OK: {response_text.strip()}", flush=True)
    outcome = "ok"
    _emit(True, outcome)


@lru_cache(maxsize=1)
def get_attachment_storage() -> SeaweedFSStorage:
    return SeaweedFSStorage()


def create_section_attachment(section, uploaded_file, *, uploaded_by=None):
    from .models import DATSectionAttachment

    try:
        metadata = build_attachment_metadata(uploaded_file)
        scan_file_with_clamav(uploaded_file)
    except AttachmentSecurityError as exc:
        quarantine_path = quarantine_rejected_upload(uploaded_file, reason=exc.failure_state)
        if quarantine_path:
            logger.warning(
                "Attachment rejected and quarantined: state=%s path=%s original_name=%s",
                exc.failure_state,
                quarantine_path,
                getattr(uploaded_file, "name", ""),
            )
        raise AttachmentSecurityError(
            exc.messages[0] if getattr(exc, "messages", None) else str(exc),
            failure_state=exc.failure_state,
            quarantine_path=quarantine_path,
        ) from exc
    dat_id = getattr(section, "dat_id", None)
    if dat_id is None:
        raise ValidationError("Section invalide pour la pièce jointe.")
    storage_name = build_attachment_storage_name(dat_id, section.slug, metadata.display_name)
    storage = get_attachment_storage()
    stored_name = storage.save(storage_name, uploaded_file)
    attachment = DATSectionAttachment(
        section=section,
        storage_path=stored_name,
        original_name=metadata.original_name,
        display_name=metadata.display_name,
        extension=metadata.extension,
        size=metadata.size,
        content_type=metadata.content_type,
        uploaded_by=uploaded_by,
        uploaded_by_display=format_user_display(uploaded_by) if uploaded_by else "",
    )
    try:
        attachment.save()
    except Exception:
        try:
            storage.delete(stored_name)
        except Exception:
            pass
        raise
    return attachment


def delete_section_attachment(attachment) -> None:
    storage = get_attachment_storage()
    path = getattr(attachment, "storage_path", "")
    if path:
        try:
            storage.delete(path)
        except Exception:
            logger.exception("Failed to delete attachment from storage: %s", path)
    attachment.delete()
