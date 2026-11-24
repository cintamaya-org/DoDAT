# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve review scope without consulting or changing section edit assignments."""

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), cls=DjangoJSONEncoder)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class TargetFacts:
    """Read fresh database facts once per reconciliation, never prefetched view caches."""

    def __init__(self, dat):
        from dat.models import DAT, DATPart, DATSection

        self.dat = DAT.objects.get(pk=dat.pk)
        self._resolved = {}
        self._digests = {}
        self.sections = list(
            DATSection.objects.filter(dat_id=dat.pk).select_related("metadata").prefetch_related(
                "sub_sections", "attachments", "drawiodiagram_diagrams", "likec4diagram_diagrams",
            ).order_by("metadata__slug")
        )
        self.parts = list(
            DATPart.objects.filter(sub_section__section__dat_id=dat.pk)
            .exclude(sub_section__section__metadata__slug="validation")
            .select_related("sub_section__section__metadata")
            .prefetch_related("entries__payload").order_by("sub_section__section__metadata__slug", "sub_section__slug", "key")
        )

    def resolve(self, target):
        key = canonical(target)
        if key not in self._resolved:
            self._resolved[key] = self._resolve(target)
        return self._resolved[key]

    def fingerprint(self, target):
        key = canonical(target)
        if key not in self._digests:
            self._digests[key] = digest(self.resolve(target))
        return self._digests[key]

    def _resolve(self, target):
        sections = [section for section in self.sections if target.get("dat") or section.slug == target.get("section")]
        if not sections or (not target.get("dat") and target.get("section") == "validation"):
            return None
        parts = [part for part in self.parts if part.sub_section.section_id in {section.pk for section in sections}]
        if "subsection" in target:
            if not any(sub.slug == target["subsection"] for section in sections for sub in section.sub_sections.all()):
                return None
            parts = [part for part in parts if part.sub_section.slug == target["subsection"]]
        if "part" in target:
            parts = [part for part in parts if part.key == target["part"]]
            if not parts:
                return None
        content = {
            "parts": [
                {"section": part.sub_section.section.slug, "subsection": part.sub_section.slug,
                 "key": part.key, "label": part.label, "required": part.required, "value": part.value}
                for part in parts
            ],
        }
        if target.get("dat"):
            content["dat"] = {key: str(getattr(self.dat, key) or "") for key in ("reference", "title", "description", "application_id", "business_direction_id")}
        # Files belong to sections, so changes invalidate section/DAT approvals,
        # not an unrelated field or subsection approval.
        if "subsection" not in target:
            content["sections"] = [
                {"slug": section.slug, "title": section.title,
                 "attachments": [
                     {"id": str(item.pk), "path": item.storage_path, "name": item.original_name, "size": item.size}
                     for item in sorted(section.attachments.all(), key=lambda item: str(item.pk))
                 ],
                 "drawio": [
                     {"id": str(item.pk), "path": item.xml_file.name, "size": item.xml_size, "revision": item.source_revision}
                     for item in sorted(section.drawiodiagram_diagrams.all(), key=lambda item: str(item.pk))
                 ],
                 "likec4": [
                     {"id": str(item.pk), "path": item.storage_path, "revision": item.revision}
                     for item in sorted(section.likec4diagram_diagrams.all(), key=lambda item: str(item.pk))
                 ]}
                for section in sections if section.slug != "validation"
            ]
        return json.loads(canonical(content))

    def condition(self, expression, content):
        operator, value = next(iter(expression.items()))
        if operator == "all":
            return all(self.condition(child, content) for child in value)
        if operator == "any":
            return any(self.condition(child, content) for child in value)
        if operator == "not":
            return not self.condition(value, content)
        if operator == "required_content_complete":
            return all(bool(item["value"].strip()) if isinstance(item["value"], str) else item["value"] not in (None, [], {}) for item in content["parts"] if item["required"])
        if operator == "no_open_reserves":
            from dat.models import DATPart
            rows = DATPart.objects.filter(sub_section__section__dat=self.dat, key="suivi_sections").first()
            values = rows.value if rows else []
            if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
                return False
            slugs = {item["section"] for item in content["parts"]} | {item["slug"] for item in content.get("sections", [])}
            return not any(row.get("section_slug") in slugs and row.get("reserve_message") for row in values)
        return False

    def describe(self, target):
        if target.get("dat"):
            return "DAT complet"
        section = next((section for section in self.sections if section.slug == target.get("section")), None)
        if section is None:
            return target.get("section", "Contenu introuvable")
        labels = [section.title]
        if target.get("subsection"):
            sub = next((sub for sub in section.sub_sections.all() if sub.slug == target["subsection"]), None)
            labels.append(sub.title if sub else target["subsection"])
        if target.get("part"):
            part = next((part for part in self.parts if part.sub_section.section_id == section.pk and part.sub_section.slug == target["subsection"] and part.key == target["part"]), None)
            labels.append(part.label if part else target["part"])
        return " / ".join(labels)

    def can_submit(self, target, actor):
        if not getattr(actor, "is_authenticated", False):
            return False
        if target.get("dat"):
            from dat.permissions import user_can_change_dat
            return user_can_change_dat(self.dat, actor)
        sections = [section for section in self.sections if target.get("dat") or section.slug == target.get("section")]
        return bool(sections) and all(section.can_user_edit(actor) for section in sections if section.slug != "validation")
