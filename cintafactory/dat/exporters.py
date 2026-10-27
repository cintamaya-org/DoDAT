# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import base64
import copy
import json
import logging
import time
import uuid
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.module_loading import import_string

from diagrams.access import get_dat_diagram
from diagrams.images import normalize_png_bytes
from diagrams.likec4_exports import enqueue_likec4_export
from diagrams.likec4_scope import valid_png_path
from diagrams.models import DrawIODiagram, LikeC4Diagram, valid_drawio_png_path
from diagrams.references import normalize_likec4_reference
from cintafactory.storage.seaweedfs_storage import SeaweedFSStorage
from cintafactory.url_safety import is_http_url
from workflows.services import workflow_state, workflow_state_label

from .models import (
    DAT,
    DATPart,
    DATPartEntry,
    DATPartEntryType,
    DATSection,
    DATSubSection,
)
from .sections import sync_dat_sections_if_needed
from .utils import (
    format_user_display,
    isoformat_datetime,
    localize_datetime,
    serialize_role,
    serialize_user,
)

DEFAULT_DAT_EXPORT_MODEL_BUILDER = "dat.exporters.DATExportModelBuilder"
logger = logging.getLogger(__name__)


def get_dat_export_model_builder():
    """
    Return an instance of the configured DAT export builder.
    """
    builder_path = getattr(settings, "DAT_EXPORT_MODEL_BUILDER", DEFAULT_DAT_EXPORT_MODEL_BUILDER)
    try:
        builder_cls = import_string(builder_path)
    except ImportError as exc:  # pragma: no cover - defensive branch
        raise ImproperlyConfigured(
            f"Cannot import DAT export model builder '{builder_path}': {exc}"
        ) from exc
    return builder_cls()


class DATExportModelBuilder:
    """
    Assemble a serialisable representation of a DAT, ready for JSON or PDF exports.

    Override this class (and reference it via the DAT_EXPORT_MODEL_BUILDER setting)
    to customise the export structure or filtering rules.
    """

    include_empty_parts = True
    refresh_likec4_exports = False
    likec4_export_source: str | None = None

    def __init__(self):
        self._participant_role_map: Dict[str, Any] = {}
        self._dat = None

    def _serialize_id(self, value):
        return str(value) if value is not None else None

    def build(self, dat: DAT) -> Dict[str, Any]:
        self._dat = dat
        sync_dat_sections_if_needed(dat)
        self._participant_role_map = self._build_participant_map(dat)
        exported_at = timezone.now()
        payload = {
            "dat": self.build_dat_metadata(dat),
            "application": self.build_application(dat),
            "owner": serialize_user(dat.owner),
            "participants": self.build_participants(dat),
            "sections": self.build_sections(dat),
            "exported_at": isoformat_datetime(exported_at),
            "exported_at_display": localize_datetime(exported_at),
        }
        return payload

    def build_dat_metadata(self, dat: DAT) -> Dict[str, Any]:
        state = workflow_state(dat)
        self._workflow_status_labels = {state: workflow_state_label(dat)}
        return {
            "id": self._serialize_id(dat.pk),
            "reference": dat.reference,
            "title": dat.title,
            "description": dat.description,
            "status": state,
            "status_label": self.get_status_label(state),
            "created_at": isoformat_datetime(dat.created_at),
            "created_at_display": localize_datetime(dat.created_at),
            "updated_at": isoformat_datetime(dat.updated_at),
            "updated_at_display": localize_datetime(dat.updated_at),
        }

    def get_status_label(self, status: str | None) -> str | None:
        if not status:
            return None
        return getattr(self, "_workflow_status_labels", {}).get(status, status)

    def build_application(self, dat: DAT) -> Dict[str, Any] | None:
        application = getattr(dat, "application", None)
        if application is None:
            return None
        return {
            "id": self._serialize_id(application.pk),
            "code": application.code,
            "name": application.name,
            "description": application.description,
        }

    def build_participants(self, dat: DAT) -> List[Dict[str, Any]]:
        participants = []
        for participant in dat.participants.all():
            user = getattr(participant, "user", None)
            role = getattr(participant, "role", None)
            participants.append(
                {
                    "id": self._serialize_id(participant.pk),
                    "user": serialize_user(user),
                    "role": serialize_role(role),
                    "user_display": format_user_display(user),
                    "role_label": getattr(role, "name", None),
                    "role_slug": getattr(role, "slug", None),
                    "assigned_at": isoformat_datetime(participant.created_at),
                    "assigned_at_display": localize_datetime(participant.created_at),
                }
            )
        return participants

    def build_sections(self, dat: DAT) -> List[Dict[str, Any]]:
        self._dat = dat
        sections = []
        for section in self._prefetch_sections(dat):
            section_responsibles = self.build_responsibles(
                dat,
                section.allowed_roles.all(),
                fallback_user=dat.owner,
            )
            sub_sections = []
            for sub_section in section.sub_sections.all():
                sub_responsibles = self.build_responsibles(
                    dat,
                    sub_section.allowed_roles.all(),
                    fallback_entries=section_responsibles,
                )
                sub_sections.append(
                    {
                        "id": self._serialize_id(sub_section.pk),
                        "slug": sub_section.slug,
                        "title": sub_section.title,
                        "description": sub_section.description,
                        "responsibles": sub_responsibles,
                        "parts": self.build_parts(sub_section),
                        "order": sub_section.order,
                    }
                )
            sections.append(
                {
                    "id": self._serialize_id(section.pk),
                    "slug": section.slug,
                    "title": section.title,
                    "description": section.description,
                    "responsibles": section_responsibles,
                    "sub_sections": sub_sections,
                    "order": section.order,
                }
            )
        return sections

    def build_parts(self, sub_section: DATSubSection) -> List[Dict[str, Any]]:
        parts = []
        for part in sub_section.parts.all():
            value = part.value
            entry = part._get_current_entry()
            has_value = value not in (None, "", [], {}, ())
            is_repeater = part.data_type == DATPartEntryType.REPEATER
            table_columns = self._extract_repeater_columns(part, value) if is_repeater else []
            display_value = part.render_value(value)
            diagram_previews = []
            if is_repeater:
                display_value = self._attach_repeater_diagram_previews(part, display_value)
                diagram_previews = self._collect_diagram_previews(display_value)
            payload = {
                "id": self._serialize_id(part.pk),
                "key": part.key,
                "label": part.label,
                "data_type": part.data_type,
                "required": part.required,
                "value": value,
                "display_value": display_value,
                "order": part.order,
                "config": part.config or {},
                "updated_at": isoformat_datetime(getattr(entry, "updated_at", None)),
                "updated_at_display": localize_datetime(getattr(entry, "updated_at", None)),
                "has_value": has_value,
                "is_repeater": is_repeater,
                "table_columns": table_columns,
                "diagram_previews": diagram_previews,
            }
            if self.include_empty_parts or has_value:
                parts.append(payload)
        return parts

    def build_responsibles(
        self,
        dat: DAT,
        roles: Iterable[Any],
        *,
        fallback_user=None,
        fallback_entries: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for role in roles or []:
            participant = self._participant_role_map.get(getattr(role, "slug", None))
            user = getattr(participant, "user", None)
            entries.append(
                {
                    "role": serialize_role(role),
                    "user": serialize_user(user),
                    "user_display": format_user_display(user),
                    "participant_id": self._serialize_id(getattr(participant, "pk", None)),
                }
            )
        if entries:
            return entries
        if fallback_entries:
            return [copy.deepcopy(entry) for entry in fallback_entries]
        if fallback_user:
            return [
                {
                    "role": None,
                    "user": serialize_user(fallback_user),
                    "user_display": format_user_display(fallback_user),
                    "participant_id": None,
                }
            ]
        return []

    def _build_participant_map(self, dat: DAT) -> Dict[str, Any]:
        participant_map: Dict[str, Any] = {}
        for participant in dat.participants.all():
            role = getattr(participant, "role", None)
            slug = getattr(role, "slug", None)
            if slug:
                participant_map[slug] = participant
        return participant_map

    def _prefetch_sections(self, dat: DAT):
        entry_prefetch = Prefetch(
            "entries",
            queryset=DATPartEntry.objects.order_by("-updated_at", "-id"),
        )
        part_prefetch = Prefetch(
            "parts",
            queryset=DATPart.objects.order_by("order", "id").prefetch_related(entry_prefetch),
        )
        sub_section_prefetch = Prefetch(
            "sub_sections",
            queryset=DATSubSection.objects.order_by("order", "id")
            .prefetch_related("allowed_roles")
            .prefetch_related(part_prefetch),
        )
        return (
            dat.sections.order_by("order", "id")
            .select_related("metadata")
            .prefetch_related("allowed_roles")
            .prefetch_related(sub_section_prefetch)
        )

    def _extract_repeater_columns(self, part: DATPart, rows) -> List[Dict[str, Any]]:
        config = part.config or {}
        columns = config.get("columns")
        normalized = []
        if isinstance(columns, list):
            for column in columns:
                if not isinstance(column, dict):
                    continue
                key = column.get("key")
                label = column.get("label") or key
                if not key:
                    continue
                normalized.append({"key": key, "label": label})
        elif isinstance(rows, list) and rows:
            sample = rows[0]
            if isinstance(sample, dict):
                for key in sample.keys():
                    label = key.replace("_", " ").title()
                    normalized.append({"key": key, "label": label})
        return normalized

    def _attach_repeater_diagram_previews(self, part: DATPart, rows):
        if not rows or not isinstance(rows, list):
            return rows
        rows = self._attach_drawio_previews(part, rows)
        rows = self._attach_likec4_previews(part, rows)
        return rows

    def _attach_drawio_previews(self, part: DATPart, rows):
        drawio_columns = self._get_drawio_columns(part)
        if not drawio_columns:
            return rows
        diagram_ids = self._collect_diagram_ids(rows, drawio_columns)
        if not diagram_ids:
            return rows
        previews = self._load_diagram_previews(diagram_ids)
        if not previews:
            return rows
        for row in rows:
            if not isinstance(row, dict):
                continue
            for column in drawio_columns:
                column_key = column.get("key")
                diagram_id = self._normalize_diagram_id(row.get(column_key))
                if diagram_id and diagram_id in previews:
                    diagram_payload = previews[diagram_id]
                    row[f"{column_key}_diagram"] = diagram_payload
                    row.setdefault("drawio_diagram", diagram_payload)
                    title_key = column.get("drawio_name_key") or column.get("drawioNameKey")
                    title = row.get(title_key) if title_key else None
                    column_label = column.get("label") or column_key
                    description = row.get("description") if isinstance(row.get("description"), str) else None
                    self._append_diagram_preview(
                        row,
                        diagram_payload,
                        title=title,
                        description=description,
                        column_label=column_label,
                    )
        return rows

    def _attach_likec4_previews(self, part: DATPart, rows):
        tool_key, reference_key = self._get_likec4_keys(part)
        if not tool_key or not reference_key:
            return rows
        references = self._collect_likec4_references(rows, tool_key, reference_key)
        if not references:
            return rows
        previews = self._load_likec4_previews(references)
        if not previews:
            return rows
        for row in rows:
            if not isinstance(row, dict):
                continue
            tool = str(row.get(tool_key) or "").strip().lower()
            if tool != "likec4":
                continue
            reference = self._normalize_likec4_path(row.get(reference_key))
            if reference and reference in previews:
                diagram_payload = previews[reference]
                row["likec4_diagram"] = diagram_payload
                title = row.get("nom_schema") or row.get(reference_key)
                description = row.get("description") if isinstance(row.get("description"), str) else None
                self._append_diagram_preview(
                    row,
                    diagram_payload,
                    title=title,
                    description=description,
                    column_label="LikeC4",
                )
        return rows

    def _get_likec4_keys(self, part: DATPart) -> tuple[str, str]:
        config = part.config or {}
        columns = config.get("columns") if isinstance(config, dict) else None
        tool_key = "schema_systeme"
        reference_key = "schema_reference"
        if isinstance(columns, list):
            for column in columns:
                if not isinstance(column, dict):
                    continue
                tool_key = column.get("diagram_tool_key") or column.get("diagramToolKey") or tool_key
                reference_key = (
                    column.get("diagram_reference_key")
                    or column.get("diagramReferenceKey")
                    or reference_key
                )
        return tool_key, reference_key

    def _collect_likec4_references(self, rows, tool_key: str, reference_key: str) -> set[str]:
        references: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            tool = str(row.get(tool_key) or "").strip().lower()
            if tool != "likec4":
                continue
            raw = row.get(reference_key)
            if raw in (None, ""):
                continue
            references.add(normalize_likec4_reference(raw))
        return references

    def _normalize_likec4_path(self, raw) -> str:
        if raw in (None, ""):
            return ""
        return normalize_likec4_reference(raw)

    def _load_likec4_previews(self, references: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        previews: Dict[str, Dict[str, Any]] = {}
        if self._dat is None:
            return previews
        references = list(references)
        # Resolve all sources in the current DAT before any refresh/storage I/O.
        meta_map = {
            reference: get_dat_diagram(LikeC4Diagram, self._dat, storage_path=reference)
            for reference in references
        }
        if self.refresh_likec4_exports:
            self._refresh_likec4_exports(references)
            self._wait_for_likec4_exports(references)
            meta_map = {
                reference: get_dat_diagram(LikeC4Diagram, self._dat, storage_path=reference)
                for reference in references
            }
        storage = SeaweedFSStorage()
        if self.refresh_likec4_exports:
            logger.info("PDF export LikeC4: metadata loaded (%s entries).", len(meta_map))
        for reference in references:
            meta = meta_map.get(reference)
            previews[reference] = self._build_likec4_preview(reference, meta, storage)
        return previews

    def _refresh_likec4_exports(self, references: Iterable[str]) -> None:
        references = [ref for ref in references if ref]
        if not references:
            return
        logger.info("PDF export LikeC4: preparing %s export(s).", len(references))
        if not getattr(settings, "LIKEC4_EXPORT_ENABLED", False):
            logger.info("PDF export LikeC4: export disabled by settings.")
            return
        export_url = getattr(settings, "LIKEC4_EXPORT_URL", "").strip()
        if not export_url:
            logger.warning("PDF export LikeC4: LIKEC4_EXPORT_URL not configured.")
            return
        timeout = int(getattr(settings, "LIKEC4_EXPORT_TIMEOUT", 60))
        for reference in references:
            self._request_likec4_export(reference, export_url=export_url, timeout=timeout)

    def _wait_for_likec4_exports(self, references: Iterable[str]) -> None:
        references = [ref for ref in references if ref]
        if not references:
            return
        if not getattr(settings, "LIKEC4_EXPORT_ENABLED", False):
            return
        export_url = getattr(settings, "LIKEC4_EXPORT_URL", "").strip()
        if not export_url:
            return
        timeout = int(getattr(settings, "LIKEC4_EXPORT_TIMEOUT", 60))
        if timeout <= 0:
            return
        deadline = time.monotonic() + timeout
        interval = 2 if timeout >= 2 else 1
        remaining = set(references)
        storage = SeaweedFSStorage()
        logger.info("PDF export LikeC4: waiting for %s export(s) to complete.", len(remaining))
        while remaining and time.monotonic() < deadline:
            metas = LikeC4Diagram.objects.filter(dat_id=self._dat.pk, storage_path__in=list(remaining)).only(
                "storage_path",
                "png_path",
                "png_paths",
            )
            meta_map = {meta.storage_path: meta for meta in metas}
            for reference in list(remaining):
                meta = meta_map.get(reference)
                if self._is_likec4_ready(storage, reference, meta):
                    remaining.remove(reference)
            if remaining:
                time.sleep(interval)
        if remaining:
            logger.warning(
                "PDF export LikeC4: timeout waiting for %s export(s): %s",
                len(remaining),
                ", ".join(sorted(remaining)),
            )
        else:
            logger.info("PDF export LikeC4: all exports ready.")

    def _is_likec4_ready(
        self,
        storage: SeaweedFSStorage,
        storage_path: str,
        meta: LikeC4Diagram | None,
    ) -> bool:
        if not meta:
            return False
        paths: list[str] = []
        if meta.png_path:
            if valid_png_path(meta, meta.png_path):
                paths.append(meta.png_path)
        if isinstance(meta.png_paths, list):
            for entry in meta.png_paths:
                if isinstance(entry, str) and valid_png_path(meta, entry):
                    paths.append(entry)
        if not paths:
            return False
        seen = set()
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            try:
                if not storage.exists(path):
                    return False
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning(
                    "PDF export LikeC4: unable to check PNG availability for %s (%s): %s",
                    storage_path,
                    path,
                    exc,
                )
                return False
        return True

    def _request_likec4_export(self, storage_path: str, *, export_url: str, timeout: int) -> None:
        if self._dat is None:
            return
        get_dat_diagram(LikeC4Diagram, self._dat, storage_path=storage_path)
        source = self.likec4_export_source or "dat_pdf"
        enqueue_likec4_export(storage_path, source=source)

    def _build_likec4_preview(
        self,
        storage_path: str,
        meta: LikeC4Diagram | None,
        storage: SeaweedFSStorage,
    ) -> Dict[str, Any]:
        if self.refresh_likec4_exports:
            logger.info("PDF export LikeC4: building preview for %s", storage_path)
        if meta is None:
            return {"type": "likec4", "title": "", "thumbnail_url": None, "images": []}
        png_paths: list[str] = []
        thumb_path = (
            meta.png_path if meta and meta.png_path and valid_png_path(meta, meta.png_path) else None
        )
        if meta and isinstance(meta.png_paths, list):
            for entry in meta.png_paths:
                if isinstance(entry, str) and valid_png_path(meta, entry):
                    if thumb_path and entry == thumb_path:
                        continue
                    png_paths.append(entry)
        if not png_paths:
            fallback = thumb_path
            if fallback:
                png_paths = [fallback]
                if self.refresh_likec4_exports:
                    logger.info("PDF export LikeC4: fallback PNG for %s -> %s", storage_path, fallback)
        images = []
        for path in png_paths:
            data_uri = self._seaweed_png_data_uri(storage, path)
            label = self._likec4_view_label(path)
            if data_uri:
                images.append({"src": data_uri, "label": label})
            if self.refresh_likec4_exports:
                logger.info(
                    "PDF export LikeC4: image resolved for %s path=%s source=%s",
                    storage_path,
                    path,
                    "data_uri" if data_uri else "url",
                )
        thumbnail_path = thumb_path
        if not thumbnail_path and png_paths:
            thumbnail_path = png_paths[0]
        thumbnail_data_uri = (
            self._seaweed_png_data_uri(storage, thumbnail_path) if thumbnail_path else None
        )
        return {
            "type": "likec4",
            "title": "",
            "thumbnail_url": thumbnail_data_uri,
            "png_paths": png_paths,
            "images": images,
        }

    def _append_diagram_preview(
        self,
        row: Dict[str, Any],
        diagram_payload: Dict[str, Any],
        *,
        title: str | None = None,
        description: str | None = None,
        column_label: str | None = None,
    ) -> None:
        if not isinstance(row, dict) or not diagram_payload:
            return
        previews = row.setdefault("diagram_previews", [])
        if not isinstance(previews, list):
            previews = []
            row["diagram_previews"] = previews
        resolved_title = str(title).strip() if title else ""
        if not resolved_title:
            resolved_title = diagram_payload.get("title") or column_label or "Schéma"
        resolved_description = str(description).strip() if description else ""
        previews.append(
            {
                "title": resolved_title,
                "description": resolved_description,
                "column_label": column_label or "",
                "diagram": diagram_payload,
            }
        )

    def _collect_diagram_previews(self, rows) -> List[Dict[str, Any]]:
        previews: list[Dict[str, Any]] = []
        if not isinstance(rows, list):
            return previews
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_previews = row.get("diagram_previews")
            if not isinstance(row_previews, list):
                continue
            for preview in row_previews:
                if isinstance(preview, dict) and preview.get("diagram"):
                    previews.append(preview)
        return previews

    def _seaweed_png_data_uri(self, storage: SeaweedFSStorage, path: str) -> str | None:
        if not path:
            return None
        raw = None
        handle = None
        try:
            handle = storage.open(path, "rb")
            byte_limit = int(getattr(settings, "DIAGRAM_PNG_MAX_BYTES", 5 * 1024 * 1024))
            raw = handle.read(byte_limit + 1)
        except FileNotFoundError:
            raw = None
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Impossible de lire l'aperçu LikeC4 %s: %s", path, exc)
            raw = None
        finally:
            if handle:
                try:
                    handle.close()
                except Exception:
                    pass
        normalized = normalize_png_bytes(raw) if raw else None
        if normalized is None:
            return None
        encoded = base64.b64encode(normalized).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _likec4_view_label(self, png_path: str) -> str:
        if not png_path:
            return ""
        marker = "/views/"
        if marker in png_path:
            view_path = png_path.split(marker, 1)[1]
            first, separator, remainder = view_path.partition("/")
            if separator and len(first) == 64 and all(character in "0123456789abcdef" for character in first):
                return remainder
            return view_path
        return png_path.rsplit("/", 1)[-1]

    def _get_drawio_columns(self, part: DATPart) -> Sequence[Dict[str, Any]]:
        config = part.config or {}
        columns = config.get("columns")
        if not isinstance(columns, list):
            return ()
        drawio_columns: list[Dict[str, Any]] = []
        for column in columns:
            if not isinstance(column, dict):
                continue
            if column.get("drawio") or column.get("render") == "drawio_diagram":
                drawio_columns.append(column)
        return tuple(drawio_columns)

    def _collect_diagram_ids(self, rows, drawio_columns: Sequence[Dict[str, Any]]):
        diagram_ids: set[uuid.UUID] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            for column in drawio_columns:
                value = row.get(column.get("key"))
                if value in (None, ""):
                    continue
                diagram_id = self._normalize_diagram_id(value)
                if diagram_id is None:
                    raise ValidationError("Référence Draw.io invalide.")
                diagram_ids.add(diagram_id)
        return diagram_ids

    def _normalize_diagram_id(self, raw) -> uuid.UUID | None:
        if raw in (None, ""):
            return None
        try:
            return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw).strip())
        except (TypeError, ValueError, AttributeError):
            return None

    def _load_diagram_previews(self, diagram_ids: Iterable[uuid.UUID]) -> Dict[uuid.UUID, Dict[str, Any]]:
        if self._dat is None:
            return {}
        diagrams = [get_dat_diagram(DrawIODiagram, self._dat, pk=identifier) for identifier in diagram_ids]
        previews: Dict[uuid.UUID, Dict[str, Any]] = {}
        for diagram in diagrams:
            previews[diagram.pk] = self._build_diagram_preview(diagram)
        return previews

    def _build_diagram_preview(self, diagram: DrawIODiagram) -> Dict[str, Any]:
        images = self._build_drawio_images(diagram)
        data_uri = images[0]["src"] if images and str(images[0].get("src", "")).startswith("data:") else None
        return {
            "id": diagram.pk,
            "title": diagram.title,
            "thumbnail_url": data_uri,
            "data_uri": data_uri,
            "images": images,
            "updated_at": isoformat_datetime(diagram.updated_at),
        }

    def _build_drawio_images(self, diagram: DrawIODiagram) -> List[Dict[str, Any]]:
        paths = []
        if isinstance(diagram.png_paths, list):
            for entry in diagram.png_paths:
                if (
                    isinstance(entry, str)
                    and entry.startswith(f"diagrams/{diagram.pk}/views/")
                    and entry.endswith(".png")
                    and all(part not in {"", ".", ".."} for part in entry.split("/"))
                    and not any(character in entry for character in ("\\", "\x00", "%", ":"))
                ):
                    paths.append(entry)
        images: list[Dict[str, Any]] = []
        if paths:
            storage = SeaweedFSStorage()
            for idx, path in enumerate(paths):
                label = f"Page {idx + 1}"
                data_uri = self._seaweed_png_data_uri(storage, path)
                if data_uri:
                    images.append({"src": data_uri, "label": label})
            return images
        data_uri = self._thumbnail_data_uri(diagram)
        if data_uri:
            return [{"src": data_uri}]
        return []

    def _thumbnail_data_uri(self, diagram: DrawIODiagram) -> str | None:
        field = diagram.thumbnail
        if not field or not valid_drawio_png_path(diagram, getattr(field, "name", "")):
            return None
        raw = None
        try:
            field.open("rb")
            byte_limit = int(getattr(settings, "DIAGRAM_PNG_MAX_BYTES", 5 * 1024 * 1024))
            raw = field.read(byte_limit + 1)
        except FileNotFoundError:
            raw = None
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Impossible de lire la miniature du diagramme %s: %s", diagram.pk, exc)
            raw = None
        finally:
            try:
                field.close()
            except Exception:  # pragma: no cover - close best effort
                pass
        normalized = normalize_png_bytes(raw) if raw else None
        if normalized is None:
            return None
        encoded = base64.b64encode(normalized).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _generate_drawio_thumbnail(self, diagram: DrawIODiagram) -> str | None:
        xml_payload = diagram.read_xml() or "<mxGraphModel/>"
        for export_url in self._get_drawio_export_candidates():
            payload = urlencode(
                {
                    "format": "png",
                    "scale": "1",
                    "xml": xml_payload,
                    "bg": "#ffffff",
                    "base64": "1",
                }
            ).encode("utf-8")
            request = Request(
                export_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urlopen(request, timeout=30) as response:
                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status}")
                    payload_base64 = response.read().decode("utf-8").strip()
            except Exception as exc:  # pragma: no cover - best-effort export fallback
                logger.warning(
                    "Impossible de générer une miniature Draw.io pour le diagramme %s via %s: %s",
                    diagram.pk,
                    export_url,
                    exc,
                )
                continue
            if payload_base64:
                return "data:image/png;base64," + payload_base64
        return None

    def _get_drawio_export_candidates(self) -> List[str]:
        candidates: list[str] = []
        configured = getattr(settings, "DRAWIO_EXPORT_URL", "").rstrip("/")
        if configured:
            if is_http_url(configured):
                candidates.append(configured)
            else:
                logger.warning("DRAWIO_EXPORT_URL ignored: non-http(s) scheme.")
        base_url = getattr(settings, "DRAWIO_BASE_URL", "").rstrip("/")
        if base_url:
            if is_http_url(base_url):
                fallback = f"{base_url}/export"
                if fallback not in candidates:
                    candidates.append(fallback)
            else:
                logger.warning("DRAWIO_BASE_URL ignored: non-http(s) scheme.")
        return candidates
