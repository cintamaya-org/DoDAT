# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import socket
import struct
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, SimpleTestCase, override_settings

from .attachments import (
    AttachmentSecurityError,
    build_attachment_metadata,
    quarantine_rejected_upload,
    scan_file_with_clamav,
)
from . import views


class AttachmentSecurityValidationTests(SimpleTestCase):
    def test_build_attachment_metadata_rejects_mime_extension_mismatch(self):
        uploaded = SimpleUploadedFile(
            "report.pdf",
            b"fake-pdf-content",
            content_type="text/plain",
        )
        with self.assertRaises(AttachmentSecurityError) as ctx:
            build_attachment_metadata(uploaded)
        self.assertEqual(ctx.exception.failure_state, "mime_not_allowed")

    @override_settings(CLAMAV_HOST="clamav", CLAMAV_PORT=3310, CLAMAV_TIMEOUT=1, CLAMAV_RETRY_COUNT=0)
    @mock.patch("dat.attachments.emit_baseline_metric")
    @mock.patch("dat.attachments._probe_clamav")
    @mock.patch("dat.attachments._scan_file_with_scan_command")
    def test_scan_timeout_has_explicit_failure_state(self, scan_command, _probe, emit_metric):
        scan_command.side_effect = socket.timeout("timeout")
        uploaded = SimpleUploadedFile("safe.txt", b"safe payload", content_type="text/plain")

        with self.assertRaises(AttachmentSecurityError) as ctx:
            scan_file_with_clamav(uploaded)

        self.assertEqual(ctx.exception.failure_state, "scanner_timeout")
        emit_metric.assert_called_once()
        _, kwargs = emit_metric.call_args
        self.assertEqual(kwargs["dimensions"]["outcome"], "scanner_timeout")

    @override_settings(CLAMAV_HOST="clamav", CLAMAV_PORT=3310, CLAMAV_TIMEOUT=1, CLAMAV_RETRY_COUNT=0)
    @mock.patch("dat.attachments.socket.create_connection")
    @mock.patch("dat.attachments.emit_baseline_metric")
    @mock.patch("dat.attachments._probe_clamav")
    def test_scan_streams_bounded_instream_chunks_and_resets_upload(
        self, _probe, emit_metric, create_connection
    ):
        payload = b"a" * (64 * 1024) + b"end"
        uploaded = SimpleUploadedFile("safe.txt", payload, content_type="text/plain")
        uploaded.read(1)

        scanner_socket = mock.MagicMock()
        scanner_socket.__enter__.return_value = scanner_socket
        scanner_socket.recv.return_value = b"stream: OK\0"
        create_connection.return_value = scanner_socket

        scan_file_with_clamav(uploaded)

        self.assertEqual(
            scanner_socket.sendall.call_args_list,
            [
                mock.call(b"zINSTREAM\0"),
                mock.call(struct.pack("!I", 64 * 1024)),
                mock.call(b"a" * (64 * 1024)),
                mock.call(struct.pack("!I", 3)),
                mock.call(b"end"),
                mock.call(struct.pack("!I", 0)),
            ],
        )
        scanner_socket.recv.assert_called_once_with(4096)
        create_connection.assert_called_once_with(("clamav", 3310), timeout=1)
        self.assertEqual(uploaded.tell(), 0)
        emit_metric.assert_called_once()
        self.assertEqual(emit_metric.call_args.kwargs["dimensions"]["outcome"], "ok")

    @override_settings(CLAMAV_HOST="clamav", CLAMAV_PORT=3310, CLAMAV_TIMEOUT=1, CLAMAV_RETRY_COUNT=0)
    @mock.patch("dat.attachments.socket.create_connection")
    @mock.patch("dat.attachments.emit_baseline_metric")
    @mock.patch("dat.attachments._probe_clamav")
    def test_scan_sends_zero_length_stream_terminator_for_empty_upload(
        self, _probe, emit_metric, create_connection
    ):
        uploaded = SimpleUploadedFile("empty.txt", b"", content_type="text/plain")
        scanner_socket = mock.MagicMock()
        scanner_socket.__enter__.return_value = scanner_socket
        scanner_socket.recv.return_value = b"stream: OK\0"
        create_connection.return_value = scanner_socket

        scan_file_with_clamav(uploaded)

        self.assertEqual(
            scanner_socket.sendall.call_args_list,
            [mock.call(b"zINSTREAM\0"), mock.call(struct.pack("!I", 0))],
        )
        self.assertEqual(emit_metric.call_args.kwargs["dimensions"]["outcome"], "ok")

    @override_settings(CLAMAV_HOST="clamav", CLAMAV_PORT=3310, CLAMAV_TIMEOUT=1, CLAMAV_RETRY_COUNT=0)
    @mock.patch("dat.attachments.socket.create_connection")
    @mock.patch("dat.attachments.emit_baseline_metric")
    @mock.patch("dat.attachments._probe_clamav")
    def test_scan_fails_closed_on_incomplete_instream_response(
        self, _probe, emit_metric, create_connection
    ):
        uploaded = SimpleUploadedFile("safe.txt", b"safe payload", content_type="text/plain")
        scanner_socket = mock.MagicMock()
        scanner_socket.__enter__.return_value = scanner_socket
        scanner_socket.recv.return_value = b""
        create_connection.return_value = scanner_socket

        with self.assertRaises(AttachmentSecurityError) as ctx:
            scan_file_with_clamav(uploaded)

        self.assertEqual(ctx.exception.failure_state, "scanner_unavailable")
        self.assertEqual(uploaded.tell(), 0)
        self.assertEqual(emit_metric.call_args.kwargs["dimensions"]["outcome"], "scanner_unavailable")

    @mock.patch("dat.attachments.get_attachment_storage")
    @override_settings(ATTACHMENT_QUARANTINE_ENABLED="1", ATTACHMENT_QUARANTINE_MAX_BYTES=1024)
    def test_quarantine_rejected_upload_stores_file(self, get_storage):
        storage = mock.Mock()
        storage.save.return_value = "dat_attachments_quarantine/scanner_unavailable/path.bin"
        get_storage.return_value = storage
        uploaded = SimpleUploadedFile("unsafe.txt", b"payload", content_type="text/plain")

        stored = quarantine_rejected_upload(uploaded, reason="scanner_unavailable")

        self.assertTrue(stored.startswith("dat_attachments_quarantine/"))
        storage.save.assert_called_once()


class UploadAttachmentFailureStateViewTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    @mock.patch("dat.views.render_section_attachments_snippet", return_value="<div>attachments</div>")
    @mock.patch("dat.views.create_section_attachment")
    @mock.patch("dat.views.section_has_attachments", return_value=True)
    @mock.patch("dat.views.user_can_update_section_status", return_value=True)
    @mock.patch("dat.views.filter_dat_queryset_for_user")
    @mock.patch("dat.views.workflow_has_capability", return_value=False)
    @mock.patch("dat.views.get_object_or_404")
    def test_upload_ajax_returns_failure_states_for_security_rejections(
        self,
        get_object_or_404,
        _workflow_has_capability,
        _filter_queryset,
        _can_update,
        _has_attachments,
        create_attachment,
        _render_snippet,
    ):
        dat_uuid = "11111111-1111-1111-1111-111111111111"
        dat = SimpleNamespace(pk=dat_uuid, status="draft")
        section = SimpleNamespace(
            slug="general",
            metadata=SimpleNamespace(slug="general"),
            can_user_edit=lambda _user: True,
        )
        get_object_or_404.side_effect = [dat, section]
        create_attachment.side_effect = AttachmentSecurityError(
            "Antivirus indisponible",
            failure_state="scanner_unavailable",
            quarantine_path="dat_attachments_quarantine/scanner_unavailable/file.bin",
        )

        uploaded = SimpleUploadedFile("doc.txt", b"content", content_type="text/plain")
        request = self.factory.post(
            "/dat/my/dat-1/sections/general/attachments/upload/",
            data={"attachments": uploaded},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = SimpleNamespace(is_authenticated=True)

        response = views.upload_section_attachment(request, dat_pk=dat_uuid, section_slug="general")
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["failure_states"][0]["state"], "scanner_unavailable")
        self.assertIn("quarantine_path", payload["failure_states"][0])

    @mock.patch("dat.views.render_section_attachments_snippet", return_value="<div>attachments</div>")
    @mock.patch("dat.views.create_section_attachment")
    @mock.patch("dat.views.section_has_attachments", return_value=True)
    @mock.patch("dat.views.filter_dat_queryset_for_user")
    @mock.patch("dat.views.workflow_has_capability", return_value=False)
    @mock.patch("dat.views.get_object_or_404")
    def test_upload_success_toast_uses_french_singular(
        self,
        get_object_or_404,
        _workflow_has_capability,
        _filter_queryset,
        _has_attachments,
        create_attachment,
        _render_snippet,
    ):
        dat_uuid = "11111111-1111-1111-1111-111111111111"
        dat = SimpleNamespace(pk=dat_uuid, status="draft")
        section = SimpleNamespace(
            slug="general",
            metadata=SimpleNamespace(slug="general"),
            can_user_edit=lambda _user: True,
        )
        get_object_or_404.side_effect = [dat, section]
        create_attachment.return_value = None

        uploaded = SimpleUploadedFile("doc.txt", b"content", content_type="text/plain")
        request = self.factory.post(
            "/dat/my/dat-1/sections/general/attachments/upload/",
            data={"attachments": uploaded},
        )
        request.user = SimpleNamespace(is_authenticated=True)
        request.session = {}
        request._messages = FallbackStorage(request)

        response = views.upload_section_attachment(request, dat_pk=dat_uuid, section_slug="general")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [str(message) for message in get_messages(request)],
            ["1 pièce jointe ajoutée."],
        )
