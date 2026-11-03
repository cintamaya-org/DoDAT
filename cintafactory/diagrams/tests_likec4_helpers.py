# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from contextlib import ExitStack
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from .forms import DiagramForm
from .likec4_exports import enqueue_likec4_export
from .likec4_scope import png_prefix, read_source, refresh_revision, scope_headers, source_revision, valid_png_path


DIAGRAM_ID = "60d2dd6b-1d5e-4b97-a8c2-e713fa178017"
SOURCE_PATH = f"diagrams/likec4/{DIAGRAM_ID}/source.c4"
SOURCE = b"model {}"
REVISION = source_revision(SOURCE)


class LikeC4ScopeTests(SimpleTestCase):
    def setUp(self):
        self.diagram = SimpleNamespace(pk=DIAGRAM_ID, storage_path=SOURCE_PATH, revision=REVISION, size=len(SOURCE))

    @override_settings(LIKEC4_MAX_SOURCE_BYTES=8)
    def test_source_limit_accepts_exact_boundary_and_closes_storage(self):
        stream = BytesIO(SOURCE)
        with mock.patch("diagrams.likec4_scope.SeaweedFSStorage") as storage:
            storage.return_value.open.return_value = stream
            self.assertEqual(read_source(self.diagram), SOURCE)
        storage.return_value.open.assert_called_once_with(SOURCE_PATH, "rb")
        self.assertTrue(stream.closed)

    @override_settings(LIKEC4_MAX_SOURCE_BYTES=8)
    def test_oversized_source_reads_only_one_byte_past_limit(self):
        stream = mock.MagicMock()
        stream.__enter__.return_value = stream
        stream.read.return_value = b"x" * 9
        with mock.patch("diagrams.likec4_scope.SeaweedFSStorage") as storage:
            storage.return_value.open.return_value = stream
            with self.assertRaises(ValidationError):
                read_source(self.diagram)
        stream.read.assert_called_once_with(9)
        stream.__exit__.assert_called_once()

    def test_refresh_reconciles_changed_storage_without_rewriting_unchanged_metadata(self):
        class Diagram:
            objects = mock.Mock()

        diagram = Diagram()
        diagram.pk, diagram.storage_path, diagram.revision, diagram.size = DIAGRAM_ID, SOURCE_PATH, "a" * 64, 0
        with mock.patch("diagrams.likec4_scope.read_source", return_value=SOURCE):
            self.assertEqual(refresh_revision(diagram), REVISION)
            self.assertEqual((diagram.revision, diagram.size), (REVISION, len(SOURCE)))
            Diagram.objects.filter.assert_called_once_with(pk=DIAGRAM_ID)
            Diagram.objects.filter.return_value.update.assert_called_once_with(revision=REVISION, size=len(SOURCE))
            Diagram.objects.reset_mock()
            self.assertEqual(refresh_revision(diagram), REVISION)
            Diagram.objects.filter.assert_not_called()

    @override_settings(LIKEC4_API_TOKEN="internal-test-token")
    def test_scope_headers_bind_explicit_revision_without_changing_diagram(self):
        replacement = "b" * 64
        self.assertEqual(scope_headers(self.diagram, "export", revision=replacement), {
            "X-LikeC4-Token": "internal-test-token", "X-LikeC4-Diagram-Id": DIAGRAM_ID,
            "X-LikeC4-Storage-Path": SOURCE_PATH, "X-LikeC4-Revision": replacement,
            "X-LikeC4-Operation": "export",
        })
        self.assertEqual(self.diagram.revision, REVISION)

    def test_scope_and_output_prefix_reject_missing_or_malformed_revisions(self):
        for revision in (None, "", "a" * 63, "A" * 64, "../source.c4"):
            with self.subTest(revision=revision):
                self.diagram.revision = revision
                with self.assertRaises(ValidationError):
                    scope_headers(self.diagram, "read")
                with self.assertRaises(ValidationError):
                    png_prefix(self.diagram)

    def test_png_paths_require_the_exact_diagram_revision_and_safe_suffix(self):
        prefix = png_prefix(self.diagram)
        self.assertTrue(valid_png_path(self.diagram, prefix + "project/index.png"))
        for suffix in ("", "index.svg", "../index.png", "./index.png", "a//index.png", "a\\index.png", "a%2findex.png", "a:index.png", "a\x00.png"):
            with self.subTest(suffix=suffix):
                self.assertFalse(valid_png_path(self.diagram, prefix + suffix))
        for path in (None, 1, prefix.replace(REVISION, "b" * 64) + "index.png", prefix.replace(DIAGRAM_ID, "other") + "index.png"):
            with self.subTest(path=path):
                self.assertFalse(valid_png_path(self.diagram, path))


@override_settings(LIKEC4_EXPORT_ENABLED=True, LIKEC4_EXPORT_URL="http://exporter.test/export", LIKEC4_API_TOKEN="internal-test-token", LIKEC4_EXPORT_TIMEOUT=7)
class LikeC4EnqueueTests(SimpleTestCase):
    def setUp(self):
        self.diagram = SimpleNamespace(pk=DIAGRAM_ID, storage_path=SOURCE_PATH, revision=REVISION)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.refresh = self.stack.enter_context(mock.patch("diagrams.likec4_scope.refresh_revision", return_value=REVISION))
        self.urlopen = self.stack.enter_context(mock.patch("diagrams.likec4_exports.urlopen"))
        self.metric = self.stack.enter_context(mock.patch("diagrams.likec4_exports.emit_baseline_metric"))
        self.query = self.stack.enter_context(mock.patch("diagrams.models.LikeC4Diagram.objects.filter"))

    def assert_outcome(self, outcome):
        self.assertEqual(self.metric.call_count, 1)
        self.assertEqual(self.metric.call_args.kwargs["dimensions"]["outcome"], outcome)
        self.assertFalse(self.metric.call_args.kwargs["success"])
        self.urlopen.assert_not_called()

    def test_invalid_input_and_disabled_or_missing_configuration_do_not_read_source(self):
        scenarios = [
            ({}, None, "invalid_storage_path"),
            ({}, "", "invalid_storage_path"),
            ({"LIKEC4_EXPORT_ENABLED": False}, SOURCE_PATH, "disabled"),
            ({"LIKEC4_EXPORT_URL": ""}, SOURCE_PATH, "missing_url"),
            ({"LIKEC4_EXPORT_URL": "file:///private/export"}, SOURCE_PATH, "invalid_url"),
        ]
        for settings, path, outcome in scenarios:
            with self.subTest(outcome=outcome), override_settings(**settings):
                self.metric.reset_mock()
                self.assertFalse(enqueue_likec4_export(path, diagram=self.diagram))
                self.assert_outcome(outcome)
                self.refresh.assert_not_called()
                self.query.assert_not_called()

    def test_unregistered_or_remapped_diagrams_never_reach_exporter(self):
        self.query.return_value.first.return_value = None
        self.assertFalse(enqueue_likec4_export(SOURCE_PATH))
        self.assert_outcome("unregistered_diagram")
        self.metric.reset_mock()
        self.diagram.storage_path = "diagrams/other/source.c4"
        self.assertFalse(enqueue_likec4_export(SOURCE_PATH, diagram=self.diagram))
        self.assert_outcome("unregistered_diagram")
        self.refresh.assert_not_called()

    def test_source_failure_and_stale_or_mismatched_identity_fail_closed(self):
        for error in (OSError("offline"), ValidationError("too large"), ValueError("bad source")):
            with self.subTest(error=type(error).__name__):
                self.metric.reset_mock()
                self.refresh.side_effect = error
                self.assertFalse(enqueue_likec4_export(SOURCE_PATH, diagram=self.diagram))
                self.assert_outcome("source_unavailable")
        self.refresh.side_effect = None
        for kwargs, outcome in (({"diagram_id": "other"}, "diagram_mismatch"), ({"revision": "b" * 64}, "stale_revision")):
            with self.subTest(outcome=outcome):
                self.metric.reset_mock()
                self.assertFalse(enqueue_likec4_export(SOURCE_PATH, diagram=self.diagram, **kwargs))
                self.assert_outcome(outcome)

    def test_lookup_binds_registered_identity_and_sends_current_revision(self):
        import json

        self.query.return_value.filter.return_value.first.return_value = self.diagram
        self.urlopen.return_value.__enter__.return_value.status = 202
        self.assertTrue(enqueue_likec4_export(SOURCE_PATH, source="pdf", diagram_id=DIAGRAM_ID))
        self.query.assert_called_once_with(storage_path=SOURCE_PATH)
        self.query.return_value.filter.assert_called_once_with(pk=DIAGRAM_ID)
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], 7)
        self.assertEqual(json.loads(request.data), {"diagram_id": DIAGRAM_ID, "storage_path": SOURCE_PATH, "revision": REVISION, "source": "pdf"})
        self.assertEqual(request.get_header("X-likec4-revision"), REVISION)
        self.assertEqual(request.get_header("X-likec4-token"), "internal-test-token")

    def test_non_success_status_and_transport_failures_are_best_effort(self):
        for status in (199, 300, 503):
            with self.subTest(status=status):
                self.metric.reset_mock()
                self.urlopen.return_value.__enter__.return_value.status = status
                self.assertFalse(enqueue_likec4_export(SOURCE_PATH, diagram=self.diagram))
                self.assertEqual(self.metric.call_args.kwargs["dimensions"]["outcome"], "http_status")
        self.urlopen.side_effect = TimeoutError("offline")
        self.assertFalse(enqueue_likec4_export(SOURCE_PATH, diagram=self.diagram))
        self.assertEqual(self.metric.call_args.kwargs["dimensions"]["outcome"], "exception")


class DiagramFormTests(SimpleTestCase):
    def test_title_is_normalized_before_model_validation(self):
        form = DiagramForm(data={"title": "  Service   API  "})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["title"], "Service API")

    def test_blank_and_unsafe_titles_are_rejected(self):
        for title in ("", "   ", "<script>", "Service\x08API", "[private]"):
            with self.subTest(title=title):
                form = DiagramForm(data={"title": title})
                self.assertFalse(form.is_valid())
                self.assertIn("title", form.errors)
