# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from . import sections


class DATSectionDefinitionTests(SimpleTestCase):
    def test_section_capabilities_follow_slug_and_blueprint(self):
        self.assertFalse(sections.section_has_status(""))
        self.assertFalse(sections.section_has_status("informations-generales"))
        self.assertFalse(sections.section_has_status("validation"))
        self.assertTrue(sections.section_has_status("architecture"))

        with mock.patch.dict(
            sections.SECTION_BLUEPRINT_MAP,
            {
                "no-attachments": {"attachments_enabled": False},
                "default-attachments": {},
            },
        ):
            self.assertFalse(sections.section_has_attachments(""))
            self.assertFalse(sections.section_has_attachments("no-attachments"))
            self.assertTrue(sections.section_has_attachments("default-attachments"))
            self.assertTrue(sections.section_has_attachments("unknown-section"))

    def test_part_normalization_supplies_placeholders_and_stable_defaults(self):
        placeholders = sections._build_placeholder_parts("architecture")
        self.assertEqual(len(placeholders), sections.PLACEHOLDER_SUBSECTION_COUNT)
        self.assertEqual(placeholders[0]["slug"], "architecture-placeholder-1")
        self.assertEqual(placeholders[0]["entries"], ())
        self.assertEqual(sections._normalise_parts("architecture", None), placeholders)

        normalized = sections._normalise_parts(
            "architecture",
            [{"slug": "schemas", "entries": [{"key": "diagram"}]}],
        )
        self.assertEqual(
            normalized,
            ({"slug": "schemas", "title": "Schemas", "description": "", "entries": ({"key": "diagram"},)},),
        )

    def test_blueprint_lookup_handles_missing_section_and_part(self):
        with mock.patch.dict(
            sections.SECTION_BLUEPRINT_MAP,
            {"sample": {"parts": [{"slug": "overview", "title": "Overview"}]}},
        ):
            self.assertEqual(
                sections._find_blueprint_part("sample", "overview"),
                {"slug": "overview", "title": "Overview"},
            )
            self.assertIsNone(sections._find_blueprint_part("sample", "missing"))
            self.assertIsNone(sections._find_blueprint_part("missing", "overview"))

    def test_legacy_model_resolution_and_part_foreign_key_fallbacks(self):
        classes = {
            ("dat", "DATSection"): type("Section", (), {}),
            ("dat", "DATSectionMetadata"): type("Metadata", (), {}),
            ("dat", "DATSectionPart"): type("LegacySubSection", (), {}),
            ("dat", "DATPartEntry"): type("LegacyPart", (), {}),
            ("users", "Role"): type("Role", (), {}),
        }

        class Apps:
            def get_model(self, app_label, model_name):
                try:
                    return classes[(app_label, model_name)]
                except KeyError as exc:
                    raise LookupError(model_name) from exc

        resolved = sections._resolve_models(Apps())
        self.assertEqual(resolved, tuple(classes[key] for key in (
            ("dat", "DATSection"),
            ("dat", "DATSectionMetadata"),
            ("dat", "DATSectionPart"),
            ("dat", "DATPartEntry"),
            ("users", "Role"),
        )))

        legacy_part_model = SimpleNamespace(
            _meta=SimpleNamespace(get_fields=lambda: [SimpleNamespace(name="part")])
        )
        current_part_model = SimpleNamespace(
            _meta=SimpleNamespace(get_fields=lambda: [SimpleNamespace(name="sub_section")])
        )
        self.assertEqual(sections._dat_part_fk_field(legacy_part_model), "part")
        self.assertEqual(sections._dat_part_fk_field(current_part_model), "sub_section")

    def test_validation_rows_are_normalized_and_stale_rows_are_removed(self):
        rows = [
            {
                "section_slug": "architecture",
                "section": "Old label",
                "statut": "valide",
                "statut_responsable": "",
                "reserve_message": "  ",
                "reserve_by_id": "42",
                "reserve_by_display": "Alice",
                "commentaire": "Existing comment",
            },
            {"section_slug": "removed", "section": "Removed"},
            "malformed",
            {},
        ]
        section_rows = [
            {"slug": "architecture", "title": "Architecture"},
            {"slug": "informations-generales", "title": "Informations générales"},
            {"slug": "validation", "title": "Validation"},
            {"slug": "without-title", "title": ""},
        ]
        section_query = mock.MagicMock()
        section_query.exclude.return_value.order_by.return_value.values.return_value = section_rows
        entry = SimpleNamespace(
            key=sections.SECTION_STATUS_ENTRY_KEY,
            value=rows,
            update_value=mock.Mock(),
        )
        section = SimpleNamespace(dat=SimpleNamespace(sections=section_query))

        sections._initialise_validation_statuses(entry, section)

        entry.update_value.assert_called_once_with(
            [
                {
                    "section": "Architecture",
                    "section_slug": "architecture",
                    "statut": "valide",
                    "statut_responsable": sections.SECTION_STATUS_DEFAULT,
                    "reserve_message": "",
                    "reserve_by_id": None,
                    "reserve_by_display": "",
                    "commentaire": "Existing comment",
                }
            ]
        )
        section_query.exclude.assert_called_once_with(metadata__slug="validation")

    def test_validation_rows_avoid_rewrites_when_already_canonical(self):
        canonical = {
            "section": "Architecture",
            "section_slug": "architecture",
            "statut": sections.SECTION_STATUS_DEFAULT,
            "statut_responsable": sections.SECTION_STATUS_DEFAULT,
            "reserve_message": "",
            "reserve_by_id": None,
            "reserve_by_display": "",
            "commentaire": "",
        }
        section_query = mock.MagicMock()
        section_query.exclude.return_value.order_by.return_value.values.return_value = [
            {"slug": "architecture", "title": "Architecture"}
        ]
        entry = SimpleNamespace(
            key=sections.SECTION_STATUS_ENTRY_KEY,
            value=[canonical],
            update_value=mock.Mock(),
        )

        sections._initialise_validation_statuses(
            entry,
            SimpleNamespace(dat=SimpleNamespace(sections=section_query)),
        )

        entry.update_value.assert_not_called()

    def test_validation_initializer_skips_unrelated_or_unavailable_entries(self):
        update_value = mock.Mock()
        sections._initialise_validation_statuses(
            SimpleNamespace(key="other", update_value=update_value),
            SimpleNamespace(dat=object()),
        )
        sections._initialise_validation_statuses(
            SimpleNamespace(key=sections.SECTION_STATUS_ENTRY_KEY, update_value=update_value),
            SimpleNamespace(dat=None),
        )
        update_value.assert_not_called()

        class BrokenEntry:
            key = sections.SECTION_STATUS_ENTRY_KEY
            update_value = mock.Mock()

            @property
            def value(self):
                raise RuntimeError("payload unavailable")

        broken_sections = mock.Mock()
        broken_sections.exclude.side_effect = RuntimeError("sections unavailable")
        sections._initialise_validation_statuses(
            BrokenEntry(),
            SimpleNamespace(dat=SimpleNamespace(sections=broken_sections)),
        )
        BrokenEntry.update_value.assert_not_called()
