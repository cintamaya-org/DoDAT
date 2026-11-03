# SPDX-License-Identifier: AGPL-3.0-only

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from io import BytesIO
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from .storage.seaweedfs_storage import SeaweedFSStorage, build_filer_jwt


class FilerJWTTests(SimpleTestCase):
    @mock.patch("cintafactory.storage.seaweedfs_storage.time.time", return_value=1700000000)
    def test_token_binds_signature_method_path_and_minimum_expiry(self, clock):
        key = "test-filer-signing-key"
        token = build_filer_jwt(key, "/root/private.txt", "get", 0)
        header, payload, signature = token.split(".")

        self.assertEqual(json.loads(base64.urlsafe_b64decode(header + "==")), {"alg": "HS256", "typ": "JWT"})
        self.assertEqual(json.loads(base64.urlsafe_b64decode(payload + "==")), {
            "allowed_prefixes": ["/root/private.txt"],
            "allowed_methods": ["GET"],
            "iat": 1700000000,
            "exp": 1700000001,
        })
        expected_signature = hmac.new(key.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        self.assertEqual(base64.urlsafe_b64decode(signature + "=="), expected_signature)


@override_settings(
    SEAWEEDFS_FILER_URL="https://files.example.test/",
    SEAWEEDFS_PUBLIC_URL="https://cdn.example.test/",
    SEAWEEDFS_BASE_DIR="/root/",
    SEAWEEDFS_TIMEOUT=5,
    SEAWEEDFS_JWT_READ_KEY="test-read-key",
    SEAWEEDFS_JWT_WRITE_KEY="test-write-key",
    SEAWEEDFS_JWT_TTL_SECONDS=60,
    SEAWEEDFS_PUBLIC_JWT_TTL_SECONDS=300,
)
class SeaweedFSStorageRequestTests(SimpleTestCase):
    def setUp(self):
        self.storage = SeaweedFSStorage()
        request_patch = mock.patch("cintafactory.storage.seaweedfs_storage.urlopen")
        self.urlopen = request_patch.start()
        self.addCleanup(request_patch.stop)
        self.response = self.urlopen.return_value.__enter__.return_value
        metric_patch = mock.patch("cintafactory.storage.seaweedfs_storage.emit_baseline_metric")
        self.emit_metric = metric_patch.start()
        self.addCleanup(metric_patch.stop)

    def _assert_request(self, method, key):
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://files.example.test/root/docs/file%20name.txt")
        self.assertEqual(request.get_method(), method)
        self.assertEqual(self.urlopen.call_args.kwargs, {"timeout": 5})
        token = request.get_header("Authorization").removeprefix("Bearer ")
        header, payload, signature = token.split(".")
        expected = hmac.new(key.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        self.assertEqual(base64.urlsafe_b64decode(signature + "=="), expected)
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        self.assertEqual(claims["allowed_methods"], [method])
        self.assertEqual(claims["allowed_prefixes"], ["/root/docs/file name.txt"])
        self.assertEqual(claims["exp"] - claims["iat"], 60)
        return request

    def _assert_metric(self, operation, success, outcome):
        self.emit_metric.assert_called_once()
        args, kwargs = self.emit_metric.call_args
        self.assertEqual(args, ("storage.seaweedfs.request",))
        self.assertEqual(kwargs["success"], success)
        self.assertEqual(kwargs["dimensions"], {"operation": operation, "outcome": outcome})
        self.assertGreaterEqual(kwargs["duration_ms"], 0)

    @override_settings(SEAWEEDFS_FILER_URL="")
    def test_missing_filer_url_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "SEAWEEDFS_FILER_URL must be configured"):
            SeaweedFSStorage()

    def test_invalid_public_url_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "SEAWEEDFS_PUBLIC_URL must be an http(s) URL"):
            SeaweedFSStorage(public_url="ftp://files.example.test")

    @override_settings(SEAWEEDFS_PUBLIC_URL="", SEAWEEDFS_BASE_DIR="", SEAWEEDFS_JWT_READ_KEY="")
    def test_public_url_falls_back_to_filer_without_base_directory(self):
        self.assertEqual(SeaweedFSStorage().url("/docs/file name.txt"),
                         "https://files.example.test/docs/file%20name.txt")

    def test_open_returns_file_contents_with_read_authorization(self):
        self.urlopen.return_value = BytesIO(b"stored document")
        with self.storage.open("docs/file name.txt") as content:
            self.assertEqual(content.read(), b"stored document")
            self.assertEqual(content.name, "docs/file name.txt")
        self._assert_request("GET", "test-read-key")
        self._assert_metric("open", True, "ok")

    def test_open_http_errors_become_file_not_found_with_failure_metric(self):
        for status, outcome in ((404, "not_found"), (503, "http_error")):
            with self.subTest(status=status):
                self.emit_metric.reset_mock()
                error = HTTPError("https://files.example.test", status, "failed", None, None)
                self.urlopen.side_effect = error
                with self.assertRaises(FileNotFoundError) as raised:
                    self.storage.open("missing.txt")
                self.assertIs(raised.exception.__cause__, error)
                self._assert_metric("open", False, outcome)

    def test_save_uploads_bytes_with_write_authorization_and_content_type(self):
        for content in (ContentFile(b"document"), SimpleUploadedFile("file.txt", b"document", "text/plain")):
            with self.subTest(content_type=getattr(content, "content_type", None)):
                self.emit_metric.reset_mock()
                self.assertEqual(self.storage.save("docs/file name.txt", content), "docs/file name.txt")
                request = self._assert_request("PUT", "test-write-key")
                self.assertEqual(request.data, b"document")
                self.assertEqual(request.get_header("Content-length"), "8")
                self.assertEqual(request.get_header("Content-type"), getattr(content, "content_type", "application/octet-stream"))
                self._assert_metric("save", True, "ok")

    def test_delete_uses_write_authorization_and_records_success(self):
        self.storage.delete("docs/file name.txt")
        self._assert_request("DELETE", "test-write-key")
        self._assert_metric("delete", True, "ok")

    def test_exists_uses_read_authorization_and_records_success(self):
        self.assertTrue(self.storage.exists("docs/file name.txt"))
        self._assert_request("HEAD", "test-read-key")
        self._assert_metric("exists", True, "ok")

    def test_delete_and_exists_propagate_server_errors(self):
        for operation in ("delete", "exists"):
            with self.subTest(operation=operation):
                self.emit_metric.reset_mock()
                error = HTTPError("https://files.example.test", 503, "unavailable", None, None)
                self.urlopen.side_effect = error
                with self.assertRaises(HTTPError) as raised:
                    getattr(self.storage, operation)("file.txt")
                self.assertIs(raised.exception, error)
                self._assert_metric(operation, False, "http_error")

    def test_connection_errors_propagate_with_failure_metrics(self):
        for operation in ("open", "save", "delete", "exists"):
            with self.subTest(operation=operation):
                self.emit_metric.reset_mock()
                error = URLError("offline")
                self.urlopen.side_effect = error
                args = ("file.txt", ContentFile(b"data")) if operation == "save" else ("file.txt",)
                with self.assertRaises(URLError) as raised:
                    getattr(self.storage, operation)(*args)
                self.assertIs(raised.exception, error)
                self._assert_metric(operation, False, "URLError" if operation == "save" else "url_error")

    def test_size_handles_numeric_missing_and_invalid_content_length(self):
        for headers, expected in (({"Content-Length": "42"}, 42), ({}, 0),
                                  ({"Content-Length": "invalid"}, 0), ({"Content-Length": None}, 0)):
            with self.subTest(headers=headers):
                self.response.headers = headers
                self.assertEqual(self.storage.size("docs/file name.txt"), expected)
                self._assert_request("HEAD", "test-read-key")

    def test_modified_time_parses_http_timestamp(self):
        self.response.headers = {"Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"}
        self.assertEqual(self.storage.get_modified_time("docs/file name.txt"),
                         datetime(2015, 10, 21, 7, 28, tzinfo=timezone.utc))
        self._assert_request("HEAD", "test-read-key")

    def test_modified_time_translates_only_not_found_errors(self):
        for status, exception in ((404, FileNotFoundError), (503, HTTPError)):
            with self.subTest(status=status):
                self.urlopen.side_effect = HTTPError("https://files.example.test", status, "failed", None, None)
                with self.assertRaises(exception):
                    self.storage.get_modified_time("file.txt")

    @mock.patch("cintafactory.storage.seaweedfs_storage.time.time", return_value=1700000000)
    def test_public_url_has_read_token_with_public_expiry(self, clock):
        url = urlsplit(self.storage.url("docs/file name.txt"))
        self.assertEqual((url.scheme, url.netloc, url.path),
                         ("https", "cdn.example.test", "/root/docs/file%20name.txt"))
        token = parse_qs(url.query)["jwt"][0]
        self.assertEqual(token, build_filer_jwt("test-read-key", "root/docs/file name.txt", "GET", 300))

    def test_storage_can_be_reconstructed_by_django(self):
        self.assertEqual(self.storage.deconstruct(),
                         ("cintafactory.storage.seaweedfs_storage.SeaweedFSStorage", [], {}))
