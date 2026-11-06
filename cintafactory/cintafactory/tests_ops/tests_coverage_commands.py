# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from workflows.exceptions import WorkflowError


class WorkflowManagementCommandCoverageTests(SimpleTestCase):
    @mock.patch("workflows.management.commands.sync_workflows.sync_workflow_definitions")
    @mock.patch("workflows.management.commands.sync_workflows.transaction.atomic")
    @mock.patch("workflows.management.commands.sync_workflows.transaction.set_rollback")
    def test_sync_workflows_check_rolls_back_and_normal_run_publishes(self, set_rollback, atomic, sync):
        from io import StringIO

        check_output = StringIO()
        call_command("sync_workflows", "--check", stdout=check_output)
        self.assertIn("no changes saved", check_output.getvalue())
        sync.assert_called_once_with()
        set_rollback.assert_called_once_with(True)

        sync.reset_mock()
        set_rollback.reset_mock()
        apply_output = StringIO()
        call_command("sync_workflows", stdout=apply_output)
        self.assertIn("synchronised and published", apply_output.getvalue())
        sync.assert_called_once_with()
        set_rollback.assert_not_called()

    @mock.patch("workflows.management.commands.migrate_workflow_instances.migrate_workflow_instances")
    @mock.patch("workflows.management.commands.migrate_workflow_instances.transaction.atomic")
    @mock.patch("workflows.management.commands.migrate_workflow_instances.transaction.set_rollback")
    def test_workflow_migration_dry_run_parses_mapping_and_object_filters(self, set_rollback, atomic, migrate):
        from io import StringIO

        migrate.return_value = SimpleNamespace(examined=3, migrated=2, target_version=4)
        output = StringIO()
        call_command(
            "migrate_workflow_instances",
            "design",
            "--map",
            " old = new ",
            "--object-id",
            "17",
            "--object-id",
            "18",
            stdout=output,
        )

        migrate.assert_called_once_with(
            workflow_code="design",
            state_mapping={"old": "new"},
            object_ids=["17", "18"],
        )
        set_rollback.assert_called_once_with(True)
        self.assertIn("dry-run: examined=3, migrated=2, target=v4", output.getvalue())

    @mock.patch("workflows.management.commands.migrate_workflow_instances.migrate_workflow_instances")
    @mock.patch("workflows.management.commands.migrate_workflow_instances.transaction.atomic")
    @mock.patch("workflows.management.commands.migrate_workflow_instances.transaction.set_rollback")
    def test_workflow_migration_apply_and_error_paths(self, set_rollback, atomic, migrate):
        from io import StringIO

        migrate.return_value = SimpleNamespace(examined=1, migrated=1, target_version=2)
        output = StringIO()
        call_command("migrate_workflow_instances", "design", "--apply", stdout=output)
        self.assertIn("applied: examined=1", output.getvalue())
        set_rollback.assert_not_called()

        migrate.reset_mock()
        with self.assertRaises(CommandError):
            call_command("migrate_workflow_instances", "design", "--map", "invalid")
        migrate.assert_not_called()

        migrate.side_effect = WorkflowError("unsupported transition")
        with self.assertRaisesMessage(CommandError, "unsupported transition"):
            call_command("migrate_workflow_instances", "design")


class DatViewflowRepairCommandCoverageTests(SimpleTestCase):
    @mock.patch("dat_viewflow.management.commands.repair_dat_viewflow.ensure_dat_viewflow_process")
    @mock.patch("dat_viewflow.management.commands.repair_dat_viewflow.DatViewflowProcess")
    @mock.patch("dat_viewflow.management.commands.repair_dat_viewflow.DAT")
    def test_repair_command_dry_run_filters_and_skips_existing_links(
        self, dat_model, link_model, ensure_process
    ):
        from io import StringIO

        first = SimpleNamespace(pk="dat-1")
        second = SimpleNamespace(pk="dat-2")
        dat_model.objects.all.return_value.order_by.return_value.filter.return_value.iterator.return_value = [first, second]
        link_model.objects.filter.return_value.first.side_effect = [None, SimpleNamespace(process_id="wf-2")]
        output = StringIO()

        call_command("repair_dat_viewflow", "--dat-id", "dat-1", "--dry-run", stdout=output)

        dat_model.objects.all.return_value.order_by.return_value.filter.assert_called_once_with(pk="dat-1")
        self.assertIn("Would repair DAT dat-1", output.getvalue())
        self.assertIn("Repaired: 1, Skipped: 1", output.getvalue())
        ensure_process.assert_not_called()

    @mock.patch("dat_viewflow.management.commands.repair_dat_viewflow.ensure_dat_viewflow_process")
    @mock.patch("dat_viewflow.management.commands.repair_dat_viewflow.DatViewflowProcess")
    @mock.patch("dat_viewflow.management.commands.repair_dat_viewflow.DAT")
    def test_repair_command_applies_repairs_and_counts_missing_process_ids(
        self, dat_model, link_model, ensure_process
    ):
        from io import StringIO

        first = SimpleNamespace(pk="dat-1")
        second = SimpleNamespace(pk="dat-2")
        dat_model.objects.all.return_value.order_by.return_value.iterator.return_value = [first, second]
        link_model.objects.filter.return_value.first.side_effect = [
            SimpleNamespace(process_id="wf-1"),
            None,
        ]
        output = StringIO()

        call_command("repair_dat_viewflow", stdout=output)

        self.assertEqual(ensure_process.call_args_list, [mock.call(first), mock.call(second)])
        self.assertIn("Repaired: 1, Skipped: 1", output.getvalue())


class LikeC4MigrationCommandCoverageTests(SimpleTestCase):
    @mock.patch("diagrams.management.commands.migrate_likec4_files.SeaweedFSStorage")
    def test_missing_data_directory_exits_without_constructing_storage(self, storage):
        from io import StringIO

        with TemporaryDirectory() as root:
            output = StringIO()
            call_command("migrate_likec4_files", "--root", root, stdout=output)
        self.assertIn("data folder not found", output.getvalue())
        storage.assert_not_called()

    @mock.patch("diagrams.management.commands.migrate_likec4_files.SeaweedFSStorage")
    def test_dry_run_lists_matching_files_without_uploading(self, storage_factory):
        from io import StringIO

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data" / "nested").mkdir(parents=True)
            (root / "data" / "nested" / "architecture.c4").write_text("spec", encoding="utf-8")
            (root / "data" / "ignore.txt").write_text("ignored", encoding="utf-8")
            output = StringIO()
            call_command("migrate_likec4_files", "--root", str(root), "--dry-run", stdout=output)

        storage_factory.return_value.exists.assert_not_called()
        storage_factory.return_value.save.assert_not_called()
        self.assertIn("[dry-run] upload data/nested/architecture.c4 (4 bytes)", output.getvalue())
        self.assertIn("Uploaded: 0, skipped: 0", output.getvalue())

    @mock.patch("diagrams.management.commands.migrate_likec4_files.LikeC4Diagram.objects.update_or_create")
    @mock.patch("diagrams.management.commands.migrate_likec4_files.SeaweedFSStorage")
    def test_upload_skip_overwrite_and_delete_local_paths(self, storage_factory, update_or_create):
        from io import StringIO

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            uploaded = data / "upload.c4"
            existing = data / "existing.c4"
            uploaded.write_text("new", encoding="utf-8")
            existing.write_text("old", encoding="utf-8")
            storage = storage_factory.return_value
            storage.exists.side_effect = [True, False]
            output = StringIO()

            call_command("migrate_likec4_files", "--root", str(root), "--delete-local", stdout=output)

            self.assertFalse(uploaded.exists())
            self.assertTrue(existing.exists())

        self.assertEqual(storage.save.call_count, 1)
        saved_path, content = storage.save.call_args.args
        self.assertEqual(saved_path, "data/upload.c4")
        self.assertEqual(content.read(), b"new")
        self.assertEqual(update_or_create.call_count, 2)
        self.assertIn("Uploaded: 1, skipped: 1", output.getvalue())

    @mock.patch("diagrams.management.commands.migrate_likec4_files.LikeC4Diagram.objects.update_or_create")
    @mock.patch("diagrams.management.commands.migrate_likec4_files.SeaweedFSStorage")
    def test_read_and_storage_http_errors_are_reported(self, storage_factory, update_or_create):
        from io import StringIO

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            file_path = data / "broken.c4"
            file_path.write_text("spec", encoding="utf-8")
            stderr = StringIO()
            with mock.patch.object(Path, "read_bytes", side_effect=OSError("read failed")):
                output = StringIO()
                call_command(
                    "migrate_likec4_files",
                    "--root",
                    str(root),
                    stdout=output,
                    stderr=stderr,
                )
            self.assertIn("Uploaded: 0, skipped: 1", output.getvalue())
            self.assertIn("Unable to read", stderr.getvalue())

            storage_factory.return_value.exists.side_effect = HTTPError(
                "http://storage.invalid", 503, "down", {}, None
            )
            with self.assertRaises(HTTPError):
                call_command("migrate_likec4_files", "--root", str(root), stdout=StringIO(), stderr=StringIO())
        update_or_create.assert_not_called()
