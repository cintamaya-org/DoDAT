# SPDX-License-Identifier: AGPL-3.0-only

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from users.models import BusinessDirection, Role
from workflows.sync import sync_workflow_definitions

from .constants import DAT_REQUIRED_PARTICIPANT_ROLES
from .exporters import DATExportModelBuilder
from .importers import DATImportError, DATImportService
from .models import Application, DAT
from .versioning import DATReferenceError


class DATVersionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        direction = BusinessDirection.objects.create(name="Finance", slug="version-finance")
        cls.billing = Application.objects.create(name="Billing", code="BIL", business_direction=direction)
        cls.payroll = Application.objects.create(name="Payroll", code="PAY", business_direction=direction)

    def test_each_application_starts_at_one_and_database_rejects_local_duplicate(self):
        billing = DAT.objects.create(application=self.billing, title="First")
        payroll = DAT.objects.create(application=self.payroll, title="First")
        self.assertEqual((billing.reference, payroll.reference), ("001", "001"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            DAT.objects.bulk_create([DAT(
                title="Duplicate", application=self.billing, reference="001",
                business_direction_id=self.billing.business_direction_id,
            )])
        self.assertEqual(DAT.objects.create(application=self.billing, title="Next").reference, "002")

    def test_manual_numbers_advance_counter_and_renames_and_deletions_retain_it(self):
        dat = DAT.objects.create(application=self.billing, reference="10", title="Manual")
        self.assertEqual(dat.reference, "010")
        dat.reference = "REV-A"
        dat.save(update_fields=["reference"])
        self.assertEqual(DAT.objects.create(application=self.billing, title="Next").reference, "011")
        DAT.objects.create(application=self.billing, reference="999", title="Last").delete()
        self.assertEqual(DAT.objects.create(application=self.billing, title="Next").reference, "1000")

    def test_move_checks_destination_and_preserves_both_sequences(self):
        dat = DAT.objects.create(application=self.billing, reference="10", title="Move")
        occupied = DAT.objects.create(application=self.payroll, reference="010", title="Occupied")
        dat.application = self.payroll
        with self.assertRaisesMessage(DATReferenceError, "existe déjà"):
            dat.save(update_fields=["application"])
        self.assertTrue(self.billing.dats.filter(pk=dat.pk).exists())
        occupied.delete()
        dat.reference = "20"
        dat.save(update_fields=["application", "reference"])
        self.assertEqual(self.payroll.dats.get(pk=dat.pk).reference, "020")
        self.assertEqual(DAT.objects.create(application=self.billing, title="Next").reference, "011")
        self.assertEqual(DAT.objects.create(application=self.payroll, title="Next").reference, "021")

    def test_rolled_back_create_does_not_consume_a_number(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            DAT.objects.create(application=self.billing, title="Rolled back")
            raise RuntimeError("rollback")
        self.assertEqual(DAT.objects.create(application=self.billing, title="Next").reference, "001")

    def test_admin_create_and_save_time_validation_error(self):
        admin = get_user_model().objects.create_superuser(username="version-admin", email="admin@example.test")
        self.client.force_login(admin)
        url = reverse("admin:dat_dat_add")
        payload = {
            "application": str(self.billing.pk), "title": "Admin version", "reference": "",
            "business_direction": str(self.billing.business_direction_id), "status": "nouvelle_demande",
            "pdf_export_size": "0", "participants-TOTAL_FORMS": "0", "participants-INITIAL_FORMS": "0",
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.billing.dats.get().reference, "001")
        DAT.objects.create(application=self.billing, reference="9" * 64, title="Maximum")
        response = self.client.post(url, payload)
        self.assertContains(response, "numérotation automatique de cette application est épuisée")
        self.assertEqual(self.billing.dats.count(), 2)

    def test_import_reference_precedence_and_raw_export(self):
        importer = DATImportService()
        payload = {"application": {"code": "BIL"}, "dat": {"title": "Imported", "reference": 7}}
        overridden = importer.import_from_payload(payload, reference_override="REV-B").dat
        self.assertEqual(overridden.reference, "REV-B")
        imported = importer.import_from_payload(payload).dat
        self.assertEqual(DATExportModelBuilder().build_dat_metadata(imported)["reference"], "007")
        with self.assertRaisesMessage(DATImportError, "existe déjà"):
            importer.import_from_payload(payload)
        del payload["dat"]["reference"]
        self.assertEqual(importer.import_from_payload(payload).dat.reference, "008")
        payload["application"]["code"] = "PAY"
        self.assertEqual(importer.import_from_payload(payload, reference_override="7").dat.reference, "007")

    def test_search_distinguishes_authorized_versions_and_header_shows_application(self):
        user = get_user_model().objects.create_user(username="version-reader")
        billing = DAT.objects.create(application=self.billing, title="Billing file", owner=user)
        payroll = DAT.objects.create(application=self.payroll, title="Payroll file", owner=user)
        hidden = Application.objects.create(name="Other", code="OTH", business_direction=self.billing.business_direction)
        DAT.objects.create(application=hidden, title="Hidden")
        self.client.force_login(user)
        results = self.client.get(reverse("dat:topbar_search"), {"q": "001", "applications": "0"}).json()["results"]
        self.assertEqual(
            [(item["label"], item["url"]) for item in results],
            [("Billing (BIL) - 001", reverse("dat:my_detail", args=[billing.pk])),
             ("Payroll (PAY) - 001", reverse("dat:my_detail", args=[payroll.pk]))],
        )
        with patch("dat.utils.get_dat_export_storage") as storage:
            storage.return_value.exists.return_value = False
            detail = self.client.get(reverse("dat:my_detail", args=[billing.pk]))
        self.assertContains(detail, 'aria-label="Version Billing (BIL) - 001, statut')


class DATVersionTransactionTests(TransactionTestCase):
    def setUp(self):
        for slug, name in DAT_REQUIRED_PARTICIPANT_ROLES:
            Role.objects.get_or_create(slug=slug, defaults={"name": name})
        sync_workflow_definitions()

    def concurrently(self, *operations):
        barrier = Barrier(len(operations))

        def run(operation):
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            futures = [pool.submit(run, operation) for operation in operations]
            return [future.result(timeout=20) for future in futures]

    def test_simultaneous_first_creates_receive_distinct_versions(self):
        direction = BusinessDirection.objects.create(name="Concurrent", slug="version-concurrency")
        app = Application.objects.create(name="Concurrent", code="CON", business_direction=direction)

        def create_version():
            return DAT.objects.create(application_id=app.pk, title="Concurrent").reference

        self.assertCountEqual(self.concurrently(create_version, create_version), ["001", "002"])

    def test_concurrent_move_and_rename_advance_the_destination_counter(self):
        direction = BusinessDirection.objects.create(name="Moves", slug="version-moves")
        source = Application.objects.create(name="Source", code="SRC", business_direction=direction)
        target = Application.objects.create(name="Target", code="DST", business_direction=direction)
        moving = DAT.objects.create(application=source, title="Move and rename")
        renaming = DAT.objects.get(pk=moving.pk)

        def move():
            moving.application = target
            moving.save(update_fields=["application"])

        def rename():
            renaming.reference = "20"
            renaming.save(update_fields=["reference"])

        self.concurrently(move, rename)
        self.assertEqual(target.dats.get(pk=moving.pk).reference, "020")
        self.assertEqual(DAT.objects.create(application=target, title="Next").reference, "021")

    def test_migration_seeds_numeric_maximum_and_preserves_legacy_spellings(self):
        previous = [("dat", "0006_dat_board_order_index")]
        current = [("dat", "0007_application_dat_versions")]
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(current))
        apps = executor.loader.project_state(previous).apps
        direction = apps.get_model("users", "BusinessDirection").objects.create(name="Legacy", slug="version-legacy")
        app = apps.get_model("dat", "Application").objects.create(name="Legacy", code="LEG", business_direction=direction)
        legacy = apps.get_model("dat", "DAT")
        for reference in ("7", "010", "REV-900", ""):
            legacy.objects.create(application=app, business_direction=direction, title="Legacy", reference=reference)

        MigrationExecutor(connection).migrate(current)
        dats = DAT.objects.filter(application_id=app.pk)
        self.assertCountEqual(dats.values_list("reference", flat=True), ["7", "010", "REV-900", ""])
        dat = dats.get(reference="7")
        dat.title = "Edited"
        dat.save()
        self.assertEqual(dats.get(pk=dat.pk).reference, "7")
        with self.assertRaisesMessage(DATReferenceError, "existe déjà"):
            DAT.objects.create(application_id=app.pk, title="Duplicate", reference="007")
        self.assertEqual(DAT.objects.create(application_id=app.pk, title="Next").reference, "011")
