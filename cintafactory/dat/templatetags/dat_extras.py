# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import random
import uuid
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlencode

from django import template

from django.urls import NoReverseMatch, reverse
from diagrams.models import DrawIODiagram
from dat.sections import SECTION_BLUEPRINT_MAP

register = template.Library()

DEFAULT_ICON_VARIANT = "material-icons"
MATERIAL_SYMBOLS_VARIANT = "material-symbols-outlined"

SECTION_PART_ICON_MAP = {
    "informations-generales": {
        "informations-administratives": {"name": "badge", "variant": MATERIAL_SYMBOLS_VARIANT},
        "description-solution": {"name": "description", "variant": MATERIAL_SYMBOLS_VARIANT},
    },
    "besoins": {
        "typologie-besoin": {"name": "category", "variant": MATERIAL_SYMBOLS_VARIANT},
        "detail-besoin": {"name": "list_alt", "variant": MATERIAL_SYMBOLS_VARIANT},
    },
    "urbanisme": {
        "mapping-urbanisation-si": {"name": "map", "variant": MATERIAL_SYMBOLS_VARIANT},
        "conformite-urbanisme": {"name": "rule", "variant": MATERIAL_SYMBOLS_VARIANT},
        "impact-si-existant": {"name": "device_hub", "variant": MATERIAL_SYMBOLS_VARIANT},
    },
    "exploitation": {
        "ressources-solution": {"name": "dns", "variant": MATERIAL_SYMBOLS_VARIANT},
        "supervision-monitoring": {"name": "monitor", "variant": MATERIAL_SYMBOLS_VARIANT},
        "sauvegardes-restauration": {"name": "cloud_sync", "variant": MATERIAL_SYMBOLS_VARIANT},
        "securite-conformite-exploitation": {"name": "shield_lock", "variant": MATERIAL_SYMBOLS_VARIANT},
        "support-exploitation": {"name": "support_agent", "variant": MATERIAL_SYMBOLS_VARIANT},
    },
    "validation": {
        "suivi-validation": {"name": "task_alt", "variant": MATERIAL_SYMBOLS_VARIANT},
    },
    "architecture": {
        "presentation-generale": "content_paste",
        "hebergement-environnements": "location_on",
        "solutions-utilisees": {"name": "deployed_code", "variant": MATERIAL_SYMBOLS_VARIANT},
        "briques-techniques": {"name": "stack", "variant": MATERIAL_SYMBOLS_VARIANT},
        "gestion-identites-acces": "person",
        "schemas": {"name": "stacks", "variant": MATERIAL_SYMBOLS_VARIANT},
        "flux": "share",
    },
    "cybersecurite": {
        "analyse-cyber": "content_paste",
        "rgpd": {"name": "contract", "variant": MATERIAL_SYMBOLS_VARIANT},
        "derogations-pssi": {"name": "remove_moderator", "variant": MATERIAL_SYMBOLS_VARIANT},
        "risques": {"name": "support", "variant": MATERIAL_SYMBOLS_VARIANT},
        "plan-traitement-risques": {"name": "shield", "variant": MATERIAL_SYMBOLS_VARIANT},
    },
}

DEFAULT_STATUS_ICON_COLOR = "#546e7a"


SECTION_STATUS_ICON_MAP = {
    "valide": {"name": "task_alt", "color": "#2e7d32", "variant": MATERIAL_SYMBOLS_VARIANT},
    "en_cours": {"name": "pending", "color": "#1565c0", "variant": MATERIAL_SYMBOLS_VARIANT},
    "bloque": {"name": "block", "color": "#c62828", "variant": MATERIAL_SYMBOLS_VARIANT},
}
SECTION_STATUS_ICON_DEFAULT = {
    "name": "radio_button_unchecked",
    "color": DEFAULT_STATUS_ICON_COLOR,
    "variant": MATERIAL_SYMBOLS_VARIANT,
}


@register.filter
def get_item(mapping, key):
    if isinstance(mapping, dict):
        return mapping.get(key)
    return None


@register.filter
def dat_columns(config):
    if isinstance(config, dict):
        columns = config.get("columns")
        if isinstance(columns, list):
            return columns
    return []


@register.filter
def dat_has_drawio_columns(config) -> bool:
    """
    Returns True when a repeater config contains at least one Draw.io column.
    """
    for column in dat_columns(config):
        if not isinstance(column, dict):
            continue
        if column.get("drawio") or column.get("render") == "drawio_diagram":
            return True
    return False


@register.filter
def dat_repeater_full_width(entry, section_slug) -> bool:
    return entry.data_type == "repeater" and (
        dat_has_drawio_columns(entry.config)
        or (section_slug, entry.key) in {
            ("validation", "suivi_sections"),
            ("architecture", "flux"),
            ("exploitation", "ressources"),
        }
    )


def _normalise_icon_config(icon_config):
    if not icon_config:
        return None
    if isinstance(icon_config, str):
        return {"name": icon_config, "variant": DEFAULT_ICON_VARIANT}
    if isinstance(icon_config, dict):
        name = icon_config.get("name")
        if not name:
            return None
        variant = icon_config.get("variant") or DEFAULT_ICON_VARIANT
        return {"name": name, "variant": variant}
    return None


@register.filter
def section_part_icon(section_slug, part_slug):
    if not section_slug or not part_slug:
        return None
    icon = SECTION_PART_ICON_MAP.get(str(section_slug), {}).get(str(part_slug))
    return _normalise_icon_config(icon)


@register.filter
def section_part_tooltip(section_slug, part_slug):
    if not section_slug or not part_slug:
        return ""
    section = SECTION_BLUEPRINT_MAP.get(str(section_slug))
    if not section:
        return ""
    for part in section.get("parts", ()):
        if part.get("slug") == str(part_slug):
            return part.get("tooltip") or ""
    return ""


@register.simple_tag
def section_status_icon(status_value=None):
    icon = SECTION_STATUS_ICON_MAP.get(status_value) or SECTION_STATUS_ICON_DEFAULT
    return {
        "name": icon.get("name"),
        "variant": icon.get("variant", DEFAULT_ICON_VARIANT),
        "color": icon.get("color", DEFAULT_STATUS_ICON_COLOR),
    }


def _extract_diagram_identifier(diagram_id):
    if diagram_id in (None, ""):
        return None, None
    if isinstance(diagram_id, dict):
        for key in ("id", "pk", "diagram_id", "diagramme_id"):
            if key in diagram_id and diagram_id[key] not in (None, ""):
                return diagram_id[key], diagram_id.get("title")
        return None, diagram_id.get("title")
    if hasattr(diagram_id, "pk"):
        return getattr(diagram_id, "pk", None), getattr(diagram_id, "title", None)
    return diagram_id, None


@register.simple_tag
def diagram_links(diagram_id):
    raw_identifier, provided_title = _extract_diagram_identifier(diagram_id)
    if raw_identifier in (None, ""):
        return None
    try:
        pk = raw_identifier if isinstance(raw_identifier, uuid.UUID) else uuid.UUID(str(raw_identifier).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    diagram = DrawIODiagram.objects.filter(pk=pk).only("pk", "title").first()
    if diagram is None:
        return None
    title = str(provided_title).strip() if provided_title not in (None, "") else diagram.title
    try:
        return {
            "pk": diagram.pk,
            "title": title,
            "detail_url": reverse("diagrams:detail", args=[diagram.pk]),
            "edit_url": reverse("diagrams:edit", args=[diagram.pk]),
            "import_url": reverse("diagrams:import_xml", args=[diagram.pk]),
            "export_url": reverse("diagrams:export_xml", args=[diagram.pk]),
        }
    except NoReverseMatch:
        return None


def _normalize_likec4_path(raw_path):
    if not raw_path:
        return ""
    cleaned = str(raw_path).strip().lstrip("/")
    if not cleaned or not cleaned.lower().endswith(".c4"):
        return ""
    parts = Path(cleaned).parts
    if any(part in (".", "..") for part in parts):
        return ""
    return cleaned


@register.simple_tag
def likec4_png_url(storage_path):
    normalized = _normalize_likec4_path(storage_path)
    if not normalized:
        return ""
    try:
        base = reverse("diagrams:likec4_png")
        query = urlencode({"file": normalized})
        return f"{base}?{query}" if query else base
    except Exception:
        return ""


@register.filter
def unique_messages(messages) -> list:
    """
    Deduplicate messages by (level, text) while preserving order.
    """
    if messages is None:
        return []

    unique: List = []
    seen: set[Tuple] = set()

    for msg in messages:
        key = (getattr(msg, "level", None), getattr(msg, "message", repr(msg)))
        
        if key in seen:
            continue
        seen.add(key)
        unique.append(msg)

    return unique
