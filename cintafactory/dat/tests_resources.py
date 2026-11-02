# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase, TestCase

from users.models import BusinessDirection, Role
from workflows.definitions import WORKFLOW_DEFINITIONS
from workflows.sync import sync_workflow_definitions

from .config import section_blueprints
from .exporters import DATExportModelBuilder
from .forms import DATSubSectionForm, RepeatableTableWidget
from .importers import DATImportError, DATImportService
from .models import Application, DAT, DATPart, DATPartEntryType
from .sections import dat_sections_need_sync, sync_dat_sections_if_needed
from .templatetags.dat_extras import dat_repeater_full_width


class ResourceBlueprintTests(SimpleTestCase):
    def test_existing_configuration_groups_resources_and_preserves_custom_columns(self):
        template = json.loads(section_blueprints._template_path().read_text(encoding="utf-8"))
        section = next(item for item in template if item["slug"] == "exploitation")
        part = next(item for item in section["parts"] if item["slug"] == "ressources-solution")
        grouped = part["entries"][0]
        legacy = []
        for key in ("ressources_existantes", "ressources_a_ajouter", "ressources_a_supprimer"):
            entry = deepcopy(grouped)
            entry["key"] = key
            entry["config"]["columns"] = entry["config"]["columns"][1:]
            legacy.append(entry)
        custom_column = {"key": "commentaire", "label": "Commentaire", "type": "text"}
        legacy[1]["config"]["columns"].append(custom_column)
        part["entries"] = legacy
        other_parts = deepcopy(section["parts"][1:])

        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "section_blueprints.json"
            original = json.dumps(template, ensure_ascii=False)
            config_path.write_text(original, encoding="utf-8")
            with mock.patch.object(section_blueprints, "_conf_path", return_value=config_path):
                loaded = section_blueprints._load_section_blueprints()
            self.assertEqual(config_path.read_text(encoding="utf-8"), original)

        updated_section = next(item for item in loaded if item["slug"] == "exploitation")
        entries = updated_section["parts"][0]["entries"]
        self.assertEqual([entry["key"] for entry in entries], ["ressources"])
        self.assertEqual(entries[0]["config"]["columns"][0]["key"], "type_ressource")
        self.assertEqual(entries[0]["config"]["columns"][-1], custom_column)
        self.assertEqual(updated_section["parts"][1:], other_parts)


class ResourceGroupingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for definition in WORKFLOW_DEFINITIONS:
            roles = {
                role for step in definition.steps for permission in step.permissions for role in permission.roles
            } | {role for transition in definition.transitions for role in transition.roles}
            for slug in roles:
                Role.objects.get_or_create(slug=slug, defaults={"name": slug})
        sync_workflow_definitions()

    def setUp(self):
        direction = BusinessDirection.objects.create(slug="resource-business", name="Resource business")
        self.application = Application.objects.create(
            code="resource-app", name="Resource app", business_direction=direction,
        )
        self.dat = DAT.objects.create(reference="DAT-RESOURCES", title="Resources", application=self.application)
        self.sub_section = (
            self.dat.sections.get(metadata__slug="exploitation")
            .sub_sections.get(slug="ressources-solution")
        )
        self.part = self.sub_section.parts.get(key="ressources")

    def _legacy_part(self, key, rows):
        part = DATPart.objects.create(
            sub_section=self.sub_section,
            key=key,
            label=key,
            data_type=DATPartEntryType.REPEATER,
            config={"columns": self.part.config["columns"][1:]},
        )
        part.update_value(rows)
        return part

    def _resource_payload(self, payload):
        section = next(item for item in payload["sections"] if item["slug"] == "exploitation")
        return next(item for item in section["sub_sections"] if item["slug"] == "ressources-solution")

    def test_new_dat_exposes_one_table_with_resource_type_choices(self):
        form = DATSubSectionForm(self.sub_section)
        self.assertEqual(list(form.fields), [self.part.form_field_name()])
        widget = form.fields[self.part.form_field_name()].widget
        self.assertIsInstance(widget, RepeatableTableWidget)
        self.assertEqual(widget.columns[0], {
            "key": "type_ressource",
            "label": "Type de ressource",
            "type": "select",
            "choices": [
                {"value": "sans_modification", "label": "Sans modification"},
                {"value": "a_ajouter", "label": "À ajouter"},
                {"value": "a_supprimer", "label": "À supprimer"},
            ],
        })
        self.assertTrue(dat_repeater_full_width(self.part, "exploitation"))

    def test_sync_preserves_all_rows_and_is_idempotent(self):
        current = {"id": "current", "type_ressource": "a_ajouter"}
        self.part.update_value([current])
        self._legacy_part("ressources_existantes", [{"id": "same", "compute": "4 vCPU"}])
        self._legacy_part("ressources_a_ajouter", [{"id": "same", "compute": "8 vCPU"}, {"id": "new"}])
        self._legacy_part("ressources_a_supprimer", [{"id": "old", "type_ressource": "sans_modification"}])

        self.assertTrue(dat_sections_need_sync(self.dat))
        self.assertTrue(sync_dat_sections_if_needed(self.dat))
        part = self.sub_section.parts.get(key="ressources")
        self.assertEqual(part.value, [
            current,
            {"id": "same", "compute": "4 vCPU", "type_ressource": "sans_modification"},
            {"id": "same", "compute": "8 vCPU", "type_ressource": "a_ajouter"},
            {"id": "new", "type_ressource": "a_ajouter"},
            {"id": "old", "type_ressource": "a_supprimer"},
        ])
        self.assertEqual(self.sub_section.parts.count(), 1)
        self.assertFalse(sync_dat_sections_if_needed(self.dat))

    def test_sync_creates_grouped_table_from_partial_and_empty_legacy_tables(self):
        self._legacy_part("ressources_existantes", [])
        self._legacy_part("ressources_a_ajouter", json.dumps([{"id": "new"}]))
        self._legacy_part("ressources_a_supprimer", None)
        self.part.delete()

        sync_dat_sections_if_needed(self.dat)

        part = self.sub_section.parts.get(key="ressources")
        self.assertEqual(part.value, [{"id": "new", "type_ressource": "a_ajouter"}])
        self.assertEqual(self.sub_section.parts.count(), 1)

    def test_sync_keeps_legacy_data_when_rows_are_invalid(self):
        legacy = self._legacy_part("ressources_a_ajouter", {"unexpected": "data"})
        self.part.delete()

        with self.assertRaisesMessage(ValueError, "liste de lignes"):
            sync_dat_sections_if_needed(self.dat)

        self.assertTrue(DATPart.objects.filter(pk=legacy.pk).exists())
        self.assertFalse(self.sub_section.parts.filter(key="ressources").exists())
        self.assertEqual(DATPart.objects.get(pk=legacy.pk).value, {"unexpected": "data"})

    def test_form_saves_types_and_row_order_and_export_keeps_raw_values(self):
        rows = [
            {"id": "remove", "type_ressource": "a_supprimer"},
            {"id": "keep", "type_ressource": "sans_modification"},
            {"id": "add", "type_ressource": "a_ajouter"},
        ]
        form = DATSubSectionForm(self.sub_section, data={self.part.form_field_name(): json.dumps(rows)})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn("ressources", form.save())
        part = self.sub_section.parts.get(key="ressources")
        self.assertEqual(part.value, rows)
        exported = self._resource_payload(DATExportModelBuilder().build(self.dat))["parts"][0]
        self.assertEqual(exported["value"], rows)
        self.assertEqual(
            [row["type_ressource"] for row in exported["display_value"]],
            ["À supprimer", "Sans modification", "À ajouter"],
        )
        self.assertEqual(part.value, rows)

    def test_import_legacy_export_groups_all_three_tables(self):
        payload = DATExportModelBuilder().build(self.dat)
        payload["dat"]["reference"] = "DAT-LEGACY-IMPORT"
        resource_payload = self._resource_payload(payload)
        resource_payload["parts"] = [
            {"key": "ressources_a_supprimer", "value": [{"id": "old"}]},
            {"key": "ressources_a_ajouter", "value": [{"id": "new"}]},
            {"key": "ressources_existantes", "value": [{"id": "keep"}]},
        ]

        result = DATImportService().import_from_payload(payload)

        part = (
            result.dat.sections.get(metadata__slug="exploitation")
            .sub_sections.get(slug="ressources-solution").parts.get(key="ressources")
        )
        self.assertEqual(part.value, [
            {"id": "keep", "type_ressource": "sans_modification"},
            {"id": "new", "type_ressource": "a_ajouter"},
            {"id": "old", "type_ressource": "a_supprimer"},
        ])
        self.assertFalse(any("ressources" in warning for warning in result.warnings))

    def test_grouped_resources_round_trip_through_json_export(self):
        rows = [{"id": "new", "type_ressource": "a_ajouter", "environnement": "recette"}]
        self.part.update_value(rows)
        payload = DATExportModelBuilder().build(self.dat)
        payload["dat"]["reference"] = "DAT-RESOURCE-ROUND-TRIP"

        result = DATImportService().import_from_payload(payload)

        exported = self._resource_payload(DATExportModelBuilder().build(result.dat))
        self.assertEqual(exported["parts"][0]["value"], rows)

    def test_invalid_legacy_import_rolls_back_new_dat(self):
        payload = DATExportModelBuilder().build(self.dat)
        payload["dat"]["reference"] = "DAT-INVALID-RESOURCE-IMPORT"
        self._resource_payload(payload)["parts"] = [
            {"key": "ressources_a_ajouter", "value": ["invalid row"]},
        ]

        with self.assertRaisesMessage(DATImportError, "ressources du fichier importé sont invalides"):
            DATImportService().import_from_payload(payload)

        self.assertFalse(DAT.objects.filter(reference="DAT-INVALID-RESOURCE-IMPORT").exists())
