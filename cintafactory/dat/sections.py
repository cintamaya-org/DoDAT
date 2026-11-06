# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Tuple, Optional

from django.db import transaction
from django.db.models import F

from .config.section_blueprints import SECTION_BLUEPRINTS
from .resources import (
    LEGACY_RESOURCE_TYPES,
    RESOURCE_ENTRY_KEY,
    RESOURCE_SECTION_SLUG,
    RESOURCE_SUB_SECTION_SLUG,
    group_resource_rows,
)

PLACEHOLDER_SUBSECTION_COUNT = 3
SECTION_STATUS_ENTRY_KEY = "suivi_sections"
SECTION_STATUS_DEFAULT = "en_cours"
SECTION_STATUS_BLOCKED_VALUE = "bloque"
SECTION_STATUS_VALIDATED_VALUE = "valide"
SECTION_STATUS_DISABLED_SLUGS = {"informations-generales", "besoins"}


def section_has_status(section_slug: str) -> bool:
    if not section_slug:
        return False
    return section_slug not in SECTION_STATUS_DISABLED_SLUGS and section_slug != "validation"


def section_has_attachments(section_slug: str) -> bool:
    if not section_slug:
        return False
    blueprint = SECTION_BLUEPRINT_MAP.get(section_slug)
    if not blueprint:
        return True
    return bool(blueprint.get("attachments_enabled", True))


def _build_placeholder_parts(section_slug: str) -> Tuple[Dict[str, Any], ...]:
    """
    Provide default sub-sections so that each section is ready to be refined later.
    """
    parts: list[Dict[str, Any]] = []
    for index in range(1, PLACEHOLDER_SUBSECTION_COUNT + 1):
        parts.append(
            {
                "slug": f"{section_slug}-placeholder-{index}",
                "title": f"Sous-section {index}",
                "description": (
                    "Personnalisez cette sous-section en mettant à jour DEFAULT_DAT_SECTION_DEFINITIONS "
                    "et en ajoutant vos champs (entries)."
                ),
                "entries": (),
            }
        )
    return tuple(parts)


def _normalise_parts(section_slug: str, raw_parts: Iterable[Dict[str, Any]] | None) -> Tuple[Dict[str, Any], ...]:
    if not raw_parts:
        return _build_placeholder_parts(section_slug)
    normalised_parts: list[Dict[str, Any]] = []
    for part in raw_parts:
        normalised_part = {
            "slug": part["slug"],
            "title": part.get("title", part["slug"].replace("-", " ").title()),
            "description": part.get("description", ""),
            "entries": tuple(part.get("entries", ())),
        }
        normalised_parts.append(normalised_part)
    return tuple(normalised_parts)


DEFAULT_DAT_SECTION_DEFINITIONS: Tuple[Dict[str, Any], ...] = tuple(
    {
        "slug": blueprint["slug"],
        "title": blueprint["title"],
        "description": blueprint.get("description", ""),
        "attachments_enabled": blueprint.get("attachments_enabled", True),
        "allowed_roles": blueprint.get("allowed_roles", []),
        "parts": _normalise_parts(blueprint["slug"], blueprint.get("parts")),
    }
    for blueprint in SECTION_BLUEPRINTS
)

SECTION_BLUEPRINT_MAP: Dict[str, Dict[str, Any]] = {
    blueprint["slug"]: blueprint for blueprint in SECTION_BLUEPRINTS
}


def _find_blueprint_part(section_slug: str, part_slug: str) -> Optional[Dict[str, Any]]:
    blueprint = SECTION_BLUEPRINT_MAP.get(section_slug)
    if not blueprint:
        return None
    for part in blueprint.get("parts", ()):
        if part["slug"] == part_slug:
            return part
    return None


def _resolve_models(apps=None):
    if apps is not None:
        DATSectionModel = apps.get_model("dat", "DATSection")
        DATSectionMetadataModel = apps.get_model("dat", "DATSectionMetadata")
        try:
            DATSubSectionModel = apps.get_model("dat", "DATSubSection")
        except LookupError:
            DATSubSectionModel = apps.get_model("dat", "DATSectionPart")
        try:
            DATPartModel = apps.get_model("dat", "DATPart")
        except LookupError:
            DATPartModel = apps.get_model("dat", "DATPartEntry")
        RoleModel = apps.get_model("users", "Role")
    else:
        from users.models import Role as RoleModel  # type: ignore

        from .models import DATPart as DATPartModel  # noqa: WPS433
        from .models import DATSection as DATSectionModel  # noqa: WPS433
        from .models import DATSectionMetadata as DATSectionMetadataModel  # noqa: WPS433
        from .models import DATSubSection as DATSubSectionModel  # noqa: WPS433

    return DATSectionModel, DATSectionMetadataModel, DATSubSectionModel, DATPartModel, RoleModel


def _section_sub_section_manager(section):
    return getattr(section, "sub_sections", getattr(section, "parts", None))


def _sub_section_entries_manager(sub_section):
    return getattr(sub_section, "parts", getattr(sub_section, "entries", None))


def _dat_part_fk_field(dat_part_model):
    field_names = {field.name for field in dat_part_model._meta.get_fields()}
    return "sub_section" if "sub_section" in field_names else "part"


def _initialise_validation_statuses(entry, section) -> None:
    """
    Populate the validation status table with default "en cours" rows for each section.
    """
    if not entry or entry.key != SECTION_STATUS_ENTRY_KEY:
        return
    dat = getattr(section, "dat", None)
    if dat is None:
        return
    try:
        existing_rows = entry.value or []
    except Exception:
        existing_rows = []
    existing_by_slug = {}
    existing_by_title = {}
    stale_rows = False
    for row in existing_rows:
        if not isinstance(row, dict):
            stale_rows = True
            continue
        slug = row.get("section_slug")
        if slug not in (None, ""):
            existing_by_slug[str(slug)] = row
            continue
        label = row.get("section")
        if label not in (None, ""):
            existing_by_title[str(label)] = row
            continue
        stale_rows = True
    try:
        sections = (
            dat.sections.exclude(metadata__slug="validation")
            .order_by("order", "id")
            .values(slug=F("metadata__slug"), title=F("metadata__title"))
        )
    except Exception:
        sections = ()
    updated_rows: list[dict[str, object]] = []
    dirty = stale_rows
    valid_identifiers = set()
    for item in sections:
        slug = item.get("slug")
        title = item.get("title")
        if not title:
            continue
        if not section_has_status(str(slug)):
            continue
        valid_identifiers.add(str(slug))
        valid_identifiers.add(str(title))
        current_row = existing_by_slug.get(str(slug)) or existing_by_title.get(str(title))
        statut = None
        statut_responsable = None
        reserve_message = ""
        reserve_by_id = None
        reserve_by_display = ""
        comment = ""
        if isinstance(current_row, dict):
            statut = current_row.get("statut")
            statut_responsable = current_row.get("statut_responsable")
            reserve_message = str(current_row.get("reserve_message") or "").strip()
            reserve_by_id = current_row.get("reserve_by_id")
            reserve_by_display = str(current_row.get("reserve_by_display") or "").strip()
            comment = current_row.get("commentaire") or ""
        if not statut:
            statut = SECTION_STATUS_DEFAULT
        if not statut_responsable:
            statut_responsable = SECTION_STATUS_DEFAULT
        if not reserve_message:
            reserve_by_id = None
            reserve_by_display = ""
        normalised = {
            "section": title,
            "section_slug": slug,
            "statut": statut,
            "statut_responsable": statut_responsable,
            "reserve_message": reserve_message,
            "reserve_by_id": reserve_by_id,
            "reserve_by_display": reserve_by_display,
            "commentaire": comment,
        }
        updated_rows.append(normalised)
        if (
            current_row is None
            or current_row.get("section") != normalised["section"]
            or current_row.get("section_slug") != normalised["section_slug"]
            or current_row.get("statut") != normalised["statut"]
            or current_row.get("statut_responsable") != normalised["statut_responsable"]
            or str(current_row.get("reserve_message") or "").strip() != normalised["reserve_message"]
            or current_row.get("reserve_by_id") != normalised["reserve_by_id"]
            or str(current_row.get("reserve_by_display") or "").strip() != normalised["reserve_by_display"]
            or (current_row.get("commentaire") or "") != normalised["commentaire"]
        ):
            dirty = True
    for label in (*existing_by_slug.keys(), *existing_by_title.keys()):
        if label not in valid_identifiers:
            dirty = True
            break
    if dirty:
        entry.update_value(updated_rows)


def ensure_default_sections(dat, *, apps=None) -> None:
    """
    Ensure the default DAT sections structure exists for the given DAT instance.
    """
    DATSectionModel, DATSectionMetadataModel, DATSubSectionModel, DATPartModel, RoleModel = _resolve_models(apps)
    part_fk_field = _dat_part_fk_field(DATPartModel)
    db_alias = getattr(dat._state, "db", None) or "default"

    with transaction.atomic(using=db_alias):
        existing_sections = (
            DATSectionModel.objects.using(db_alias)
            .select_related("metadata")
            .filter(dat_id=dat.pk)
        )
        existing_section_map = {}
        for section in existing_sections:
            metadata = getattr(section, "metadata", None)
            if metadata and metadata.slug:
                existing_section_map[metadata.slug] = section
        for section_order, section_definition in enumerate(DEFAULT_DAT_SECTION_DEFINITIONS):
            metadata_defaults = {
                "title": section_definition["title"],
                "description": section_definition.get("description", ""),
            }
            section = existing_section_map.get(section_definition["slug"])
            if section is None:
                metadata = DATSectionMetadataModel.objects.using(db_alias).create(
                    slug=section_definition["slug"],
                    **metadata_defaults,
                )
                section = DATSectionModel.objects.using(db_alias).create(
                    dat_id=dat.pk,
                    metadata=metadata,
                    order=section_order,
                )
                existing_section_map[section_definition["slug"]] = section
            else:
                if section.metadata_id is None:
                    metadata = DATSectionMetadataModel.objects.using(db_alias).create(
                        slug=section_definition["slug"],
                        **metadata_defaults,
                    )
                    section.metadata = metadata
                else:
                    metadata = section.metadata
                meta_updates = []
                if metadata and metadata.title != metadata_defaults["title"]:
                    metadata.title = metadata_defaults["title"]
                    meta_updates.append("title")
                if metadata and metadata.description != metadata_defaults["description"]:
                    metadata.description = metadata_defaults["description"]
                    meta_updates.append("description")
                if metadata and metadata.slug != section_definition["slug"]:
                    metadata.slug = section_definition["slug"]
                    meta_updates.append("slug")
                if meta_updates:
                    metadata.save(update_fields=meta_updates)

            section_updates = []
            if getattr(section, "order", None) != section_order:
                section.order = section_order
                section_updates.append("order")
            if section.metadata_id is None and metadata is not None:
                section.metadata = metadata
                section_updates.append("metadata")
            if section_updates:
                section.save(update_fields=section_updates)

            allowed_slugs: Iterable[str] = section_definition.get("allowed_roles", [])
            if allowed_slugs is not None:
                roles = list(RoleModel.objects.using(db_alias).filter(slug__in=allowed_slugs))
                section.allowed_roles.set(roles)

            sub_section_manager = _section_sub_section_manager(section)
            existing_sub_sections = {item.slug: item for item in sub_section_manager.all()} if sub_section_manager else {}
            blueprint_parts = section_definition.get("parts", ())
            expected_part_slugs = {part["slug"] for part in blueprint_parts}

            for part_order, part_definition in enumerate(blueprint_parts):
                part_defaults = {
                    "title": part_definition["title"],
                    "description": part_definition.get("description", ""),
                    "order": part_order,
                }
                sub_section = existing_sub_sections.get(part_definition["slug"])
                if sub_section is None:
                    sub_section = DATSubSectionModel.objects.using(db_alias).create(
                        section=section,
                        slug=part_definition["slug"],
                        **part_defaults,
                    )
                else:
                    part_updates = []
                    for field, value in part_defaults.items():
                        if getattr(sub_section, field) != value:
                            setattr(sub_section, field, value)
                            part_updates.append(field)
                    if part_updates:
                        sub_section.save(update_fields=part_updates)

                entry_manager = _sub_section_entries_manager(sub_section)
                existing_entries = {entry.key: entry for entry in entry_manager.all()} if entry_manager else {}
                expected_entry_keys = {entry["key"] for entry in part_definition.get("entries", ())}

                allowed_sub_roles = part_definition.get("allowed_roles")
                if allowed_sub_roles is not None and hasattr(sub_section, "allowed_roles"):
                    roles = list(RoleModel.objects.using(db_alias).filter(slug__in=allowed_sub_roles))
                    sub_section.allowed_roles.set(roles)

                for entry_order, entry_definition in enumerate(part_definition.get("entries", ())):
                    entry_defaults = {
                        "label": entry_definition["label"],
                        "data_type": entry_definition["type"],
                        "order": entry_order,
                        "required": entry_definition.get("required", False),
                        "config": entry_definition.get("config"),
                    }
                    entry = existing_entries.get(entry_definition["key"])
                    if entry is None:
                        creation_kwargs = {
                            part_fk_field: sub_section,
                            "key": entry_definition["key"],
                            **entry_defaults,
                        }
                        entry = DATPartModel.objects.using(db_alias).create(**creation_kwargs)
                        _initialise_validation_statuses(entry, section)
                    else:
                        entry_updates = []
                        for field, value in entry_defaults.items():
                            if getattr(entry, field) != value:
                                setattr(entry, field, value)
                                entry_updates.append(field)
                        if entry_updates:
                            entry.save(update_fields=entry_updates + ["updated_at"])
                        _initialise_validation_statuses(entry, section)

                    if (
                        section_definition["slug"] == RESOURCE_SECTION_SLUG
                        and part_definition["slug"] == RESOURCE_SUB_SECTION_SLUG
                        and entry.key == RESOURCE_ENTRY_KEY
                        and any(key in existing_entries for key in LEGACY_RESOURCE_TYPES)
                    ):
                        values = {
                            key: existing_entries[key].value
                            for key in LEGACY_RESOURCE_TYPES if key in existing_entries
                        }
                        values[RESOURCE_ENTRY_KEY] = entry.value
                        rows = group_resource_rows(values)
                        if rows != entry.value:
                            entry.update_value(rows)

                # Remove entries no longer defined
                removable_entries = [
                    entry for key, entry in existing_entries.items() if key not in expected_entry_keys
                ]
                for entry in removable_entries:
                    entry.delete()

            # Remove parts no longer defined
            removable_sub_sections = [
                part for slug, part in existing_sub_sections.items() if slug not in expected_part_slugs
            ]
            for sub_section in removable_sub_sections:
                sub_section.delete()


def _serialise_config(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _configs_match(left: Any, right: Any) -> bool:
    if left in (None, {}) and right in (None, {}):
        return True
    return _serialise_config(left) == _serialise_config(right)


def dat_sections_need_sync(dat) -> bool:
    """
    Determine whether the DAT sections deviate from the expected blueprint definitions.
    """
    expected_section_order = [blueprint["slug"] for blueprint in SECTION_BLUEPRINTS]
    actual_section_order = list(
        dat.sections.order_by("order", "id").values_list("metadata__slug", flat=True)
    )
    if actual_section_order != expected_section_order[: len(actual_section_order)] or len(actual_section_order) != len(expected_section_order):
        return True

    sections = dat.sections.select_related("metadata").all()
    for section in sections:
        blueprint = SECTION_BLUEPRINT_MAP.get(section.slug)
        if not blueprint:
            continue
        expected_title = blueprint.get("title", "")
        expected_description = blueprint.get("description", "")
        if section.title != expected_title:
            return True
        if section.description != expected_description:
            return True
        blueprint_parts = blueprint.get("parts", ())
        if not blueprint_parts:
            continue
        expected_part_slugs = {part["slug"] for part in blueprint_parts}
        sub_section_manager = _section_sub_section_manager(section)
        if not sub_section_manager:
            return True
        actual_part_slugs = set(sub_section_manager.values_list("slug", flat=True))
        if actual_part_slugs != expected_part_slugs:
            return True
        for sub_section in sub_section_manager.all():
            matching_part = next((item for item in blueprint_parts if item["slug"] == sub_section.slug), None)
            if matching_part is None:
                return True
            expected_entry_keys = {entry["key"] for entry in matching_part.get("entries", ())}
            entry_manager = _sub_section_entries_manager(sub_section)
            if not entry_manager:
                return True
            actual_entry_keys = set(entry_manager.values_list("key", flat=True))
            if actual_entry_keys != expected_entry_keys:
                return True
            blueprint_entry_map = {entry["key"]: entry for entry in matching_part.get("entries", ())}
            for part in entry_manager.all():
                blueprint_entry = blueprint_entry_map.get(part.key)
                if blueprint_entry is None:
                    return True
                if part.data_type != blueprint_entry.get("type"):
                    return True
                if part.label != blueprint_entry.get("label"):
                    return True
                if not _configs_match(part.config or None, blueprint_entry.get("config")):
                    return True
    return False


def sync_dat_sections_if_needed(dat) -> bool:
    """
    Ensure the DAT sections match the current blueprint definitions.
    Returns True if changes were applied.
    """
    if not getattr(dat, "pk", None):
        return False
    if not dat_sections_need_sync(dat):
        return False
    ensure_default_sections(dat)
    cache = getattr(dat, "_prefetched_objects_cache", None)
    if cache:
        cache.pop("sections", None)
    return True
