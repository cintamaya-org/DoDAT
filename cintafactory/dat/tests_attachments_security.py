# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import socket
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
