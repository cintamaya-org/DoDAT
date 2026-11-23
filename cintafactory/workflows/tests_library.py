# SPDX-License-Identifier: AGPL-3.0-only
"""Workflow library selection, atomic cutover and historical approvals."""

import json
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from django.apps import apps
from django.core.management import CommandError, call_command
from django.db import connection, connections, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from dat.models import DAT

from .exceptions import WorkflowConfigurationError
from .graph_config import load_graph
from .graph_engine import reconcile_graph
from .models import Workflow, WorkflowInstance
from .signals import sync_workflows_on_migrate
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
    def setUp(self):
        super().setUp()
        self.library = Path(self.enterContext(TemporaryDirectory()))
        copy_library(self.library)
        self.config = self.library / "workflows.json"
        self.enterContext(override_settings(DAT_WORKFLOW_CONFIG=self.config, DAT_WORKFLOW_JSON=""))

    def select(self, filename):
        self.config.write_text(json.dumps({"active_workflow": filename}), encoding="utf-8")

    def publish(self, *options):
        output = StringIO()
        call_command("sync_workflows", *options, stdout=output)
        return output.getvalue()

    def test_default_reuses_live_contract_and_executes_first_version_milestone(self):
        original = ensure_workflow_instance(self.dat).definition_version
        self.assertEqual(original.checksum, "d133c2ce1369c9339c82cb1f11fceb5aef2788d91c1b678e51caf194dee5206b")
        self.reach_ssi()
        attempts = reconcile_graph(self.dat)
        self.assertEqual(attempts["first_version"].status, "completed")
        self.assertEqual(attempts["ssi"].status, "ready")
        self.assertIn("restarted: 0", self.publish())
        self.assertEqual(reconcile_graph(self.dat)["ssi"].pk, attempts["ssi"].pk)
        self.act("ssi", "rssi")
        self.act("hosting", "infra-exploitation")
        self.act("final", "comite-validation")
        self.dat.refresh_from_db()
        self.assertEqual(self.dat.status, "valider")

    def test_revising_a_closed_dat_after_a_switch_reopens_on_the_active_graph(self):
        self.reach_ssi()
        self.act("ssi", "rssi")
        self.act("hosting", "infra-exploitation")
        final = self.act("final", "comite-validation")
        self.select("parallel-review.json")
        self.assertIn("restarted: 0", self.publish())
        self.dat.title = "Revised dossier"
        self.dat.save(update_fields=["title"])
        self.dat.refresh_from_db()
        instance = ensure_workflow_instance(self.dat)
        self.assertEqual(instance.definition_version_id, instance.workflow.active_version_id)
        self.assertEqual(self.dat.status, "nouvelle_demande")
        self.assertTrue(instance.node_attempts.filter(is_current=True, node_id="architecture").exists())
        self.assertFalse(instance.node_attempts.get(pk=final.attempt_id).is_current)
        self.assertEqual(instance.transition_events.get(event="workflow-restarted").from_state, "valider")

    def test_open_legacy_dat_restarts_even_when_selected_graph_is_already_active(self):
        from .definitions import WORKFLOW_DEFINITIONS
        from .sync import sync_workflow_definitions
        from .graph_config import publish_graph

        sync_workflow_definitions(WORKFLOW_DEFINITIONS)
        legacy_dat = DAT.objects.create(reference="LIBRARY-LEGACY", title="Legacy dossier", status="en_cours",
                                        application=self.dat.application, owner=self.dat.owner)
        legacy = ensure_workflow_instance(legacy_dat)
        previous_version = legacy.definition_version.version
        active = publish_graph(self.spec)
        self.assertIn("restarted: 1", self.publish())
        legacy.refresh_from_db()
        legacy_dat.refresh_from_db()
        self.assertEqual(legacy.definition_version_id, active.pk)
        self.assertEqual(legacy_dat.status, "nouvelle_demande")
        self.assertEqual(legacy.transition_events.get(event="workflow-restarted").metadata["from_version"], previous_version)
        self.assertTrue(legacy.node_attempts.filter(is_current=True, node_id="need").exists())

    def test_publish_added_file_restarts_open_dats_and_keeps_closed_history(self):
        instance = ensure_workflow_instance(self.dat)
        original = instance.definition_version
        version_count = original.workflow.versions.count()
        old_decision = self.act("need", "porteur-demande", "submit")
        editors = list(self.dat.section_participants.values_list("pk", "user_id"))
        closed = DAT.objects.create(reference="LIBRARY-CLOSED", title="Closed", status="valider",
                                    application=self.dat.application, owner=self.dat.owner)
        self.assertFalse(WorkflowInstance.objects.filter(object_id=str(closed.pk)).exists())
        copyfile(self.library / "parallel-review.json", self.library / "new-circuit.json")
        self.select("new-circuit.json")
        self.assertIn("to restart: 1", self.publish("--check"))
        self.assertEqual(Workflow.objects.get(code="dat-validation").active_version_id, original.pk)
        self.assertFalse(WorkflowInstance.objects.filter(object_id=str(closed.pk)).exists())

        self.assertIn("restarted: 1", self.publish())
        instance.refresh_from_db()
        self.dat.refresh_from_db()
        selected = instance.definition_version
        self.assertEqual(selected.specification, load_graph(self.library / "new-circuit.json"))
        self.assertEqual(self.dat.status, "nouvelle_demande")
        old_decision.attempt.refresh_from_db()
        self.assertFalse(old_decision.attempt.is_current)
        self.assertTrue(old_decision.attempt.decisions.filter(pk=old_decision.pk).exists())
        self.assertFalse(instance.node_attempts.filter(is_current=True, decisions__isnull=False).exists())
        restart = instance.transition_events.get(event="workflow-restarted")
        self.assertEqual(restart.metadata["from_version"], original.version)
        self.assertEqual(restart.metadata["to_version"], selected.version)
        self.assertEqual(list(self.dat.section_participants.values_list("pk", "user_id")), editors)
        archived = ensure_workflow_instance(closed)
        self.assertEqual((archived.definition_version_id, archived.current_state), (original.pk, "valider"))
        self.assertTrue(archived.data["archived"])
        created = DAT.objects.create(reference="LIBRARY-2", title="Parallel review",
                                     application=self.dat.application, owner=self.dat.owner)
        self.assertEqual(ensure_workflow_instance(created).definition_version_id, selected.pk)
        self.assertIn("restarted: 0", self.publish())
        self.assertEqual(instance.transition_events.filter(event="workflow-restarted").count(), 1)
        self.select("dat-validation.json")
        self.assertIn("restarted: 2", self.publish())
        self.assertEqual(original.workflow.versions.count(), version_count + 1)
        self.assertFalse(WorkflowInstance.objects.filter(workflow=original.workflow).exclude(definition_version=original).exists())
        self.assertFalse(archived.transition_events.filter(event="workflow-restarted").exists())

    def test_failed_restart_rolls_back_selection_attempts_and_events(self):
        instance = ensure_workflow_instance(self.dat)
        original = instance.definition_version_id
        old_decision = self.act("need", "porteur-demande", "submit")
        self.select("parallel-review.json")
        with patch("workflows.graph_engine._reconcile", side_effect=RuntimeError("restart failed")):
            with self.assertRaisesMessage(RuntimeError, "restart failed"):
                self.publish()
        self.assertEqual(Workflow.objects.get(code="dat-validation").active_version_id, original)
        instance.refresh_from_db()
        self.assertEqual(instance.definition_version_id, original)
        self.assertTrue(instance.node_attempts.get(pk=old_decision.attempt_id).is_current)
        self.assertFalse(instance.transition_events.filter(event="workflow-restarted").exists())

    def test_post_migrate_leaves_published_workflow_active_after_selector_edit(self):
        original = ensure_workflow_instance(self.dat).definition_version_id
        self.select("parallel-review.json")
        sync_workflows_on_migrate(sender=apps.get_app_config("workflows"))
        self.assertEqual(Workflow.objects.get(code="dat-validation").active_version_id, original)

    def test_selected_parallel_workflow_executes_both_reviews_before_final(self):
        self.select("parallel-review.json")
        self.publish()
        self.dat.refresh_from_db()
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
        self.select("missing.json")
        with self.assertRaises(CommandError):
            self.publish()
        self.assertEqual(Workflow.objects.get(code="dat-validation").active_version_id, original)


class WorkflowSwitchConcurrencyTests(GraphFixture, TransactionTestCase):
    def test_dat_created_during_switch_pins_committed_selection(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks.")
        binding = Event()

        def create_dat():
            # Signal when the new DAT has reached its normal on-commit binding.
            def observe_lock(execute, sql, params, many, context):
                if f'FROM "{Workflow._meta.db_table}"' in sql and "FOR UPDATE" in sql and "JOIN" not in sql:
                    binding.set()
                return execute(sql, params, many, context)

            try:
                with connections["default"].execute_wrapper(observe_lock):
                    dat = DAT.objects.create(reference="LIBRARY-RACE", title="During switch",
                                             application_id=self.dat.application_id, owner_id=self.dat.owner_id)
                    return ensure_workflow_instance(dat).definition_version_id
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                call_command("sync_workflows", "--json", BUNDLED_LIBRARY / "parallel-review.json", stdout=StringIO())
                selected = Workflow.objects.get(code="dat-validation").active_version_id
                new_dat = pool.submit(create_dat)
                self.assertTrue(binding.wait(timeout=10))
            self.assertEqual(new_dat.result(timeout=10), selected)
