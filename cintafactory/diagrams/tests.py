# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import base64
import json
import shutil
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import views
from .models import DrawIODiagram
from .validation import sanitize_diagram_title


class DiagramTitleValidationTest(SimpleTestCase):
    def test_normalizes_whitespace(self):
        self.assertEqual(sanitize_diagram_title("  Mon   diagramme  "), "Mon diagramme")

    def test_rejects_html_like_characters(self):
        with self.assertRaises(ValidationError):
            sanitize_diagram_title("<schema>")

    def test_rejects_control_characters(self):
        with self.assertRaises(ValidationError):
            sanitize_diagram_title("Schema\x08Name")


class DiagramImportViewTest(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self._media_dir, ignore_errors=True))
        self._media_override = self.settings(MEDIA_ROOT=self._media_dir)
        self._media_override.enable()
        self.user = get_user_model().objects.create_user(username="importer", password="pwd")
        self.diagram = DrawIODiagram.objects.create(title="Test diagram", owner=self.user)
        self.url = reverse("diagrams:import_xml", args=[self.diagram.pk])

    def tearDown(self) -> None:
        self._media_override.disable()
        super().tearDown()

    def test_rejects_invalid_payload_type(self):
        self.client.force_login(self.user)
        payload = SimpleUploadedFile("diagram.svg", b"<svg></svg>", content_type="image/svg+xml")
        response = self.client.post(self.url, {"file": payload})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error"), "invalid_diagram")

    @patch("diagrams.views.enqueue_drawio_export_job")
    def test_imports_valid_drawio_file(self, mock_enqueue_job):
        mock_enqueue_job.return_value = type("Job", (), {"id": "33333333-3333-3333-3333-333333333333", "status": "queued"})()
        self.client.force_login(self.user)
        xml = "<mxGraphModel><root><mxCell id=\"0\" /></root></mxGraphModel>"
        payload = SimpleUploadedFile("schema.drawio", xml.encode("utf-8"), content_type="application/xml")
        response = self.client.post(self.url, {"file": payload})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("thumbnail_url", data.get("diagram", {}))
        self.assertIsNone(data["diagram"]["thumbnail_url"])
        self.assertIn("job", data)
        self.assertEqual(data["job"]["status"], "queued")
        mock_enqueue_job.assert_called_once()
        self.diagram.refresh_from_db()
        self.assertEqual(self.diagram.read_xml(), xml)


class DiagramViewerContextTest(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self._media_dir, ignore_errors=True))
        self._media_override = self.settings(MEDIA_ROOT=self._media_dir)
        self._media_override.enable()
        self.user = get_user_model().objects.create_user(username="viewer", password="pwd")
        self.diagram = DrawIODiagram.objects.create(title="Diag", owner=self.user)
        self.diagram.write_xml("<mxGraphModel/>")
        self.url = reverse("diagrams:viewer_context", args=[self.diagram.pk])

    def tearDown(self) -> None:
        self._media_override.disable()
        super().tearDown()

    @patch("diagrams.views.enqueue_drawio_export_job")
    def test_enqueues_missing_thumbnail_on_view(self, mock_enqueue_job):
        self.client.force_login(self.user)
        mock_enqueue_job.return_value = type("Job", (), {"id": "44444444-4444-4444-4444-444444444444", "status": "queued"})()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data["diagram"]["thumbnail_url"])
        self.assertIn("job", data)
        self.assertEqual(data["job"]["status"], "queued")
        mock_enqueue_job.assert_called_once_with(
            self.diagram.pk,
            xml_payload="<mxGraphModel/>",
            requested_by=self.user,
            source="viewer_context",
        )


class DiagramSaveXmlAsyncTest(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(username="save-xml-user", password="pwd")
        self.diagram = DrawIODiagram.objects.create(title="Save xml diagram", owner=self.user)
        self.url = reverse("diagrams:save_xml", args=[self.diagram.pk])

    @patch("diagrams.views.enqueue_drawio_export_job")
    def test_save_xml_enqueues_drawio_job(self, mock_enqueue_job):
        mock_enqueue_job.return_value = type("Job", (), {"id": "66666666-6666-6666-6666-666666666666", "status": "queued"})()
        self.client.force_login(self.user)
        xml = "<mxGraphModel><root><mxCell id=\"1\" /></root></mxGraphModel>"
        response = self.client.post(
            self.url,
            data=json.dumps({"xml": xml}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("job", payload)
        self.assertEqual(payload["job"]["status"], "queued")
        mock_enqueue_job.assert_called_once_with(
            self.diagram.pk,
            xml_payload=xml,
            requested_by=self.user,
            source="save_xml",
        )


class LikeC4MetadataAuthTest(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.url = reverse("diagrams:likec4_metadata")
        self.payload = {
            "path": "diagrams/123/likec4.c4",
            "size": 10,
            "content_type": "text/plain",
        }

    @patch("diagrams.views.emit_baseline_metric")
    def test_requires_auth_or_token(self, emit_metric):
        with self.settings(LIKEC4_METADATA_TOKEN="secret-token"):
            response = self.client.post(
                self.url,
                data=json.dumps(self.payload),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("error"), "unauthorized")
        emit_metric.assert_called_once()
        _, kwargs = emit_metric.call_args
        self.assertEqual(kwargs["dimensions"]["surface"], "likec4_metadata")
        self.assertEqual(kwargs["dimensions"]["outcome"], "unauthorized")
        self.assertFalse(kwargs["success"])

    @patch("diagrams.views.enqueue_likec4_export_job")
    def test_allows_valid_token(self, mock_enqueue):
        mock_enqueue.return_value = type("Job", (), {"id": "11111111-1111-1111-1111-111111111111", "status": "queued"})()
        payload = dict(self.payload, token="secret-token")
        with self.settings(LIKEC4_METADATA_TOKEN="secret-token"):
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("job", data)
        self.assertEqual(data["job"]["status"], "queued")
        mock_enqueue.assert_called_once()

    @patch("diagrams.views.enqueue_likec4_export_job")
    def test_allows_authenticated_user(self, mock_enqueue):
        mock_enqueue.return_value = type("Job", (), {"id": "22222222-2222-2222-2222-222222222222", "status": "queued"})()
        user = get_user_model().objects.create_user(username="meta-auth", password="pwd")
        self.client.force_login(user)
        with self.settings(LIKEC4_METADATA_TOKEN="secret-token"):
            response = self.client.post(
                self.url,
                data=json.dumps(self.payload),
                content_type="application/json",
                HTTP_ORIGIN="http://testserver",
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("job", data)
        self.assertEqual(data["job"]["status"], "queued")
        mock_enqueue.assert_called_once()

    @patch("diagrams.views.enqueue_likec4_export_job")
    def test_authenticated_user_requires_same_origin(self, mock_enqueue):
        mock_enqueue.return_value = type("Job", (), {"id": "33333333-3333-3333-3333-333333333333", "status": "queued"})()
        user = get_user_model().objects.create_user(username="meta-cross-site", password="pwd")
        self.client.force_login(user)
        with self.settings(LIKEC4_METADATA_TOKEN="secret-token"):
            response = self.client.post(
                self.url,
                data=json.dumps(self.payload),
                content_type="application/json",
                HTTP_ORIGIN="http://evil.example",
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json().get("error"), "csrf_failed")
        mock_enqueue.assert_not_called()


class LikeC4ExportBaselineTests(SimpleTestCase):
    @patch("diagrams.likec4_exports.emit_baseline_metric")
    def test_enqueue_emits_success_metric(self, emit_metric):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with self.settings(
            LIKEC4_EXPORT_ENABLED=True,
            LIKEC4_EXPORT_URL="https://example.local/export",
            LIKEC4_EXPORT_TIMEOUT=1,
            LIKEC4_API_TOKEN="",
        ):
            with patch("diagrams.likec4_exports.urlopen", return_value=Response()):
                from .likec4_exports import enqueue_likec4_export

                result = enqueue_likec4_export("diagrams/1/likec4.c4", source="test")

        self.assertTrue(result)
        emit_metric.assert_called_once()
        _, kwargs = emit_metric.call_args
        self.assertEqual(kwargs["dimensions"]["outcome"], "ok")

    @patch("diagrams.likec4_exports.emit_baseline_metric")
    def test_enqueue_emits_failure_metric(self, emit_metric):
        with self.settings(
            LIKEC4_EXPORT_ENABLED=True,
            LIKEC4_EXPORT_URL="https://example.local/export",
            LIKEC4_EXPORT_TIMEOUT=1,
            LIKEC4_API_TOKEN="",
        ):
            with patch(
                "diagrams.likec4_exports.urlopen",
                side_effect=HTTPError(
                    url="https://example.local/export",
                    code=503,
                    msg="Service Unavailable",
                    hdrs=None,
                    fp=None,
                ),
            ):
                from .likec4_exports import enqueue_likec4_export

                result = enqueue_likec4_export("diagrams/1/likec4.c4", source="test")

        self.assertFalse(result)
        emit_metric.assert_called_once()
        _, kwargs = emit_metric.call_args
        self.assertEqual(kwargs["dimensions"]["outcome"], "http_error")


class ProxySecurityTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(username="proxy-user", password="pwd")

    def test_drawio_proxy_blocks_absolute_url_path(self):
        with self.settings(DRAWIO_BASE_URL="https://drawio.example.test"):
            with patch("diagrams.views.urlopen") as mock_urlopen:
                response = self.client.get("/diagrams/drawio/proxy/http://evil.example/")
        self.assertEqual(response.status_code, 404)
        mock_urlopen.assert_not_called()

    def test_drawio_proxy_blocks_non_allowlisted_upstream_host(self):
        with self.settings(
            DRAWIO_BASE_URL="https://drawio.example.test",
            DRAWIO_PROXY_ALLOWED_UPSTREAM_HOSTS="allowed.internal",
        ):
            with patch("diagrams.views.urlopen") as mock_urlopen:
                response = self.client.get(reverse("diagrams:drawio_proxy_root"))
        self.assertEqual(response.status_code, 404)
        mock_urlopen.assert_not_called()

    def test_likec4_proxy_blocks_path_traversal(self):
        self.client.force_login(self.user)
        with self.settings(LIKEC4_EDITOR_URL="https://likec4.example.test"):
            with patch("diagrams.views.urlopen") as mock_urlopen:
                response = self.client.get("/diagrams/likec4/editor/..%2fsecrets")
        self.assertEqual(response.status_code, 404)
        mock_urlopen.assert_not_called()

    def test_likec4_proxy_blocks_oversized_post_payload(self):
        self.client.force_login(self.user)
        with self.settings(
            LIKEC4_EDITOR_URL="https://likec4.example.test",
            LIKEC4_PROXY_MAX_BODY_BYTES=4,
        ):
            with patch("diagrams.views.urlopen") as mock_urlopen:
                response = self.client.post(
                    reverse("diagrams:likec4_proxy_root"),
                    data=b"abcdef",
                    content_type="application/json",
                    HTTP_ORIGIN="http://testserver",
                )
        self.assertEqual(response.status_code, 413)
        mock_urlopen.assert_not_called()

    def test_likec4_proxy_uses_editor_csp(self):
        class Response:
            status = 200
            headers = {"Content-Type": "text/html; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"<html></html>"

        self.client.force_login(self.user)
        with self.settings(LIKEC4_EDITOR_URL="https://likec4.example.test"):
            with patch("diagrams.views.urlopen", return_value=Response()):
                response = self.client.get(reverse("diagrams:likec4_proxy_root"))

        self.assertEqual(response.status_code, 200)
        csp = response["Content-Security-Policy"]
        self.assertIn("'unsafe-inline'", csp)
        self.assertIn("'unsafe-eval'", csp)
        self.assertIn("https://cdn.jsdelivr.net", csp)

    def test_likec4_proxy_post_adds_api_token(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"ok": true}'

        self.client.force_login(self.user)
        with self.settings(LIKEC4_EDITOR_URL="https://likec4.example.test", LIKEC4_API_TOKEN="proxy-token"):
            with patch("diagrams.views.urlopen", return_value=Response()) as mock_urlopen:
                response = self.client.post(
                    reverse("diagrams:likec4_proxy", args=["save"]),
                    data=b'{"content": ""}',
                    content_type="application/json",
                    HTTP_ORIGIN="http://testserver",
                )

        self.assertEqual(response.status_code, 200)
        forwarded_request = mock_urlopen.call_args.args[0]
        self.assertEqual(forwarded_request.get_header("X-likec4-token"), "proxy-token")

    def test_likec4_proxy_blocks_cross_origin_post(self):
        self.client.force_login(self.user)
        with self.settings(LIKEC4_EDITOR_URL="https://likec4.example.test"):
            with patch("diagrams.views.urlopen") as mock_urlopen:
                response = self.client.post(
                    reverse("diagrams:likec4_proxy", args=["save"]),
                    data=b'{"content": ""}',
                    content_type="application/json",
                    HTTP_ORIGIN="http://evil.example",
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json().get("error"), "csrf_failed")
        mock_urlopen.assert_not_called()


@override_settings(ALLOWED_HOSTS=["app.example.test", "testserver"])
class DiagramCoverageHelperTests(SimpleTestCase):
    def test_asset_and_proxy_path_helpers_reject_traversal_and_external_paths(self):
        self.assertEqual(views._normalize_asset_path(" /views/main.png "), "views/main.png")
        for path in (None, "", "/", "../secret", "views/../secret"):
            with self.subTest(path=path):
                self.assertEqual(views._normalize_asset_path(path), "")

        self.assertTrue(views._proxy_path_is_allowed("assets/drawio.js"))
        self.assertTrue(views._proxy_path_is_allowed(""))
        for path in ("https://evil.test/a", "/absolute", "../secret", "%2e%2e/secret", "a\\b", "a\x00b"):
            with self.subTest(path=path):
                self.assertFalse(views._proxy_path_is_allowed(path))

        self.assertEqual(views._split_csv(None), [])
        self.assertEqual(views._split_csv(" a, ,b "), ["a", "b"])
        self.assertTrue(views._proxy_path_matches_prefixes("editor/app.js", []))
        self.assertTrue(views._proxy_path_matches_prefixes("editor/app.js", ["editor", "*"]))
        self.assertFalse(views._proxy_path_matches_prefixes("other/app.js", ["editor"]))

    @override_settings(DRAWIO_PROXY_ALLOWED_UPSTREAM_HOSTS="editor.example.test,other.example.test")
    def test_upstream_allowlist_rejects_unknown_host_and_credentials(self):
        self.assertTrue(
            views._proxy_upstream_is_allowlisted(
                "https://editor.example.test/export", "DRAWIO_PROXY_ALLOWED_UPSTREAM_HOSTS"
            )
        )
        self.assertFalse(
            views._proxy_upstream_is_allowlisted(
                "https://unknown.example.test/export", "DRAWIO_PROXY_ALLOWED_UPSTREAM_HOSTS"
            )
        )
        self.assertFalse(
            views._proxy_upstream_is_allowlisted(
                "https://user:secret@editor.example.test/export", "DRAWIO_PROXY_ALLOWED_UPSTREAM_HOSTS"
            )
        )
        self.assertFalse(
            views._proxy_upstream_is_allowlisted("/relative/path", "DRAWIO_PROXY_ALLOWED_UPSTREAM_HOSTS")
        )

    def test_static_library_discovery_filters_non_xml_and_combines_configured_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            library_dir = Path(temporary) / "static" / "diagrams"
            library_dir.mkdir(parents=True)
            (library_dir / "Badges.xml").write_text("<mxlibrary />", encoding="utf-8")
            (library_dir / "ignored.txt").write_text("ignore", encoding="utf-8")
            (library_dir / "Network.drawio:Zone.Identifier").write_text("ignore", encoding="utf-8")
            with override_settings(BASE_DIR=temporary, DRAWIO_PUBLIC_URL="https://drawio.example.test/editor"):
                discovered = views._discover_static_library_urls()
                configured = ["https://cdn.example.test/custom.xml", discovered[0]]
                with (
                    override_settings(DRAWIO_CLIBS=configured),
                    mock.patch.object(views, "_discover_static_library_urls", return_value=discovered),
                ):
                    combined = views._collect_library_urls()

        self.assertEqual(len(discovered), 1)
        self.assertTrue(discovered[0].endswith("/static/diagrams/Badges.xml"))
        self.assertEqual(combined, configured)

    def test_embed_url_and_https_proxy_resolution_preserve_browser_origin(self):
        request = RequestFactory().get("/diagrams/", HTTP_HOST="app.example.test", secure=True)
        with override_settings(
            DRAWIO_PUBLIC_URL="http://editor.example.test/embed?mode=edit#canvas",
            DRAWIO_LIBS="custom-libraries",
        ):
            public_url = views._resolve_public_drawio_url(request)
            embed_url = views._build_embed_url(
                ["https://app.example.test/static/one.xml"],
                "/local/editor/?mode=edit#canvas",
                request,
            )

        self.assertIn(reverse("diagrams:drawio_proxy_root"), public_url)
        parsed = urlsplit(embed_url)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "app.example.test")
        self.assertEqual(parsed.fragment, "canvas")
        self.assertEqual(params["mode"], ["edit"])
        self.assertEqual(params["libs"], ["custom-libraries"])
        self.assertEqual(params["embed"], ["1"])
        self.assertIn("one.xml", params["clibs"][0])
        self.assertEqual(views._origin_from("https://editor.example.test/path"), "https://editor.example.test")

    def test_diagram_asset_url_requires_storage_path_under_diagram_prefix(self):
        request = RequestFactory().get("/", HTTP_HOST="app.example.test")
        diagram_id = uuid.uuid4()
        diagram = SimpleNamespace(pk=diagram_id)
        url = views._diagram_asset_url(request, diagram, f"diagrams/{diagram_id}/views/main.png")
        self.assertTrue(url.startswith("http://app.example.test"))
        self.assertIn(reverse("diagrams:diagram_asset", args=[diagram_id, "views/main.png"]), url)
        self.assertIsNone(views._diagram_asset_url(request, diagram, None))
        self.assertIsNone(views._diagram_asset_url(request, diagram, f"diagrams/{uuid.uuid4()}/main.png"))
        self.assertIsNone(views._diagram_asset_url(request, diagram, f"diagrams/{diagram_id}/../secret"))

    def test_thumbnail_lookup_handles_missing_and_failed_storage(self):
        self.assertIsNone(views._current_thumbnail_url(SimpleNamespace(pk=1, thumbnail=None)))
        field = SimpleNamespace(
            name="thumb.png",
            storage=SimpleNamespace(exists=mock.Mock(return_value=False)),
            url="/media/thumb.png",
        )
        self.assertIsNone(views._current_thumbnail_url(SimpleNamespace(pk=2, thumbnail=field)))
        field.storage.exists.return_value = True
        self.assertEqual(views._current_thumbnail_url(SimpleNamespace(pk=2, thumbnail=field)), "/media/thumb.png")
        field.storage.exists.side_effect = OSError("storage offline")
        self.assertIsNone(views._current_thumbnail_url(SimpleNamespace(pk=2, thumbnail=field)))

    def test_thumbnail_save_rejects_invalid_data_and_skips_identical_content(self):
        self.assertFalse(views._save_thumbnail_from_data_uri(SimpleNamespace(pk=1), "not-an-image"))
        raw = b"png-bytes"
        encoded = base64.b64encode(raw).decode("ascii")
        field = SimpleNamespace(
            name="thumb.png",
            open=mock.Mock(),
            read=mock.Mock(return_value=raw),
            close=mock.Mock(),
            save=mock.Mock(),
        )
        diagram = SimpleNamespace(pk=3, thumbnail=field, save=mock.Mock())

        self.assertTrue(views._save_thumbnail_from_data_uri(diagram, f"data:image/png;base64,{encoded}"))

        field.open.assert_called_once_with("rb")
        field.close.assert_called_once_with()
        field.save.assert_not_called()
        diagram.save.assert_called_once_with(update_fields=["updated_at"])

    def test_thumbnail_save_writes_new_png_and_updates_metadata(self):
        raw = b"new-png"
        encoded = base64.b64encode(raw).decode("ascii")
        field = SimpleNamespace(name="", save=mock.Mock())
        diagram = SimpleNamespace(pk=4, thumbnail=field, save=mock.Mock())

        self.assertTrue(views._save_thumbnail_from_data_uri(diagram, f"data:image/png;base64,{encoded}"))

        field.save.assert_called_once()
        self.assertEqual(field.save.call_args.args[0], "thumb.png")
        self.assertEqual(diagram.thumbnail_size, len(raw))
        self.assertEqual(diagram.thumbnail_content_type, "image/png")
        self.assertIn("thumbnail_size", diagram.save.call_args.kwargs["update_fields"])

    @override_settings(DRAWIO_EXPORT_URL="javascript:bad", DRAWIO_BASE_URL="https://drawio.example.test/")
    def test_drawio_export_falls_back_to_safe_base_and_decodes_png(self):
        self.assertEqual(
            views._drawio_export_candidates(),
            ["https://drawio.example.test/export"],
        )
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = base64.b64encode(b"png")
        with (
            mock.patch.object(views, "_drawio_export_candidates", return_value=["http://first", "http://second"]),
            mock.patch.object(views, "urlopen", side_effect=[OSError("offline"), response]),
        ):
            self.assertEqual(views._export_drawio_png_bytes("<mxGraphModel />"), b"png")

    def test_drawio_view_export_saves_pages_and_best_effort_deletes_stale_images(self):
        diagram = SimpleNamespace(
            pk=12,
            png_paths=["diagrams/12/views/old.png"],
            thumbnail=mock.Mock(),
            save=mock.Mock(),
        )
        storage = mock.Mock()
        pages = [
            {"index": 0, "name": "Main view", "xml": "<mxGraphModel />"},
            {"index": 1, "name": "Empty", "xml": ""},
        ]
        with (
            override_settings(DRAWIO_EXPORT_DELETE_OLD=True),
            mock.patch.object(views, "extract_drawio_pages", return_value=pages),
            mock.patch.object(views, "SeaweedFSStorage", return_value=storage),
            mock.patch.object(views, "_export_drawio_png_bytes", return_value=b"png"),
        ):
            result = views._export_drawio_views(diagram, "<mxfile />")

        self.assertTrue(result)
        self.assertEqual(diagram.png_paths, ["diagrams/12/views/page-01-main-view.png"])
        self.assertEqual(storage.save.call_args.args[0], "diagrams/12/views/page-01-main-view.png")
        storage.delete.assert_called_once_with("diagrams/12/views/old.png")
        self.assertIn("png_paths", diagram.save.call_args.kwargs["update_fields"])

    def test_likec4_metadata_rejects_invalid_auth_payload_and_paths(self):
        factory = RequestFactory()
        anonymous = SimpleNamespace(is_authenticated=False)
        request = factory.post("/metadata", data="{invalid", content_type="application/json")
        request.user = anonymous
        self.assertEqual(views.likec4_metadata(request).status_code, 400)

        request = factory.post("/metadata", data="[]", content_type="application/json")
        request.user = anonymous
        self.assertEqual(views.likec4_metadata(request).status_code, 400)

        request = factory.post("/metadata", data=json.dumps({"path": "models/a.c4"}), content_type="application/json")
        request.user = anonymous
        with override_settings(LIKEC4_METADATA_TOKEN="secret"), mock.patch.object(views, "emit_baseline_metric") as metric:
            self.assertEqual(views.likec4_metadata(request).status_code, 403)
        metric.assert_called_once()

        request = factory.post(
            "/metadata",
            data=json.dumps({"path": "models/a.c4"}),
            content_type="application/json",
            HTTP_X_LIKEC4_TOKEN="wrong",
        )
        request.user = SimpleNamespace(is_authenticated=True)
        request.META["HTTP_ORIGIN"] = "http://evil.example"
        with override_settings(LIKEC4_METADATA_TOKEN="secret"):
            self.assertEqual(views.likec4_metadata(request).status_code, 403)

        request = factory.post(
            "/metadata",
            data=json.dumps({"path": "../outside.c4"}),
            content_type="application/json",
            HTTP_X_LIKEC4_TOKEN="secret",
        )
        request.user = anonymous
        with override_settings(LIKEC4_METADATA_TOKEN="secret"):
            self.assertEqual(views.likec4_metadata(request).status_code, 400)

        request = factory.post(
            "/metadata",
            data=json.dumps({"path": "models/a.c4", "png_path": "../outside.png"}),
            content_type="application/json",
            HTTP_X_LIKEC4_TOKEN="secret",
        )
        request.user = anonymous
        with override_settings(LIKEC4_METADATA_TOKEN="secret"):
            self.assertEqual(views.likec4_metadata(request).status_code, 400)

    def test_likec4_metadata_saves_normalized_paths_and_best_effort_cleans_old_assets(self):
        request = RequestFactory().post(
            "/metadata",
            data=json.dumps(
                {
                    "path": " /models/context.c4 ",
                    "size": -7,
                    "content_type": "text/plain",
                    "png_path": "/models/main.png",
                    "png_size": "invalid",
                    "png_paths": ["models/main.png", "models/page.png", "../bad.png", None, "models/page.png"],
                }
            ),
            content_type="application/json",
            HTTP_X_LIKEC4_TOKEN="secret",
        )
        request.user = SimpleNamespace(is_authenticated=False)
        old = SimpleNamespace(png_path="old.png", png_paths=["old.png", "stale.png"])
        with (
            override_settings(LIKEC4_METADATA_TOKEN="secret", LIKEC4_EXPORT_DELETE_OLD=True),
            mock.patch.object(views.LikeC4Diagram.objects, "filter") as filter_diagrams,
            mock.patch.object(views.LikeC4Diagram.objects, "update_or_create") as update_diagram,
            mock.patch.object(views, "SeaweedFSStorage") as storage_factory,
        ):
            filter_diagrams.return_value.only.return_value.first.return_value = old
            storage_factory.return_value.delete.side_effect = OSError("storage unavailable")
            response = views.likec4_metadata(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"ok": True})
        update_diagram.assert_called_once()
        self.assertEqual(update_diagram.call_args.kwargs["storage_path"], "models/context.c4")
        defaults = update_diagram.call_args.kwargs["defaults"]
        self.assertEqual(defaults["size"], 0)
        self.assertEqual(defaults["png_path"], "models/main.png")
        self.assertEqual(defaults["png_size"], 0)
        self.assertEqual(defaults["png_paths"], ["models/main.png", "models/page.png"])
        self.assertEqual(storage_factory.return_value.delete.call_args_list, [
            mock.call("old.png"),
            mock.call("stale.png"),
        ])

    def test_likec4_metadata_queues_export_when_thumbnail_is_missing(self):
        request = RequestFactory().post(
            "/metadata",
            data=json.dumps({"path": "models/context.c4", "size": "bad"}),
            content_type="application/json",
            HTTP_X_LIKEC4_TOKEN="secret",
        )
        request.user = SimpleNamespace(is_authenticated=False)
        job = SimpleNamespace(id=uuid.uuid4(), status="queued")
        with (
            override_settings(LIKEC4_METADATA_TOKEN="secret"),
            mock.patch.object(views.LikeC4Diagram.objects, "filter") as filter_diagrams,
            mock.patch.object(views.LikeC4Diagram.objects, "update_or_create"),
            mock.patch.object(views, "enqueue_likec4_export_job", return_value=job) as enqueue,
        ):
            filter_diagrams.return_value.only.return_value.first.return_value = None
            response = views.likec4_metadata(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["job"]["job_id"], str(job.id))
        enqueue.assert_called_once_with("models/context.c4", requested_by=None, source="metadata")

    def test_likec4_import_validates_files_saves_and_reports_storage_errors(self):
        factory = RequestFactory()
        user = SimpleNamespace(is_authenticated=True)
        no_file = factory.post("/import")
        no_file.user = user
        self.assertEqual(views.likec4_import(no_file).status_code, 400)
        invalid_file = factory.post(
            "/import",
            {"file": SimpleUploadedFile("diagram.txt", b"data")},
        )
        invalid_file.user = user
        self.assertEqual(views.likec4_import(invalid_file).status_code, 400)

        upload = SimpleUploadedFile("diagram.c4", b"spec", content_type="text/plain")
        request = factory.post("/import", {"file": upload, "path": "/models/context.c4"})
        request.user = user
        job = SimpleNamespace(id=uuid.uuid4(), status="queued")
        with (
            mock.patch.object(views, "SeaweedFSStorage") as storage_factory,
            mock.patch.object(views.LikeC4Diagram.objects, "update_or_create") as update_diagram,
            mock.patch.object(views, "enqueue_likec4_export_job", return_value=job) as enqueue,
        ):
            response = views.likec4_import(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["path"], "models/context.c4")
        storage_factory.return_value.save.assert_called_once()
        saved_path, saved_file = storage_factory.return_value.save.call_args.args
        self.assertEqual(saved_path, "models/context.c4")
        self.assertEqual(saved_file.name, "diagram.c4")
        self.assertEqual(saved_file.read(), b"spec")
        update_diagram.assert_called_once()
        enqueue.assert_called_once_with("models/context.c4", requested_by=user, source="import")

        upload = SimpleUploadedFile("diagram.c4", b"spec", content_type="text/plain")
        request = factory.post("/import", {"file": upload})
        request.user = user
        with mock.patch.object(views, "SeaweedFSStorage") as storage_factory:
            storage_factory.return_value.save.side_effect = HTTPError("http://storage.invalid", 503, "down", {}, None)
            self.assertEqual(views.likec4_import(request).status_code, 502)

    def test_likec4_file_endpoints_serve_metadata_paths_and_not_found_errors(self):
        user = SimpleNamespace(is_authenticated=True)
        png_request = RequestFactory().get("/png", {"file": "models/context.c4"})
        png_request.user = user
        metadata = SimpleNamespace(png_path="models/rendered.png", png_content_type="image/webp")
        with (
            mock.patch.object(views.LikeC4Diagram.objects, "filter") as find_diagram,
            mock.patch.object(views, "SeaweedFSStorage") as storage_factory,
        ):
            find_diagram.return_value.only.return_value.first.return_value = metadata
            storage_factory.return_value.exists.return_value = True
            storage_factory.return_value.open.return_value = BytesIO(b"png")
            png_response = views.likec4_png(png_request)
        self.assertEqual(png_response.status_code, 200)
        self.assertEqual(png_response["Content-Type"], "image/webp")
        self.assertEqual(png_response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(png_response.streaming_content), b"png")

        invalid_png = RequestFactory().get("/png", {"file": "../outside.c4"})
        invalid_png.user = user
        self.assertEqual(views.likec4_png(invalid_png).status_code, 400)

        views_request = RequestFactory().get("/views", {"file": "models/context.c4"})
        views_request.user = user
        metadata = SimpleNamespace(
            png_path="models/main.png",
            png_paths=["models/main.png", "models/page.png", "../bad.png", "models/page.png"],
        )
        with mock.patch.object(views.LikeC4Diagram.objects, "filter") as find_diagram:
            find_diagram.return_value.only.return_value.first.return_value = metadata
            views_response = views.likec4_views(views_request)
        views_payload = json.loads(views_response.content)
        self.assertEqual(len(views_payload["paths"]), 1)
        self.assertEqual(
            parse_qs(urlsplit(views_payload["paths"][0]).query)["file"],
            ["models/page.png"],
        )

        export_request = RequestFactory().get("/export", {"file": "models/context.c4"})
        export_request.user = user
        metadata = SimpleNamespace(content_type="text/plain")
        with (
            mock.patch.object(views.LikeC4Diagram.objects, "filter") as find_diagram,
            mock.patch.object(views, "SeaweedFSStorage") as storage_factory,
        ):
            find_diagram.return_value.only.return_value.first.return_value = metadata
            storage_factory.return_value.open.return_value = BytesIO(b"model")
            export_response = views.likec4_export(export_request)
        self.assertEqual(export_response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(export_response["Content-Disposition"], 'attachment; filename="context.c4"')
        self.assertEqual(b"".join(export_response.streaming_content), b"model")

        with (
            mock.patch.object(views.LikeC4Diagram.objects, "filter") as find_diagram,
            mock.patch.object(views, "SeaweedFSStorage") as storage_factory,
        ):
            find_diagram.return_value.only.return_value.first.return_value = None
            storage_factory.return_value.exists.side_effect = OSError("offline")
            with self.assertRaises(Http404):
                views.likec4_png(png_request)

            storage_factory.return_value.exists.side_effect = None
            storage_factory.return_value.exists.return_value = True
            storage_factory.return_value.open.side_effect = FileNotFoundError
            with self.assertRaises(Http404):
                views.likec4_png(png_request)

    def test_same_origin_check_supports_origin_and_referer_fallback(self):
        factory = RequestFactory()
        same_origin = factory.post("/save", HTTP_HOST="app.example.test", HTTP_ORIGIN="http://app.example.test")
        cross_origin = factory.post("/save", HTTP_HOST="app.example.test", HTTP_ORIGIN="http://evil.example")
        referer = factory.post("/save", HTTP_HOST="app.example.test", HTTP_REFERER="http://app.example.test/page")
        missing = factory.post("/save", HTTP_HOST="app.example.test")

        self.assertTrue(views._request_same_origin(same_origin))
        self.assertFalse(views._request_same_origin(cross_origin))
        self.assertTrue(views._request_same_origin(referer))
        self.assertFalse(views._request_same_origin(missing))
        self.assertIsNone(views._reject_unsafe_session_request(factory.get("/save"), "test"))
        rejected = views._reject_unsafe_session_request(cross_origin, "test")
        self.assertEqual(rejected.status_code, 403)
