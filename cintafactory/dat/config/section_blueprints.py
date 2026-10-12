# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Tuple

from django.conf import settings

from cintafactory.conf_utils import ensure_conf_dir

from ..resources import (
    LEGACY_RESOURCE_TYPES,
    RESOURCE_ENTRY_KEY,
    RESOURCE_SECTION_SLUG,
    RESOURCE_SUB_SECTION_SLUG,
)


def _template_path() -> Path:
    return Path(__file__).with_name("section_blueprints.json")


def _conf_path() -> Path:
    conf_dir = ensure_conf_dir()
    return conf_dir / "section_blueprints.json"


def ensure_section_blueprints_file_exists() -> None:
    conf_path = _conf_path()
    if conf_path.exists():
        return
    template_path = _template_path()
    conf_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")


def _group_resource_blueprint(data) -> None:
    """Upgrade existing configuration while keeping custom resource columns."""
    for section in data:
        if section.get("slug") != RESOURCE_SECTION_SLUG:
            continue
        for part in section.get("parts", ()):
            if part.get("slug") != RESOURCE_SUB_SECTION_SLUG:
                continue
            entries = part.get("entries", [])
            legacy = [entry for entry in entries if entry.get("key") in LEGACY_RESOURCE_TYPES]
            if not legacy:
                continue
            grouped = next((entry for entry in entries if entry.get("key") == RESOURCE_ENTRY_KEY), None)
            if grouped is None:
                template = json.loads(_template_path().read_text(encoding="utf-8"))
                grouped = deepcopy(next(
                    entry
                    for blueprint in template if blueprint["slug"] == RESOURCE_SECTION_SLUG
                    for sub_section in blueprint["parts"] if sub_section["slug"] == RESOURCE_SUB_SECTION_SLUG
                    for entry in sub_section["entries"] if entry["key"] == RESOURCE_ENTRY_KEY
                ))
                columns = {column["key"]: column for column in grouped["config"]["columns"][:1]}
                for entry in legacy:
                    for column in (entry.get("config") or {}).get("columns", ()):
                        columns.setdefault(column["key"], deepcopy(column))
                grouped["config"] = {**(legacy[0].get("config") or {}), "columns": list(columns.values())}
            updated_entries = []
            for entry in entries:
                if entry.get("key") in (*LEGACY_RESOURCE_TYPES, RESOURCE_ENTRY_KEY):
                    if grouped not in updated_entries:
                        updated_entries.append(grouped)
                else:
                    updated_entries.append(entry)
            part["entries"] = updated_entries


def _load_section_blueprints() -> Tuple[Dict[str, Any], ...]:
    conf_path = _conf_path()
    if not conf_path.exists():
        ensure_section_blueprints_file_exists()
    config_path = conf_path if conf_path.exists() else _template_path()
    try:
        with config_path.open(encoding="utf-8") as fp:
            data = json.load(fp)
    except json.JSONDecodeError:
        with _template_path().open(encoding="utf-8") as fp:
            data = json.load(fp)
    _group_resource_blueprint(data)
    return tuple(data)


SECTION_BLUEPRINTS: Tuple[Dict[str, Any], ...] = _load_section_blueprints()

__all__ = ["SECTION_BLUEPRINTS", "ensure_section_blueprints_file_exists"]
