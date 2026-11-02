# SPDX-License-Identifier: AGPL-3.0-only

"""Validate references before source reads; copy cross-DAT attachments privately."""

import copy
import re
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404

from cintafactory.async_jobs import enqueue_drawio_export_job, enqueue_likec4_export_job
from cintafactory.storage.seaweedfs_storage import SeaweedFSStorage
from dat.permissions import user_is_dat_admin, user_is_dat_admin_for_dat

from .access import bind_diagram, get_diagram_for_user, require_section_write
from .likec4_scope import read_source, source_revision
from .models import DrawIODiagram, LikeC4Diagram, likec4_source_path_for


def normalize_likec4_reference(raw):
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise ValidationError("Référence LikeC4 invalide.")
    path = raw
    if (
        not path.lower().endswith(".c4")
        or any(char in path for char in ("\\", ":", "\x00"))
        or any(part in ("", ".", "..") for part in path.split("/"))
    ):
        raise ValidationError("Référence LikeC4 invalide.")
    return path


def diagram_reference_keys(config):
    columns = config.get("columns", []) if isinstance(config, dict) else []
    tool_key, reference_key = "schema_systeme", "schema_reference"
    drawio_keys = []
    for column in columns if isinstance(columns, list) else []:
        if not isinstance(column, dict):
            continue
        tool_key = column.get("diagram_tool_key") or column.get("diagramToolKey") or tool_key
        reference_key = column.get("diagram_reference_key") or column.get("diagramReferenceKey") or reference_key
        if column.get("drawio") or column.get("render") == "drawio_diagram":
            key = column.get("key")
            if isinstance(key, str) and key and key not in drawio_keys:
                drawio_keys.append(key)
    return tool_key, reference_key, drawio_keys


def iter_diagram_references(config, rows):
    """Yield (kind, row index, configured key, normalized source identifier)."""
    if not isinstance(rows, list):
        return
    tool_key, reference_key, drawio_keys = diagram_reference_keys(config)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if str(row.get(tool_key) or "").strip().lower() == "likec4":
            raw = row.get(reference_key)
            if raw not in (None, ""):
                yield "likec4", index, reference_key, normalize_likec4_reference(raw)
        for key in drawio_keys:
            raw = row.get(key)
            if raw in (None, ""):
                continue
            try:
                identifier = uuid.UUID(str(raw).strip())
            except (ValueError, TypeError, AttributeError):
                raise ValidationError("Référence Draw.io invalide.") from None
            yield "drawio", index, key, identifier


def _validated_references(part, value, user):
    refs = list(iter_diagram_references(part.config, value))
    if not refs:
        return []
    section = part.sub_section.section
    require_section_write(section, user)
    validated = []
    for kind, index, key, identifier in refs:
        model = LikeC4Diagram if kind == "likec4" else DrawIODiagram
        lookup = {"storage_path": identifier} if kind == "likec4" else {"pk": identifier}
        try:
            source = get_diagram_for_user(model, user, **lookup)
        except Http404:
            raise ValidationError("Diagramme inconnu ou inaccessible.") from None
        # Unlinked sources become shared DAT objects only through their owner.
        if source.dat_id is None and not (
            source.owner_id == getattr(user, "pk", None) or user_is_dat_admin(user)
        ):
            raise ValidationError("Diagramme inconnu ou inaccessible.")
        if source.dat_id is not None and source.dat_id != section.dat_id and not (
            source.owner_id == getattr(user, "pk", None)
            or user_is_dat_admin_for_dat(source.dat, user)
        ):
            raise ValidationError("Vous ne pouvez pas copier ce diagramme depuis un autre DAT.")
        # Existing bindings can be retained by section editors. A new binding
        # within the same DAT requires authority over the source object.
        if source.dat_id == section.dat_id and not source.sections.filter(pk=section.pk).exists():
            if not (
                source.owner_id == getattr(user, "pk", None)
                or user_is_dat_admin_for_dat(section.dat, user)
            ):
                raise ValidationError("Vous ne pouvez pas associer ce diagramme à cette section.")
        validated.append((kind, index, key, source))
    return validated


def _copy_diagram(source, section, user):
    # The caller validates every reference before reaching any storage read.
    if isinstance(source, LikeC4Diagram):
        try:
            raw = read_source(source)
        except (OSError, IOError, ValidationError):
            raise ValidationError("Source du diagramme indisponible.") from None
        storage = SeaweedFSStorage()
        copied = LikeC4Diagram(
            title=source.title, owner=user, dat=section.dat,
            revision=source_revision(raw), content_type="text/plain", size=len(raw),
        )
        copied.storage_path = likec4_source_path_for(copied.pk)
        content = ContentFile(raw)
        content.content_type = "text/plain"
        storage.save(copied.storage_path, content)
        copied.save()
        copied.sections.add(section)
        transaction.on_commit(lambda: enqueue_likec4_export_job(
            copied.storage_path, requested_by=user, source="dat_copy",
        ))
        return copied
    copied = DrawIODiagram.objects.create(title=source.title, owner=user, dat=section.dat)
    xml_payload = source.read_xml()
    if not xml_payload:
        raise ValidationError("Source du diagramme indisponible.")
    copied.write_xml(xml_payload)
    copied.sections.add(section)
    transaction.on_commit(lambda: enqueue_drawio_export_job(
        copied.pk, xml_payload=xml_payload, requested_by=user, source="dat_copy",
    ))
    return copied


def prepare_diagram_references(part, value, user, *, attach=False, copies=None):
    """Validate without side effects, then explicitly attach/copy on save."""
    try:
        refs = _validated_references(part, value, user)
    except PermissionDenied:
        raise ValidationError("Vous ne pouvez pas modifier les diagrammes de cette section.") from None
    if not attach or not refs:
        return value
    section = part.sub_section.section
    prepared = copy.deepcopy(value)
    copies = copies if copies is not None else {}
    with transaction.atomic():
        for kind, index, key, source in refs:
            if source.dat_id is not None and source.dat_id != section.dat_id:
                cache_key = (kind, source.pk, section.dat_id)
                attached = copies.get(cache_key)
                if attached is None:
                    attached = _copy_diagram(source, section, user)
                    copies[cache_key] = attached
                else:
                    attached.sections.add(section)
            elif source.dat_id == section.dat_id and source.sections.filter(pk=section.pk).exists():
                attached = source
            else:
                attached = bind_diagram(source, section, user)
            prepared[index][key] = attached.storage_path if kind == "likec4" else str(attached.pk)
    return prepared


def backfill_legacy_diagrams(*, apps=None, dry_run=False):
    """Infer only unique current DAT references; never guess ownership or read files."""
    if apps is None:
        from django.apps import apps
    PartEntry = apps.get_model("dat", "DATPartEntry")
    references = {"drawio": {}, "likec4": {}}
    seen_parts = set()
    entries = PartEntry.objects.select_related(
        "payload", "part__sub_section__section",
    ).order_by("part_id", "-updated_at", "-id")
    for entry in entries.iterator():
        if entry.part_id in seen_parts:
            continue
        seen_parts.add(entry.part_id)
        rows = entry.payload.data if entry.payload_id else None
        if not isinstance(rows, list):
            continue
        section = entry.part.sub_section.section
        for row in rows:
            try:
                refs = list(iter_diagram_references(entry.part.config, [row]))
            except ValidationError:
                continue
            for kind, _index, _key, identifier in refs:
                dats = references[kind].setdefault(identifier, {})
                dats.setdefault(section.dat_id, set()).add(section.pk)

    report = {
        "bound": 0,
        "section_bindings": 0,
        "unresolved": 0,
        "png_reset": 0,
        "assignments": [],
        "unresolved_diagrams": [],
    }
    with transaction.atomic():
        for kind, name in (("drawio", "DrawIODiagram"), ("likec4", "LikeC4Diagram")):
            model = apps.get_model("diagrams", name)
            for diagram in model.objects.all().iterator():
                identifier = diagram.storage_path if kind == "likec4" else diagram.pk
                dats = references[kind].get(identifier, {})
                target_dat = diagram.dat_id
                if target_dat is None and len(dats) == 1:
                    target_dat = next(iter(dats))
                    report["bound"] += 1
                    report["assignments"].append({
                        "kind": kind,
                        "diagram_id": str(diagram.pk),
                        "dat_id": str(target_dat),
                        "section_ids": sorted(str(value) for value in dats[target_dat]),
                    })
                    if not dry_run:
                        model.objects.filter(pk=diagram.pk, dat__isnull=True).update(dat_id=target_dat)
                if target_dat is None:
                    report["unresolved"] += 1
                    report["unresolved_diagrams"].append({
                        "kind": kind,
                        "diagram_id": str(diagram.pk),
                        "reason": "multiple_dat_references" if len(dats) > 1 else "no_current_dat_reference",
                        "candidate_dat_ids": sorted(str(value) for value in dats),
                    })
                elif target_dat in dats:
                    current_sections = set(diagram.sections.values_list("pk", flat=True))
                    missing = dats[target_dat] - current_sections
                    report["section_bindings"] += len(missing)
                    if missing and not dry_run:
                        diagram.sections.add(*missing)
                if kind == "likec4":
                    prefix = f"diagrams/likec4/{diagram.pk}/views/{diagram.revision}/"
                    managed_revision = bool(re.fullmatch(r"[0-9a-f]{64}", diagram.revision or ""))
                    paths = [diagram.png_path] if diagram.png_path else []
                    paths.extend(diagram.png_paths if isinstance(diagram.png_paths, list) else [])
                    unsafe = any(
                        not managed_revision or not isinstance(path, str)
                        or not path.startswith(prefix) or not path.endswith(".png")
                        or any(segment in ("", ".", "..") for segment in path.split("/"))
                        for path in paths
                    )
                    if unsafe:
                        report["png_reset"] += 1
                        if not dry_run:
                            model.objects.filter(pk=diagram.pk).update(
                                png_path="", png_paths=[], png_size=0, png_updated_at=None,
                                png_content_type="image/png",
                            )
    return report
