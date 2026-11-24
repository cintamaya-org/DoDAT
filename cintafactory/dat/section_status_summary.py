# SPDX-License-Identifier: AGPL-3.0-only
"""Read the section statuses displayed on the Mes DAT list."""

from collections import defaultdict
from uuid import UUID

from django.db.models import Exists, OuterRef, Prefetch, Subquery, prefetch_related_objects

from diagrams.models import DrawIODiagram, LikeC4Diagram
from workflows.graph_engine import REFUSAL_BLOCKED, SATISFIED_STATUSES, is_graph, refusal_links
from workflows.models import WorkflowNodeAttempt
from workflows.services import bind_workflow_instances

from .constants import DAT_OWNER_EDITABLE_SECTION_SLUGS
from .models import DAT, DATPart, DATPartEntry, DATSection, DATSectionAttachment
from .sections import (
    SECTION_STATUS_BLOCKED_VALUE,
    SECTION_STATUS_ENTRY_KEY,
    SECTION_STATUS_VALIDATED_VALUE,
    index_section_status_rows,
    section_has_status,
    section_value_is_filled,
)
from .utils import format_user_display


STATUS_LABELS = {
    "validated": "Validé",
    "in-progress": "En cours",
    "refused": "Refusé",
    "blocked": "Bloqué",
    "not-started": "Non commencé",
}


def _graph_section_tones(instance) -> dict[str, str]:
    attempts = {attempt.node_id: attempt.status for attempt in instance.current_section_attempts}
    pending_refusals = {link["from"] for link in refusal_links(instance) if link["pending"]}
    nodes_by_section = defaultdict(list)
    for node in instance.definition_version.specification["nodes"]:
        section_slug = node["target"].get("section")
        if section_slug:
            node_id = node["id"]
            status = "rejected" if node_id in pending_refusals else attempts.get(node_id)
            nodes_by_section[section_slug].append((node["type"], status))

    tones = {}
    for slug, nodes in nodes_by_section.items():
        statuses = {status for _, status in nodes}
        if statuses & {"rejected", REFUSAL_BLOCKED}:
            tones[slug] = "refused"
        elif statuses <= SATISFIED_STATUSES and ("approval", "completed") in nodes:
            tones[slug] = "validated"
        elif statuses & {"ready", "completed"}:
            tones[slug] = "in-progress"
    return tones


def _load_legacy_section_statuses(dat_ids):
    if not dat_ids:
        return {}

    current_entry = DATPartEntry.objects.filter(part_id=OuterRef("pk")).order_by("-updated_at", "-id")
    saved_statuses = (
        DATPart.objects.filter(
            sub_section__section__dat_id__in=dat_ids,
            sub_section__section__metadata__slug="validation",
            key=SECTION_STATUS_ENTRY_KEY,
        )
        .annotate(status_rows=Subquery(current_entry.values("payload__data")[:1]))
        .values_list("sub_section__section__dat_id", "status_rows")
        .order_by("order", "id")
    )
    statuses_by_dat = {}
    for dat_id, rows in saved_statuses:
        if dat_id not in statuses_by_dat:
            by_slug, by_title, _ = index_section_status_rows(rows)
            statuses_by_dat[dat_id] = (by_slug, by_title)
    return statuses_by_dat


def _sections_with_saved_values(dat_ids) -> set[UUID]:
    current_entry = DATPartEntry.objects.filter(part_id=OuterRef("part_id")).order_by("-updated_at", "-id")
    values = (
        DATPartEntry.objects.filter(
            part__sub_section__section__dat_id__in=dat_ids,
            pk=Subquery(current_entry.values("pk")[:1]),
            payload__isnull=False,
        )
        .exclude(part__sub_section__section__metadata__slug="validation")
        .values_list("part__sub_section__section_id", "payload__data")
        .order_by()
        .iterator()
    )
    return {section_id for section_id, value in values if section_value_is_filled(value)}


def build_dat_list_section_statuses(dats: list[DAT]) -> dict[UUID, list[dict[str, str]]]:
    if not dats:
        return {}

    dats_by_id = {dat.pk: dat for dat in dats}
    instances = bind_workflow_instances(dats, create_missing=False)
    graph_instances = {
        dat.pk: instance
        for dat in dats
        if (instance := instances.get(str(dat.pk))) and is_graph(instance) and not instance.data.get("archived")
    }
    prefetch_related_objects(
        list(graph_instances.values()),
        Prefetch(
            "node_attempts",
            queryset=WorkflowNodeAttempt.objects.filter(is_current=True).only("instance_id", "node_id", "status"),
            to_attr="current_section_attempts",
        ),
    )
    graph_tones = {dat_id: _graph_section_tones(instance) for dat_id, instance in graph_instances.items()}
    legacy_statuses = _load_legacy_section_statuses(dats_by_id.keys() - graph_instances.keys())

    sections = (
        DATSection.objects.filter(dat_id__in=dats_by_id)
        .exclude(metadata__slug="validation")
        .select_related("metadata", "participant_assignment__user")
        .annotate(
            has_saved_artifacts=(
                Exists(DATSectionAttachment.objects.filter(section_id=OuterRef("pk")))
                | Exists(DrawIODiagram.objects.filter(sections=OuterRef("pk")))
                | Exists(LikeC4Diagram.objects.filter(sections=OuterRef("pk")))
            ),
        )
        .order_by("order", "id")
    )
    filled_sections = _sections_with_saved_values(dats_by_id)

    result = defaultdict(list)
    for section in sections:
        tone = graph_tones.get(section.dat_id, {}).get(section.slug)
        if section.dat_id in legacy_statuses and section_has_status(section.slug):
            by_slug, by_title = legacy_statuses[section.dat_id]
            row = by_slug.get(section.slug) or by_title.get(section.title) or {}
            if row.get("statut") == SECTION_STATUS_VALIDATED_VALUE:
                tone = "validated"
            elif row.get("statut") == SECTION_STATUS_BLOCKED_VALUE:
                tone = "blocked"
        if tone is None:
            tone = "in-progress" if section.pk in filled_sections or section.has_saved_artifacts else "not-started"

        assignment = getattr(section, "participant_assignment", None)
        editor = assignment.user if assignment else None
        if editor is None and section.slug in DAT_OWNER_EDITABLE_SECTION_SLUGS:
            editor = dats_by_id[section.dat_id].owner
        editor_name = format_user_display(editor) if editor is not None else "Non affecté"
        title = section.title or section.slug
        result[section.dat_id].append({"tone": tone, "text": f"{title} · {STATUS_LABELS[tone]} · {editor_name}"})
    return dict(result)
