# SPDX-License-Identifier: AGPL-3.0-only
"""Library selection and publication keep approval contracts pinned per DAT."""

import json
from io import StringIO
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings

from dat.models import DAT

from .exceptions import WorkflowConfigurationError
from .graph_config import load_graph
from .graph_engine import reconcile_graph
from .models import Workflow, WorkflowInstance
from .services import ensure_workflow_instance
from .tests_graph import GraphFixture


BUNDLED_LIBRARY = Path(__file__).with_name("config")


def copy_library(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for source in BUNDLED_LIBRARY.glob("*.json"):
        copyfile(source, directory / source.name)


class WorkflowLibraryTests(SimpleTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = self.root / "library"
        copy_library(self.library)
        self.config = self.library / "workflows.json"
        override = self.settings(
            BASE_DIR=self.root / "app",
            DAT_WORKFLOW_CONFIG=self.config,
            DAT_WORKFLOW_JSON="",
        )
        override.enable()
        self.addCleanup(override.disable)

    def select(self, filename):
        self.config.write_text(json.dumps({"active_workflow": filename}), encoding="utf-8")

    def test_selected_file_changes_without_loading_inactive_drafts(self):
        (self.library / "draft.json").write_text("unfinished JSON", encoding="utf-8")
        full = load_graph()
        self.select("parallel-review.json")
        parallel = load_graph()
        self.assertNotEqual(full["nodes"], parallel["nodes"])
        self.assertEqual(parallel, load_graph(self.library / "parallel-review.json"))
        self.select("dat-validation.json")
        self.assertEqual(load_graph(), full)

    def test_bundled_default_loads_without_creating_local_configuration(self):
        with self.settings(DAT_WORKFLOW_CONFIG=""):
            self.assertEqual(load_graph(), load_graph(BUNDLED_LIBRARY / "dat-validation.json"))
        self.assertFalse((self.root / "app" / "conf").exists())

    def test_local_configuration_overrides_bundled_selection(self):
        local = self.root / "app" / "conf"
        copy_library(local)
        (local / "workflows.json").write_text('{"active_workflow": "parallel-review.json"}', encoding="utf-8")
        with self.settings(DAT_WORKFLOW_CONFIG=""):
            self.assertEqual(load_graph(), load_graph(local / "parallel-review.json"))

    def test_explicit_and_legacy_paths_override_library_selection(self):
        self.config.write_text("broken selection", encoding="utf-8")
        full = load_graph(self.library / "dat-validation.json")
        with self.settings(DAT_WORKFLOW_JSON=self.library / "parallel-review.json"):
            self.assertEqual(load_graph(), load_graph(self.library / "parallel-review.json"))
            self.assertEqual(load_graph(self.library / "dat-validation.json"), full)

    def test_rejects_invalid_selector_and_unsafe_filenames(self):
        for config in (
            [], {}, {"active_workflow": "dat-validation.json", "unknown": True},
            {"active_workflow": None}, {"active_workflow": 1},
            {"active_workflow": "../outside.json"},
            {"active_workflow": str(self.library / "dat-validation.json")},
            {"active_workflow": "nested/dat-validation.json"},
            {"active_workflow": "nested\\dat-validation.json"},
            {"active_workflow": "dat-validation"},
        ):
            with self.subTest(config=config):
                self.config.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaises(WorkflowConfigurationError):
                    load_graph()

    def test_missing_config_or_selected_file_fails_without_fallback(self):
        self.select("missing.json")
        with self.assertRaisesMessage(WorkflowConfigurationError, "Cannot load workflow JSON"):
            load_graph()
        self.config.unlink()
        with self.assertRaisesMessage(WorkflowConfigurationError, "Cannot load workflow library configuration"):
            load_graph()

    def test_invalid_local_config_fails_without_bundled_fallback(self):
        local = self.root / "app" / "conf"
        local.mkdir(parents=True)
        (local / "workflows.json").write_text("broken JSON", encoding="utf-8")
        with self.settings(DAT_WORKFLOW_CONFIG=""):
            with self.assertRaises(WorkflowConfigurationError):
                load_graph()

    def test_duplicate_keys_rejected_in_selector_and_workflow(self):
        self.config.write_text('{"active_workflow":"dat-validation.json","active_workflow":"parallel-review.json"}', encoding="utf-8")
        with self.assertRaisesMessage(WorkflowConfigurationError, "Duplicate JSON key"):
            load_graph()
        self.select("duplicate.json")
        (self.library / "duplicate.json").write_text('{"nodes":[],"nodes":[]}', encoding="utf-8")
        with self.assertRaisesMessage(WorkflowConfigurationError, "Duplicate JSON key"):
            load_graph()

    def test_selected_graph_is_validated_and_size_is_limited_in_bytes(self):
        self.select("invalid.json")
        graph = self.library / "invalid.json"
        graph.write_text("{}", encoding="utf-8")
        with self.assertRaises(WorkflowConfigurationError):
            load_graph()
        graph.write_text('"' + "é" * (512 * 1024) + '"', encoding="utf-8")
        with self.assertRaisesMessage(WorkflowConfigurationError, "exceeds 1 MiB"):
            load_graph()

    def test_symlink_cannot_escape_library_directory(self):
        outside = self.root / "outside.json"
        copyfile(BUNDLED_LIBRARY / "dat-validation.json", outside)
        (self.library / "escape.json").symlink_to(outside)
        self.select("escape.json")
        with self.assertRaisesMessage(WorkflowConfigurationError, "inside the library directory"):
            load_graph()


class WorkflowLibraryPublicationTests(GraphFixture, TestCase):
    def test_selection_publishes_for_new_dats_and_keeps_existing_contracts(self):
        original = ensure_workflow_instance(self.dat).definition_version
        version_count = original.workflow.versions.count()
        editors = list(self.dat.section_participants.values_list("pk", "user_id"))
        with TemporaryDirectory() as temporary:
            library = Path(temporary)
            copy_library(library)
            config = library / "workflows.json"
            config.write_text('{"active_workflow": "parallel-review.json"}', encoding="utf-8")
            with override_settings(DAT_WORKFLOW_CONFIG=config, DAT_WORKFLOW_JSON=""):
                call_command("sync_workflows", "--check", stdout=StringIO())
                self.assertEqual(Workflow.objects.get(code="dat-validation").active_version_id, original.pk)
                call_command("sync_workflows", stdout=StringIO())
                selected = Workflow.objects.get(code="dat-validation").active_version
                self.assertNotEqual(selected.pk, original.pk)
                self.assertEqual(selected.specification, load_graph(library / "parallel-review.json"))
                created = DAT.objects.create(reference="LIBRARY-2", title="Parallel review", application=self.dat.application, owner=self.dat.owner)
                self.assertEqual(ensure_workflow_instance(created).definition_version_id, selected.pk)
                self.assertEqual(WorkflowInstance.objects.get(object_id=str(self.dat.pk), workflow__code="dat-validation").definition_version_id, original.pk)
                self.assertEqual(list(self.dat.section_participants.values_list("pk", "user_id")), editors)
                call_command("sync_workflows", stdout=StringIO())
                self.assertEqual(Workflow.objects.get(code="dat-validation").versions.count(), version_count + 1)
                config.write_text('{"active_workflow": "dat-validation.json"}', encoding="utf-8")
                call_command("sync_workflows", stdout=StringIO())
                self.assertEqual(Workflow.objects.get(code="dat-validation").active_version_id, original.pk)
                self.assertEqual(WorkflowInstance.objects.get(object_id=str(created.pk), workflow__code="dat-validation").definition_version_id, selected.pk)

    def test_selected_parallel_workflow_executes_both_reviews_before_final(self):
        with TemporaryDirectory() as temporary:
            library = Path(temporary)
            copy_library(library)
            config = library / "workflows.json"
            config.write_text('{"active_workflow": "parallel-review.json"}', encoding="utf-8")
            with override_settings(DAT_WORKFLOW_CONFIG=config, DAT_WORKFLOW_JSON=""):
                call_command("sync_workflows", stdout=StringIO())
        call_command("migrate_workflow_instances", "dat-validation", "--restart-active", "--object-id", str(self.dat.pk), "--apply", stdout=StringIO())
        self.dat._workflow_instance_cache = {}
        self.act("need", "porteur-demande", "submit")
        attempts = reconcile_graph(self.dat)
        self.assertEqual(attempts["architecture"].status, "ready")
        self.assertEqual(attempts["security"].status, "ready")
        self.act("architecture", "architecte-technique")
        self.assertEqual(reconcile_graph(self.dat)["final"].status, "waiting")
        self.act("security", "rssi")
        self.assertEqual(reconcile_graph(self.dat)["final"].status, "ready")
        self.act("final", "comite-validation")
        self.dat.refresh_from_db()
        self.assertEqual(self.dat.status, "valider")

    def test_bad_selection_does_not_replace_active_database_version(self):
        original = ensure_workflow_instance(self.dat).definition_version_id
        with TemporaryDirectory() as temporary:
            config = Path(temporary) / "workflows.json"
            config.write_text('{"active_workflow": "missing.json"}', encoding="utf-8")
            with override_settings(DAT_WORKFLOW_CONFIG=config, DAT_WORKFLOW_JSON=""):
                with self.assertRaises(CommandError):
                    call_command("sync_workflows", stdout=StringIO())
        self.assertEqual(Workflow.objects.get(code="dat-validation").active_version_id, original)
