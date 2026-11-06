# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import uuid
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.urls import NoReverseMatch

from . import dat_extras as tags


class DATExtraTagsTests(SimpleTestCase):
    def test_mapping_and_repeater_filters_reject_invalid_shapes(self):
        self.assertEqual(tags.get_item({"name": "DAT"}, "name"), "DAT")
        self.assertIsNone(tags.get_item(["DAT"], 0))
        self.assertEqual(tags.dat_columns({"columns": [{"name": "label"}]}), [{"name": "label"}])
        self.assertEqual(tags.dat_columns({"columns": "invalid"}), [])
        self.assertEqual(tags.dat_columns(None), [])
        self.assertFalse(tags.dat_has_drawio_columns({"columns": [None, {"name": "label"}]}))
        self.assertTrue(tags.dat_has_drawio_columns({"columns": [{"render": "drawio_diagram"}]}))

    def test_repeater_width_and_pagination_follow_schema(self):
        drawio = SimpleNamespace(data_type="repeater", key="other", config={"columns": [{"drawio": True}]})
        special = SimpleNamespace(data_type="repeater", key="flux", config={})
        ordinary = SimpleNamespace(data_type="text", key="flux", config={})
        self.assertTrue(tags.dat_repeater_full_width(drawio, "architecture"))
        self.assertTrue(tags.dat_repeater_full_width(special, "architecture"))
        self.assertFalse(tags.dat_repeater_full_width(ordinary, "architecture"))
        self.assertFalse(tags.dat_repeater_full_width(SimpleNamespace(data_type="text", key="x", config={}), "validation"))

        self.assertIsNone(tags.dat_repeater_pagination(special, "validation", []))
        self.assertEqual(
            tags.dat_repeater_pagination(special, "architecture", list(range(21))),
            {"page_size": 10, "total": 21, "num_pages": 3},
        )
        self.assertEqual(
            tags.dat_repeater_pagination(SimpleNamespace(key="briques"), "architecture", []),
            {"page_size": 10, "total": 0, "num_pages": 1},
        )

    def test_section_icons_tooltips_and_status_use_safe_defaults(self):
        self.assertEqual(
            tags.section_part_icon("architecture", "schemas"),
            {"name": "stacks", "variant": tags.MATERIAL_SYMBOLS_VARIANT},
        )
        self.assertIsNone(tags.section_part_icon("", "schemas"))
        self.assertIsNone(tags.section_part_icon("unknown", "unknown"))
        with mock.patch.dict(
            tags.SECTION_BLUEPRINT_MAP,
            {"sample": {"parts": [{"slug": "overview", "tooltip": "Useful context"}] }},
        ):
            self.assertEqual(tags.section_part_tooltip("sample", "overview"), "Useful context")
            self.assertEqual(tags.section_part_tooltip("sample", "missing"), "")
        self.assertEqual(tags.section_part_tooltip("missing", "overview"), "")
        self.assertEqual(tags.section_part_tooltip("", "overview"), "")
        self.assertEqual(tags.section_status_icon("valide")["color"], "#2e7d32")
        self.assertEqual(tags.section_status_icon("unknown"), tags.SECTION_STATUS_ICON_DEFAULT)

    def test_diagram_identifier_accepts_supported_forms_and_rejects_empty_values(self):
        diagram_id = uuid.uuid4()
        self.assertEqual(tags._extract_diagram_identifier(None), (None, None))
        self.assertEqual(tags._extract_diagram_identifier({"pk": diagram_id, "title": "Provided"}), (diagram_id, "Provided"))
        self.assertEqual(tags._extract_diagram_identifier({"title": "Only title"}), (None, "Only title"))
        self.assertEqual(
            tags._extract_diagram_identifier(SimpleNamespace(pk=diagram_id, title="Object title")),
            (diagram_id, "Object title"),
        )
        self.assertEqual(tags._extract_diagram_identifier("raw-id"), ("raw-id", None))

    def test_diagram_links_validate_uuid_and_resolve_only_existing_diagrams(self):
        self.assertIsNone(tags.diagram_links(None))
        self.assertIsNone(tags.diagram_links("not-a-uuid"))

        diagram_id = uuid.uuid4()
        diagram = SimpleNamespace(pk=diagram_id, title="Stored title")
        with (
            mock.patch.object(tags.DrawIODiagram, "objects") as manager,
            mock.patch.object(tags, "reverse", side_effect=lambda name, args: f"/{name}/{args[0]}/"),
        ):
            manager.filter.return_value.only.return_value.first.return_value = diagram
            links = tags.diagram_links({"diagram_id": str(diagram_id), "title": " Override "})

        self.assertEqual(links["title"], "Override")
        self.assertEqual(links["pk"], diagram_id)
        self.assertEqual(links["detail_url"], f"/diagrams:detail/{diagram_id}/")
        manager.filter.assert_called_once_with(pk=diagram_id)

        with mock.patch.object(tags.DrawIODiagram, "objects") as manager:
            manager.filter.return_value.only.return_value.first.return_value = None
            self.assertIsNone(tags.diagram_links(diagram_id))

    def test_diagram_links_return_none_when_route_is_unavailable(self):
        diagram_id = uuid.uuid4()
        diagram = SimpleNamespace(pk=diagram_id, title="Stored title")
        with (
            mock.patch.object(tags.DrawIODiagram, "objects") as manager,
            mock.patch.object(tags, "reverse", side_effect=NoReverseMatch),
        ):
            manager.filter.return_value.only.return_value.first.return_value = diagram
            self.assertIsNone(tags.diagram_links(diagram_id))

    def test_likec4_urls_reject_unsafe_paths_and_url_build_failures(self):
        self.assertEqual(tags._normalize_likec4_path(None), "")
        self.assertEqual(tags._normalize_likec4_path("../secret.c4"), "")
        self.assertEqual(tags._normalize_likec4_path("diagrams/model.txt"), "")
        self.assertEqual(tags._normalize_likec4_path("/diagrams/model.C4"), "diagrams/model.C4")
        with mock.patch.object(tags, "reverse", return_value="/proxy/"):
            self.assertEqual(tags.likec4_png_url("/diagrams/model.c4"), "/proxy/?file=diagrams%2Fmodel.c4")
        with mock.patch.object(tags, "reverse", side_effect=RuntimeError("route unavailable")):
            self.assertEqual(tags.likec4_png_url("diagrams/model.c4"), "")
        with mock.patch.object(tags, "reverse") as reverse:
            self.assertEqual(tags.likec4_png_url("../model.c4"), "")
            reverse.assert_not_called()

    def test_unique_messages_deduplicate_by_level_and_text_in_order(self):
        first = SimpleNamespace(level=20, message="Saved")
        duplicate = SimpleNamespace(level=20, message="Saved")
        other_level = SimpleNamespace(level=30, message="Saved")
        self.assertEqual(tags.unique_messages(None), [])
        self.assertEqual(tags.unique_messages([first, duplicate, other_level]), [first, other_level])
