# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import uuid
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from users.models import BusinessDirection, Role

from . import utils
from .export_access import approve_request, create_request
from .models import Application, DAT, DATAdmin, DATExportAccessEventType, DATExportAccessHistory
from .tasks import _run_pdf_generation


class PDFStorageReferenceSecurityTests(SimpleTestCase):
    def setUp(self):
        self.dat = SimpleNamespace(pk=uuid.uuid4(), reference="Renamed DAT", title="Export", pdf_export_path="")

    @mock.patch("dat.utils.get_dat_export_storage")
    def test_foreign_and_malformed_paths_never_reach_storage(self, get_storage):
        prefix = f"dat_exports/{self.dat.pk}/"
        paths = [
            f"dat_exports/{uuid.uuid4()}/foreign.pdf", "diagrams/private.c4", "/" + prefix + "file.pdf",
            prefix + "../foreign.pdf", prefix + "nested/file.pdf", prefix + "%2e%2e%2fforeign.pdf",
            prefix + "file\\other.pdf", prefix + "bad\x00.pdf", prefix + "bad\r\n.pdf", prefix + ".pdf",
        ]
        for path in paths:
            with self.subTest(path=repr(path)):
                self.dat.pdf_export_path = path
                self.assertFalse(utils.dat_pdf_export_exists(self.dat))
                self.assertIsNone(utils.dat_pdf_export_modified_at(self.dat))
                self.assertIsNone(utils.open_dat_pdf_export(self.dat))
        get_storage.assert_not_called()

    @mock.patch("dat.utils.get_dat_export_storage")
    def test_historical_filename_within_dat_namespace_remains_readable(self, get_storage):
        path = f"dat_exports/{self.dat.pk}/old-reference.pdf"
        self.dat.pdf_export_path = path
        storage = get_storage.return_value
        storage.exists.return_value = True
        storage.get_modified_time.return_value = timezone.now()
        storage.open.return_value = BytesIO(b"%PDF historical")
        self.assertTrue(utils.dat_pdf_export_exists(self.dat))
        self.assertIsNotNone(utils.dat_pdf_export_modified_at(self.dat))
        self.assertEqual(utils.open_dat_pdf_export(self.dat).read(), b"%PDF historical")
        storage.open.assert_called_once_with(path, "rb")

    @mock.patch("dat.utils.get_dat_export_storage")
    def test_empty_reference_uses_canonical_own_path(self, get_storage):
        utils.dat_pdf_export_exists(self.dat)
        get_storage.return_value.exists.assert_called_once_with(utils.get_dat_pdf_export_path(self.dat))


class DATExportAPISecurityTests(TestCase):
    def setUp(self):
        self.role, _ = Role.objects.get_or_create(
            slug="porteur-demande", defaults={"name": "Export API porteur"},
        )
        self.user = get_user_model().objects.create_user(
            username="dat-api-creator", password="pwd", role=self.role, can_use_api=True,
        )
        self.other_user = get_user_model().objects.create_user(username="dat-api-other", password="pwd")
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label="dat", codename="add_dat"))
        self.application = Application.objects.create(
            code="export-api-security", name="Export API",
            business_direction=BusinessDirection.objects.create(name="Export API Direction", slug="export-api-direction"),
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_create_ignores_internal_export_metadata_and_approval_override(self):
        response = self.api.post(reverse("api:dat-list"), {
            "reference": "SECURE-API-DAT", "title": "Own DAT", "application": str(self.application.pk),
            "owner": str(self.other_user.pk), "status": "refuse",
            "pdf_export_path": "dat_exports/foreign/secret.pdf", "pdf_export_content_type": "text/html",
            "pdf_export_size": 9000, "pdf_export_in_progress": True,
            "pdf_export_requested_by": str(self.user.pk), "pdf_export_requested_by_display": "Forged",
            "secure_export_requires_dual_admin_approval": False,
        }, format="json")
        self.assertEqual(response.status_code, 201)
        dat = DAT.objects.get(pk=response.data["id"])
        self.assertEqual(dat.owner, self.user)
        self.assertEqual(dat.status, DAT._meta.get_field("status").default)
        self.assertEqual(dat.pdf_export_path, "")
        self.assertEqual(dat.pdf_export_content_type, "application/pdf")
        self.assertEqual(dat.pdf_export_size, 0)
        self.assertFalse(dat.pdf_export_in_progress)
        self.assertIsNone(dat.pdf_export_requested_by)
        self.assertEqual(dat.pdf_export_requested_by_display, "")
        self.assertTrue(dat.secure_export_requires_dual_admin_approval)

    def test_existing_poisoned_path_is_not_downloaded(self):
        dat = DAT.objects.create(
            reference="POISONED-DAT", title="Own DAT", application=self.application, owner=self.user,
            secure_export_requires_dual_admin_approval=False, pdf_export_path="diagrams/foreign/source.c4",
        )
        self.client.force_login(self.user)
        with mock.patch("dat.utils.get_dat_export_storage") as get_storage:
            response = self.client.get(reverse("dat:my_export_pdf_download", args=[dat.pk]))
        self.assertEqual(response.status_code, 302)
        get_storage.assert_not_called()


class FreshPDFApprovalSecurityTests(TestCase):
    def setUp(self):
        role = Role.objects.create(name="Fresh PDF member", slug="fresh-pdf-member")
        self.owner = get_user_model().objects.create_user(username="fresh-pdf-owner", password="pwd", role=role)
        self.admin_1 = get_user_model().objects.create_user(username="fresh-pdf-admin-1", password="pwd", role=role)
        self.admin_2 = get_user_model().objects.create_user(username="fresh-pdf-admin-2", password="pwd", role=role)
        application = Application.objects.create(
            code="fresh-pdf-security", name="Fresh PDF",
            business_direction=BusinessDirection.objects.create(name="Fresh PDF Direction", slug="fresh-pdf-direction"),
        )
        self.dat = DAT.objects.create(
            reference="FRESH-PDF-DAT", title="Fresh PDF", application=application, owner=self.owner,
        )
        DATAdmin.objects.create(dat=self.dat, user=self.admin_1)
        DATAdmin.objects.create(dat=self.dat, user=self.admin_2)

    def _approve(self):
        request = create_request(self.dat, self.admin_1)
        approve_request(self.dat, self.admin_2)
        request.refresh_from_db()
        return request

    def test_trigger_denies_unauthorized_user_before_queueing(self):
        self.client.force_login(self.owner)
        with mock.patch("dat.views.schedule_dat_pdf_generation") as schedule:
            response = self.client.post(
                reverse("dat:my_export_pdf_trigger", args=[self.dat.pk]),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"ok": False, "error": "export_locked"})
        schedule.assert_not_called()

    def test_both_aliases_deny_visible_nonapprover_before_generation_or_storage(self):
        self.client.force_login(self.owner)
        with (
            mock.patch("dat.views.DatExportBaseView.build_pdf_document") as generate,
            mock.patch("dat.views.store_dat_pdf_export") as store,
        ):
            for name in ("my_export_pdf_generate", "my_export_pdf"):
                response = self.client.get(reverse(f"dat:{name}", args=[self.dat.pk]))
                self.assertEqual(response.status_code, 302)
        generate.assert_not_called()
        store.assert_not_called()

    def test_expired_approval_cannot_generate_fresh_pdf(self):
        request = self._approve()
        type(request).objects.filter(pk=request.pk).update(access_valid_until=timezone.now() - timedelta(seconds=1))
        self.client.force_login(self.admin_1)
        with mock.patch("dat.views.DatExportBaseView.build_pdf_document") as generate:
            response = self.client.get(reverse("dat:my_export_pdf_generate", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 302)
        generate.assert_not_called()

    def test_background_generation_rechecks_expired_approval_before_storage(self):
        request = self._approve()
        type(request).objects.filter(pk=request.pk).update(access_valid_until=timezone.now() - timedelta(seconds=1))
        DAT.objects.filter(pk=self.dat.pk).update(
            pdf_export_in_progress=True,
            pdf_export_requested_by=self.admin_1,
        )
        with (
            mock.patch("dat.tasks.generate_dat_pdf") as generate,
            mock.patch("dat.tasks.store_dat_pdf_export") as store,
        ):
            self.assertFalse(_run_pdf_generation(self.dat.pk, base_url=None))
        generate.assert_not_called()
        store.assert_not_called()
        self.dat.refresh_from_db()
        self.assertFalse(self.dat.pdf_export_in_progress)

    def test_approved_fresh_pdf_download_works_and_is_audited(self):
        self._approve()
        self.client.force_login(self.admin_2)
        with (
            mock.patch("dat.views.DatExportBaseView.build_pdf_document", return_value=(b"%PDF approved", {})),
            mock.patch("dat.views.store_dat_pdf_export"),
        ):
            for name in ("my_export_pdf_generate", "my_export_pdf"):
                response = self.client.get(reverse(f"dat:{name}", args=[self.dat.pk]))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"%PDF approved")
        self.assertEqual(DATExportAccessHistory.objects.filter(
            dat=self.dat, actor=self.admin_2, event_type=DATExportAccessEventType.DOWNLOAD_PDF,
        ).count(), 2)
