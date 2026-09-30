# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import base64
import json
import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from . import exporters
from .exporters import DATExportModelBuilder


def _response(status, body):
    response = mock.MagicMock()
    response.status = status
    response.read.return_value = body
    response.__enter__.return_value = response
    return response


class DatExportModelBuilderTests(SimpleTestCase):
    def setUp(self) -> None:
        self.builder = DATExportModelBuilder()

    def test_builder_factory_uses_setting_and_wraps_import_error(self):
        class CustomBuilder:
            pass

        with override_settings(DAT_EXPORT_MODEL_BUILDER="custom.Builder"):
            with mock.patch.object(exporters, "import_string", return_value=CustomBuilder) as load:
                self.assertIsInstance(exporters.get_dat_export_model_builder(), CustomBuilder)
            load.assert_called_once_with("custom.Builder")

        with override_settings(DAT_EXPORT_MODEL_BUILDER="missing.Builder"):
            with mock.patch.object(exporters, "import_string", side_effect=ImportError("missing")):
                with self.assertRaises(ImproperlyConfigured):
                    exporters.get_dat_export_model_builder()

    def test_metadata_and_application_cover_optional_values(self):
        dat = SimpleNamespace(
            pk=12,
            reference="DAT-12",
            title="Architecture",
            description="Description",
            created_at="created",
            updated_at="updated",
        )
        with (
            mock.patch.object(exporters, "workflow_state", return_value="draft"),
            mock.patch.object(exporters, "workflow_state_label", return_value="Draft"),
            mock.patch.object(exporters, "isoformat_datetime", side_effect=lambda value: value),
            mock.patch.object(exporters, "localize_datetime", side_effect=lambda value: f"local:{value}"),
        ):
            result = self.builder.build_dat_metadata(dat)

        self.assertEqual(result["id"], "12")
        self.assertEqual(result["status_label"], "Draft")
        self.assertEqual(result["created_at_display"], "local:created")
        self.assertIsNone(self.builder.get_status_label(None))
        self.assertEqual(self.builder.get_status_label("unknown"), "unknown")
        self.assertIsNone(self.builder.build_application(SimpleNamespace(application=None)))
        self.assertEqual(
            self.builder.build_application(
                SimpleNamespace(
                    application=SimpleNamespace(pk=3, code="APP", name="App", description="About")
                )
            ),
            {"id": "3", "code": "APP", "name": "App", "description": "About"},
        )

    def test_participants_and_responsibles_cover_role_and_fallback_paths(self):
        user = SimpleNamespace(username="owner")
        role = SimpleNamespace(slug="architect", name="Architect")
        participant = SimpleNamespace(pk=4, user=user, role=role, created_at="assigned")
        dat = SimpleNamespace(participants=SimpleNamespace(all=lambda: [participant]))
        with (
            mock.patch.object(exporters, "serialize_user", side_effect=lambda value: value),
            mock.patch.object(exporters, "serialize_role", side_effect=lambda value: value),
            mock.patch.object(exporters, "format_user_display", side_effect=lambda value: value.username),
            mock.patch.object(exporters, "isoformat_datetime", side_effect=lambda value: value),
            mock.patch.object(exporters, "localize_datetime", side_effect=lambda value: value),
        ):
            rows = self.builder.build_participants(dat)
            self.builder._participant_role_map = self.builder._build_participant_map(dat)
            responsible = self.builder.build_responsibles(dat, [role])
            inherited = [{"user": {"name": "owner"}}]
            copied = self.builder.build_responsibles(dat, [], fallback_entries=inherited)
            copied[0]["user"]["name"] = "changed"
            fallback = self.builder.build_responsibles(dat, [], fallback_user=user)
            empty = self.builder.build_responsibles(dat, [])

        self.assertEqual(rows[0]["role_slug"], "architect")
        self.assertEqual(responsible[0]["participant_id"], "4")
        self.assertEqual(inherited[0]["user"]["name"], "owner")
        self.assertEqual(fallback[0]["participant_id"], None)
        self.assertEqual(empty, [])

    def test_build_sections_serializes_nested_sections(self):
        empty_roles = SimpleNamespace(all=lambda: [])
        sub_section = SimpleNamespace(
            pk=2,
            slug="overview",
            title="Overview",
            description="Summary",
            allowed_roles=empty_roles,
            order=1,
        )
        section = SimpleNamespace(
            pk=1,
            slug="architecture",
            title="Architecture",
            description="",
            allowed_roles=empty_roles,
            sub_sections=SimpleNamespace(all=lambda: [sub_section]),
            order=0,
        )
        self.builder._prefetch_sections = mock.Mock(return_value=[section])
        self.builder.build_responsibles = mock.Mock(side_effect=[[{"user": "owner"}], []])
        self.builder.build_parts = mock.Mock(return_value=[])

        result = self.builder.build_sections(SimpleNamespace(owner="owner"))

        self.assertEqual(result[0]["slug"], "architecture")
        self.assertEqual(result[0]["sub_sections"][0]["slug"], "overview")
        self.assertEqual(result[0]["sub_sections"][0]["responsibles"], [])

    def test_build_parts_filters_empty_values_and_builds_repeater_columns(self):
        def make_part(value, data_type="text", config=None):
            return SimpleNamespace(
                pk=1,
                key="part",
                label="Part",
                data_type=data_type,
                required=False,
                value=value,
                order=0,
                config=config,
                _get_current_entry=lambda: SimpleNamespace(updated_at=None),
                render_value=lambda current: current,
            )

        empty = make_part([])
        rows = [{"service_name": "API"}]
        repeater = make_part(
            rows,
            exporters.DATPartEntryType.REPEATER,
            {"columns": [{"key": "service_name"}]},
        )
        self.builder.include_empty_parts = False
        with (
            mock.patch.object(exporters, "isoformat_datetime", side_effect=lambda value: value),
            mock.patch.object(exporters, "localize_datetime", side_effect=lambda value: value),
        ):
            result = self.builder.build_parts(
                SimpleNamespace(parts=SimpleNamespace(all=lambda: [empty, repeater]))
            )

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["is_repeater"])
        self.assertEqual(result[0]["table_columns"], [{"key": "service_name", "label": "service_name"}])
        self.assertEqual(result[0]["display_value"], rows)

    def test_extract_repeater_columns_normalizes_and_infers_labels(self):
        configured = SimpleNamespace(
            config={
                "columns": [
                    None,
                    {"label": "Missing key"},
                    {"key": "name", "label": "Display name"},
                    {"key": "owner"},
                ]
            }
        )
        inferred = SimpleNamespace(config={"columns": "invalid"})

        self.assertEqual(
            self.builder._extract_repeater_columns(configured, []),
            [{"key": "name", "label": "Display name"}, {"key": "owner", "label": "owner"}],
        )
        self.assertEqual(
            self.builder._extract_repeater_columns(inferred, [{"service_name": "API"}]),
            [{"key": "service_name", "label": "Service Name"}],
        )
        self.assertEqual(self.builder._extract_repeater_columns(inferred, ["not a row"]), [])

    def test_drawio_previews_attach_only_matching_diagrams(self):
        diagram_id = uuid.uuid4()
        payload = {"title": "System"}
        part = SimpleNamespace(
            config={
                "columns": [
                    {"key": "diagram_id", "drawio": True, "label": "Diagram", "drawio_name_key": "name"}
                ]
            }
        )
        rows = [
            {"diagram_id": str(diagram_id), "name": "Main", "description": "Primary"},
            {"diagram_id": str(uuid.uuid4())},
            "not a row",
        ]
        self.builder._load_diagram_previews = mock.Mock(return_value={diagram_id: payload})

        result = self.builder._attach_drawio_previews(part, rows)

        self.assertIs(result, rows)
        self.assertIs(rows[0]["drawio_diagram"], payload)
        self.assertEqual(rows[0]["diagram_id_diagram"], payload)
        self.assertEqual(rows[0]["diagram_previews"][0]["title"], "Main")
        self.assertEqual(rows[0]["diagram_previews"][0]["description"], "Primary")
        self.assertNotIn("drawio_diagram", rows[1])

    def test_likec4_keys_and_paths_reject_invalid_references(self):
        part = SimpleNamespace(
            config={
                "columns": [
                    "ignore",
                    {"diagramToolKey": "engine", "diagramReferenceKey": "path"},
                ]
            }
        )
        self.assertEqual(self.builder._get_likec4_keys(part), ("engine", "path"))
        self.assertEqual(self.builder._normalize_likec4_path(" /workspace/views/main.c4 "), "workspace/views/main.c4")
        for raw in (None, "", "/../secret.c4", "./main.c4", "main.txt"):
            with self.subTest(raw=raw):
                self.assertEqual(self.builder._normalize_likec4_path(raw), "")

        rows = [
            {"engine": " LIKEC4 ", "path": "one.c4"},
            {"engine": "drawio", "path": "two.c4"},
            {"engine": "likec4", "path": "../unsafe.c4"},
            "not a row",
        ]
        self.assertEqual(self.builder._collect_likec4_references(rows, "engine", "path"), {"one.c4"})

    def test_likec4_previews_attach_to_matching_rows(self):
        part = SimpleNamespace(
            config={"columns": [{"diagram_tool_key": "engine", "diagram_reference_key": "path"}]}
        )
        payload = {"type": "likec4"}
        rows = [
            {"engine": "likec4", "path": "architecture.c4", "nom_schema": "Architecture", "description": "  Main  "},
            {"engine": "drawio", "path": "other.c4"},
        ]
        self.builder._load_likec4_previews = mock.Mock(return_value={"architecture.c4": payload})

        self.builder._attach_likec4_previews(part, rows)

        self.assertIs(rows[0]["likec4_diagram"], payload)
        preview = rows[0]["diagram_previews"][0]
        self.assertEqual(preview["title"], "Architecture")
        self.assertEqual(preview["description"], "Main")
        self.assertNotIn("likec4_diagram", rows[1])

    def test_diagram_preview_append_and_collection_handle_malformed_rows(self):
        row = {"diagram_previews": "invalid"}
        payload = {"title": "Diagram"}

        self.builder._append_diagram_preview(
            row,
            payload,
            title="  ",
            description=" Summary ",
        )
        self.builder._append_diagram_preview(row, {"id": 1}, column_label="LikeC4")
        self.builder._append_diagram_preview([], payload)
        self.builder._append_diagram_preview(row, {})

        self.assertEqual(row["diagram_previews"][0]["title"], "Diagram")
        self.assertEqual(row["diagram_previews"][0]["description"], "Summary")
        self.assertEqual(row["diagram_previews"][1]["title"], "LikeC4")
        self.assertEqual(len(self.builder._collect_diagram_previews([None, row, {"diagram_previews": [None, {}]}])), 2)
        self.assertEqual(self.builder._collect_diagram_previews(None), [])

    def test_seaweed_png_data_uri_reads_and_handles_storage_failures(self):
        storage = mock.Mock()
        handle = BytesIO(b"png")
        storage.open.return_value = handle

        self.assertEqual(
            self.builder._seaweed_png_data_uri(storage, "image.png"),
            "data:image/png;base64," + base64.b64encode(b"png").decode("ascii"),
        )
        self.assertTrue(handle.closed)
        self.assertIsNone(self.builder._seaweed_png_data_uri(storage, ""))

        storage.open.side_effect = FileNotFoundError
        self.assertIsNone(self.builder._seaweed_png_data_uri(storage, "missing.png"))
        storage.open.side_effect = OSError("offline")
        with self.assertLogs(exporters.logger, level="WARNING"):
            self.assertIsNone(self.builder._seaweed_png_data_uri(storage, "offline.png"))

    def test_likec4_preview_uses_views_and_fallback_paths(self):
        storage = mock.Mock()
        storage.url.side_effect = lambda path: f"storage:{path}"
        meta = SimpleNamespace(
            png_path="thumb.png",
            png_paths=["thumb.png", "repo/views/home.png", "ignored.svg", 3],
        )
        self.builder._seaweed_png_data_uri = mock.Mock(side_effect=["data:image/png;base64,AA==", None])
        self.builder.refresh_likec4_exports = True

        preview = self.builder._build_likec4_preview("repo/model.c4", meta, storage)

        self.assertEqual(preview["png_paths"], ["repo/views/home.png"])
        self.assertEqual(preview["images"], [{"src": "data:image/png;base64,AA==", "label": "home.png"}])
        self.assertEqual(preview["thumbnail_url"], "storage:thumb.png")

        with mock.patch.object(exporters, "likec4_png_path_for", return_value="fallback.png"):
            self.builder._seaweed_png_data_uri = mock.Mock(return_value=None)
            fallback = self.builder._build_likec4_preview("repo/model.c4", None, storage)
        self.assertEqual(fallback["png_paths"], ["fallback.png"])
        self.assertEqual(fallback["images"][0]["src"], "storage:fallback.png")

    def test_likec4_readiness_checks_unique_paths_and_missing_files(self):
        storage = mock.Mock()
        storage.exists.return_value = True
        meta = SimpleNamespace(png_path="thumb.png", png_paths=["thumb.png", "view.png", None, 4])

        self.assertTrue(self.builder._is_likec4_ready(storage, "model.c4", meta))
        self.assertEqual(storage.exists.call_args_list, [mock.call("thumb.png"), mock.call("view.png")])
        self.assertFalse(self.builder._is_likec4_ready(storage, "model.c4", None))
        self.assertFalse(self.builder._is_likec4_ready(storage, "model.c4", SimpleNamespace(png_path=None, png_paths=[])))
        storage.exists.side_effect = [False]
        self.assertFalse(self.builder._is_likec4_ready(storage, "model.c4", meta))
        storage.exists.side_effect = OSError("offline")
        with self.assertLogs(exporters.logger, level="WARNING"):
            self.assertFalse(self.builder._is_likec4_ready(storage, "model.c4", meta))

    @override_settings(LIKEC4_EXPORT_ENABLED=True, LIKEC4_EXPORT_URL="https://export.example", LIKEC4_EXPORT_TIMEOUT="12")
    def test_likec4_refresh_requests_each_reference(self):
        self.builder._request_likec4_export = mock.Mock()

        self.builder._refresh_likec4_exports(["one.c4", "", "two.c4"])

        self.assertEqual(self.builder._request_likec4_export.call_count, 2)
        self.builder._request_likec4_export.assert_has_calls(
            [
                mock.call("one.c4", export_url="https://export.example", timeout=12),
                mock.call("two.c4", export_url="https://export.example", timeout=12),
            ]
        )

    @override_settings(LIKEC4_EXPORT_ENABLED=True, LIKEC4_EXPORT_URL="https://export.example", LIKEC4_EXPORT_TIMEOUT=2)
    def test_wait_for_likec4_exports_exits_when_preview_is_ready(self):
        meta = SimpleNamespace(storage_path="model.c4", png_path="thumb.png", png_paths=[])
        query = mock.Mock()
        query.only.return_value = [meta]
        with (
            mock.patch.object(exporters.LikeC4Diagram.objects, "filter", return_value=query),
            mock.patch.object(exporters, "SeaweedFSStorage") as storage_factory,
            mock.patch.object(exporters.time, "monotonic", side_effect=[10, 11]),
        ):
            storage_factory.return_value.exists.return_value = True
            self.builder._wait_for_likec4_exports(["model.c4"])

        storage_factory.return_value.exists.assert_called_once_with("thumb.png")
        query.only.assert_called_once_with("storage_path", "png_path", "png_paths")

    def test_likec4_export_request_sends_json_and_handles_failures(self):
        with mock.patch.object(exporters, "urlopen", return_value=_response(201, b'{"ok":true}')) as open_url:
            self.builder.likec4_export_source = "unit_test"
            self.builder._request_likec4_export("model.c4", export_url="https://export.example", timeout=9)

        request = open_url.call_args.args[0]
        self.assertEqual(json.loads(request.data), {
            "storage_path": "model.c4",
            "source": "unit_test",
            "requested_at": mock.ANY,
        })
        open_url.assert_called_once_with(request, timeout=9)

        with mock.patch.object(exporters, "urlopen") as open_url:
            self.builder._request_likec4_export("model.c4", export_url="file:///unsafe", timeout=1)
            open_url.assert_not_called()

        responses = [
            _response(503, b"{}"),
            _response(200, b"not-json"),
            _response(200, b'{"ok":false,"error":"failed"}'),
        ]
        with mock.patch.object(exporters, "urlopen", side_effect=responses):
            with self.assertLogs(exporters.logger, level="INFO"):
                for _ in responses:
                    self.builder._request_likec4_export(
                        "model.c4", export_url="https://export.example", timeout=1
                    )
        with mock.patch.object(exporters, "urlopen", side_effect=TimeoutError("offline")):
            with self.assertLogs(exporters.logger, level="WARNING"):
                self.builder._request_likec4_export(
                    "model.c4", export_url="https://export.example", timeout=1
                )

    def test_drawio_columns_and_ids_ignore_malformed_values(self):
        diagram_id = uuid.uuid4()
        part = SimpleNamespace(
            config={
                "columns": [
                    None,
                    {"key": "ignored"},
                    {"key": "diagram", "drawio": True},
                    {"key": "other", "render": "drawio_diagram"},
                ]
            }
        )
        columns = self.builder._get_drawio_columns(part)
        self.assertEqual([column["key"] for column in columns], ["diagram", "other"])
        self.assertEqual(self.builder._get_drawio_columns(SimpleNamespace(config={"columns": "invalid"})), ())
        self.assertEqual(self.builder._normalize_diagram_id(diagram_id), diagram_id)
        self.assertEqual(self.builder._normalize_diagram_id(f" {diagram_id} "), diagram_id)
        for invalid in (None, "", "bad-uuid"):
            self.assertIsNone(self.builder._normalize_diagram_id(invalid))
        rows = [{"diagram": str(diagram_id)}, {"other": "bad-uuid"}, "not a row"]
        self.assertEqual(self.builder._collect_diagram_ids(rows, columns), {diagram_id})

    def test_drawio_images_use_stored_paths_then_thumbnail_fallback(self):
        storage = mock.Mock()
        storage.url.side_effect = lambda path: f"storage:{path}"
        diagram = SimpleNamespace(png_paths=["one.png", None, 4, "two.png"])
        self.builder._seaweed_png_data_uri = mock.Mock(side_effect=["data:image/png;base64,AA==", None])
        with mock.patch.object(exporters, "SeaweedFSStorage", return_value=storage):
            images = self.builder._build_drawio_images(diagram)
        self.assertEqual(
            images,
            [
                {"src": "data:image/png;base64,AA==", "label": "Page 1"},
                {"src": "storage:two.png", "label": "Page 2"},
            ],
        )

        diagram.png_paths = []
        diagram.thumbnail = SimpleNamespace(url="thumbnail-url")
        self.builder._thumbnail_data_uri = mock.Mock(return_value="data:image/png;base64,BB==")
        self.assertEqual(self.builder._build_drawio_images(diagram), [{"src": "data:image/png;base64,BB=="}])
        self.builder._thumbnail_data_uri.return_value = None
        self.assertEqual(self.builder._build_drawio_images(diagram), [{"src": "thumbnail-url"}])
        diagram.thumbnail = None
        self.assertEqual(self.builder._build_drawio_images(diagram), [])

    def test_thumbnail_data_uri_encodes_and_tolerates_unreadable_files(self):
        field = mock.Mock()
        field.name = "diagram.jpg"
        field.read.return_value = b"image"
        diagram = SimpleNamespace(pk=1, thumbnail=field)

        self.assertEqual(
            self.builder._thumbnail_data_uri(diagram),
            "data:image/jpeg;base64," + base64.b64encode(b"image").decode("ascii"),
        )
        field.close.assert_called_once()
        field.open.side_effect = FileNotFoundError
        self.assertIsNone(self.builder._thumbnail_data_uri(diagram))
        field.open.side_effect = OSError("offline")
        with self.assertLogs(exporters.logger, level="WARNING"):
            self.assertIsNone(self.builder._thumbnail_data_uri(diagram))
        self.assertIsNone(self.builder._thumbnail_data_uri(SimpleNamespace(thumbnail=None)))

    @override_settings(DRAWIO_EXPORT_URL="https://primary.example/export", DRAWIO_BASE_URL="https://backup.example")
    def test_drawio_thumbnail_retries_export_endpoint(self):
        diagram = SimpleNamespace(pk=7, read_xml=lambda: "")
        with mock.patch.object(
            exporters,
            "urlopen",
            side_effect=[TimeoutError("primary down"), _response(200, b" Zm9v ")],
        ) as open_url:
            result = self.builder._generate_drawio_thumbnail(diagram)

        self.assertEqual(result, "data:image/png;base64,Zm9v")
        self.assertEqual(open_url.call_count, 2)

    def test_drawio_export_candidates_validate_and_deduplicate_urls(self):
        with override_settings(
            DRAWIO_EXPORT_URL="https://drawio.example/export/",
            DRAWIO_BASE_URL="https://drawio.example/",
        ):
            self.assertEqual(self.builder._get_drawio_export_candidates(), ["https://drawio.example/export"])

        with override_settings(DRAWIO_EXPORT_URL="file:///unsafe", DRAWIO_BASE_URL="ftp://unsafe"):
            with self.assertLogs(exporters.logger, level="WARNING"):
                self.assertEqual(self.builder._get_drawio_export_candidates(), [])
