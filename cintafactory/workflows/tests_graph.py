# SPDX-License-Identifier: AGPL-3.0-only
"""Behavioral acceptance tests for the configured DAT approval circuit."""

from copy import deepcopy
from io import StringIO
from pathlib import Path
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.core.management import call_command
from django.urls import reverse

from dat.models import Application, DAT, DATParticipant, DATSectionParticipant, DATSectionResponsible
from dat.sections import ensure_default_sections
from users.models import BusinessDirection, Role, TechnicalDirection

from .exceptions import WorkflowConfigurationError, WorkflowPermissionDenied
from .graph_config import load_graph, publish_graph
from .graph_engine import WorkflowConflict, act_on_node, graph_context, reconcile_graph
from .graph_schema import validate_graph
from .models import WorkflowApprovalDecision
from .services import ensure_workflow_instance, migrate_workflow_instances


class GraphFixture:
    workflow_file = "dat-validation.json"

    def setUp(self):
        notifications = mock.patch("cintafactory.notifications.external.dispatch_external_notification", return_value=[])
        notifications.start()
        self.addCleanup(notifications.stop)
        self.spec = load_graph(Path(__file__).with_name("config") / self.workflow_file)
        if not get_user_model().objects.exists():
            get_user_model().objects.create_user(username="graph-bootstrap")
        direction, _ = TechnicalDirection.objects.get_or_create(slug="graph-review", defaults={"name": "Graph review"})
        self.actors = {}
        self.roles = {}
        for slug in ("porteur-demande", "architecte-referent", "architecte-technique", "urbaniste", "analyste-secu", "rssi", "infra-exploitation", "comite-validation"):
            self.roles[slug], _ = Role.objects.get_or_create(slug=slug, defaults={"name": slug, "technical_direction": direction})
            self.actors[slug] = get_user_model().objects.create_user(username=f"graph-{slug}", role=self.roles[slug])
        publish_graph(self.spec)
        business, _ = BusinessDirection.objects.get_or_create(name="Graph business", defaults={"slug": "graph-business"})
        self.dat = DAT.objects.create(reference="GRAPH-1", title="Review circuit", application=Application.objects.create(code="graph-app", name="Graph app", business_direction=business), owner=self.actors["porteur-demande"])
        ensure_workflow_instance(self.dat)
        ensure_default_sections(self.dat)
        for slug, actor in self.actors.items():
            DATParticipant.objects.create(dat=self.dat, user=actor, role=self.roles[slug])
        self.need = self.dat.sections.get(metadata__slug="besoins")
        DATSectionParticipant.objects.update_or_create(section=self.need, defaults={"dat": self.dat, "user": self.actors["porteur-demande"]})

    def act(self, node_id, role, outcome="approve", **kwargs):
        attempt = reconcile_graph(self.dat)[node_id]
        return act_on_node(self.dat, node_id, self.actors[role], outcome=outcome, attempt_id=attempt.pk, request_id=uuid4(), **kwargs)

    def prepare(self):
        self.act("need", "porteur-demande", "submit")
        self.act("prerequisites", "architecte-referent")
        self.act("preparation", "architecte-technique")

    def reach_ssi(self):
        self.prepare()
        self.act("urbanisation", "urbaniste")
        self.act("cyber", "analyste-secu")

    def configure(self, nodes):
        spec = deepcopy(self.spec)
        spec["nodes"] = nodes
        spec["complete_when"] = {"all": [nodes[-1]["id"]]}
        publish_graph(spec)
        migrate_workflow_instances(workflow_code="dat-validation", restart_active=True, object_ids=[self.dat.pk])
        self.dat._workflow_instance_cache = {}

    def review_nodes(self, target, approvers):
        return [
            {"id": "review", "type": "approval", "target": target, "approvers": approvers},
            {"id": "final", "type": "approval", "target": {"dat": True}, "after": ["review"], "approvers": {"all_roles": ["comite-validation"]}},
        ]


class ApprovalGraphTests(GraphFixture, TestCase):
    workflow_file = "dat-validation-refusal-routes.json"

    def test_diagram_parallel_join_and_explicit_final_approval(self):
        self.prepare()
        attempts = reconcile_graph(self.dat)
        self.assertEqual(attempts["urbanisation"].status, "ready")
        self.assertEqual(attempts["cyber"].status, "ready")
        self.act("cyber", "analyste-secu")
        self.assertEqual(reconcile_graph(self.dat)["ssi"].status, "waiting")
        self.act("urbanisation", "urbaniste")
        attempts = reconcile_graph(self.dat)
        self.assertNotIn("first_version", attempts)
        self.assertEqual(attempts["ssi"].status, "ready")
        self.act("ssi", "rssi")
        self.act("hosting", "infra-exploitation")
        self.dat.refresh_from_db()
        self.assertNotEqual(self.dat.status, "valider")
        self.act("final", "comite-validation")
        self.dat.refresh_from_db()
        self.assertEqual(self.dat.status, "valider")

    def test_approver_does_not_need_or_gain_section_edit_permission(self):
        self.reach_ssi()
        section = self.dat.sections.get(metadata__slug="cybersecurite")
        self.assertFalse(section.can_user_edit(self.actors["rssi"]))
        self.act("ssi", "rssi")
        self.assertFalse(section.can_user_edit(self.actors["rssi"]))

    def test_editor_and_admin_cannot_replace_required_dat_role(self):
        self.prepare()
        actor = self.actors["porteur-demande"]
        actor.is_staff = True
        actor.save()
        with self.assertRaises(WorkflowPermissionDenied):
            self.act("cyber", "porteur-demande")

    def test_ssi_ko_restarts_preparation_and_preserves_prerequisites(self):
        self.reach_ssi()
        before = reconcile_graph(self.dat)
        self.act("ssi", "rssi", "request_changes", comment="Architecture à corriger")
        after = reconcile_graph(self.dat)
        self.assertEqual(after["prerequisites"].pk, before["prerequisites"].pk)
        self.assertEqual(after["preparation"].status, "ready")
        self.assertNotEqual(after["cyber"].pk, before["cyber"].pk)
        self.assertEqual(after["hosting"].status, "waiting")
        self.assertTrue(WorkflowApprovalDecision.objects.filter(attempt=before["ssi"], outcome="request_changes").exists())

    def test_edit_invalidates_only_affected_parallel_branch(self):
        self.reach_ssi()
        before = reconcile_graph(self.dat)
        part = self.dat.sections.get(metadata__slug="urbanisme").sub_sections.first().parts.first()
        part.update_value("Urbanisation modifiée")
        after = reconcile_graph(self.dat)
        self.assertNotEqual(after["urbanisation"].pk, before["urbanisation"].pk)
        self.assertEqual(after["cyber"].pk, before["cyber"].pk)
        self.assertEqual(after["cyber"].status, "completed")
        self.assertEqual(after["ssi"].status, "waiting")

    def test_role_reassignment_supersedes_old_decisions(self):
        self.reach_ssi()
        self.act("ssi", "rssi")
        before = reconcile_graph(self.dat)
        replacement = get_user_model().objects.create_user(username="new-rssi", role=self.roles["rssi"])
        assignment = self.dat.participants.get(role=self.roles["rssi"])
        assignment.user = replacement
        assignment.save(update_fields=["user"])
        after = reconcile_graph(self.dat)
        self.assertNotEqual(after["ssi"].pk, before["ssi"].pk)
        self.assertEqual(after["ssi"].status, "ready")
        self.assertEqual(after["hosting"].status, "waiting")
        with self.assertRaises(WorkflowPermissionDenied):
            self.act("ssi", "rssi")

    def test_missing_assignee_blocks_required_approval(self):
        self.prepare()
        self.dat.participants.get(role=self.roles["urbaniste"]).delete()
        attempt = reconcile_graph(self.dat)["urbanisation"]
        self.assertEqual(attempt.status, "waiting")
        self.assertIn({"code": "missing_assignee", "role": "urbaniste"}, attempt.blockers)

    def test_retry_is_idempotent_and_stale_attempt_cannot_approve(self):
        self.prepare()
        attempt = reconcile_graph(self.dat)["urbanisation"]
        request_id = uuid4()
        first = act_on_node(self.dat, "urbanisation", self.actors["urbaniste"], outcome="approve", attempt_id=attempt.pk, request_id=request_id)
        second = act_on_node(self.dat, "urbanisation", self.actors["urbaniste"], outcome="approve", attempt_id=attempt.pk, request_id=request_id)
        self.assertEqual(first.pk, second.pk)
        part = self.dat.sections.get(metadata__slug="urbanisme").sub_sections.first().parts.first()
        part.update_value("Changed after review")
        with self.assertRaises(WorkflowConflict):
            act_on_node(self.dat, "urbanisation", self.actors["urbaniste"], outcome="approve", attempt_id=attempt.pk, request_id=uuid4())

    def test_review_page_and_json_endpoint_render(self):
        self.client.force_login(self.actors["porteur-demande"])
        url = reverse("workflows:graph_detail", args=[self.dat.pk])
        for html_url in (url, reverse("dat:my_detail", args=[self.dat.pk]) + "?section=validation"):
            with self.subTest(html_url=html_url):
                page = self.client.get(html_url)
                self.assertContains(page, "Approbations du dossier")
                self.assertContains(page, "workflow_review.js")
                self.assertContains(page, 'name="review-layout" value="stepper" checked')
                self.assertContains(page, 'data-node="ssi"', count=2)
                self.assertContains(page, 'data-from="cyber" data-to="ssi"', count=2)
                self.assertContains(page, 'data-layout="lanes"')
                self.assertContains(page, 'data-step="ssi" data-after="urbanisation cyber"')
                self.assertContains(page, 'value="submit"')
                self.assertNotContains(page, 'value="approve"')
        self.client.force_login(self.actors["urbaniste"])
        self.assertNotContains(self.client.get(url), 'name="outcome"')
        response = self.client.get(url, {"format": "json"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {node["id"] for node in response.json()["nodes"]},
            {node["id"] for node in self.spec["nodes"]},
        )

    def test_archived_validation_shows_pinned_graph_without_creating_attempts(self):
        instance = ensure_workflow_instance(self.dat)
        instance.node_attempts.all().delete()
        instance.data = {"archived": True}
        instance.current_state = "valider"
        instance.save(update_fields=["data", "current_state"])
        DAT.objects.filter(pk=self.dat.pk).update(status="valider")
        self.client.force_login(self.actors["porteur-demande"])
        page = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]), {"section": "validation"})
        self.assertContains(page, 'data-node="need" data-tone="unknown"', count=2)
        self.assertContains(page, 'data-from="cyber" data-to="ssi"', count=2)
        self.assertNotContains(page, 'name="outcome"')
        self.assertFalse(instance.node_attempts.exists())

    def test_historical_validation_renders_its_pinned_visualization(self):
        from .definitions import WORKFLOW_DEFINITIONS
        from .sync import sync_workflow_definitions
        instance = ensure_workflow_instance(self.dat)
        sync_workflow_definitions(WORKFLOW_DEFINITIONS)
        instance.workflow.refresh_from_db()
        instance.definition_version = instance.workflow.active_version
        instance.save(update_fields=["definition_version"])
        publish_graph(self.spec)
        self.client.force_login(self.actors["porteur-demande"])
        page = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]), {"section": "validation"})
        self.assertContains(page, 'class="review-diagram"')
        self.assertContains(page, 'data-node="historique-1"', count=2)
        self.assertContains(page, "Suivi des sections")
        self.assertNotContains(page, 'data-node="ssi"')
        self.assertNotContains(page, 'name="outcome"')

    def test_definition_versions_are_pinned(self):
        instance = ensure_workflow_instance(self.dat)
        old = instance.definition_version_id
        spec = deepcopy(self.spec)
        spec["nodes"][1]["approvers"] = {"all_roles": ["rssi"]}
        publish_graph(spec)
        instance.refresh_from_db()
        self.assertEqual(instance.definition_version_id, old)

    def test_all_roles_and_any_role_have_different_completion_rules(self):
        for mode in ("all_roles", "any_role"):
            with self.subTest(mode=mode):
                self.configure(self.review_nodes({"section": "architecture"}, {mode: ["rssi", "architecte-referent"]}))
                self.act("review", "rssi")
                attempts = reconcile_graph(self.dat)
                if mode == "all_roles":
                    self.assertEqual(attempts["review"].status, "ready")
                    self.assertEqual(attempts["final"].status, "waiting")
                    self.act("review", "architecte-referent")
                self.assertEqual(reconcile_graph(self.dat)["final"].status, "ready")

    def test_part_approval_tracks_exact_scope_and_retains_old_content(self):
        from dat.models import DATPart
        part = DATPart.objects.filter(sub_section__section__dat=self.dat, sub_section__section__metadata__slug="architecture").select_related("sub_section").first()
        part.update_value("Original content")
        self.configure(self.review_nodes({"section": "architecture", "subsection": part.sub_section.slug, "part": part.key}, {"all_roles": ["rssi"]}))
        decision = self.act("review", "rssi")
        old_attempt = decision.attempt_id
        sibling = DATPart.objects.filter(sub_section__section__dat=self.dat, sub_section__section__metadata__slug="architecture").exclude(pk=part.pk).first()
        sibling.update_value("Unrelated content")
        self.assertEqual(reconcile_graph(self.dat)["review"].pk, old_attempt)
        part.update_value("Revised content")
        self.assertNotEqual(reconcile_graph(self.dat)["review"].pk, old_attempt)
        decision.refresh_from_db()
        self.assertEqual(decision.snapshot.content["parts"][0]["value"], "Original content")

    def test_arbitrary_registered_dat_role_can_approve_without_edit_access(self):
        from dat.forms import DATForm
        role = Role.objects.create(slug="legal-reviewer", name="Legal reviewer", technical_direction=self.roles["rssi"].technical_direction)
        reviewer = get_user_model().objects.create_user(username="legal-reviewer", role=role)
        DATParticipant.objects.create(dat=self.dat, role=role, user=reviewer)
        self.configure(self.review_nodes({"section": "besoins"}, {"all_roles": [role.slug]}))
        form = DATForm(instance=self.dat, user=self.actors["porteur-demande"])
        self.assertIn("participant_legal_reviewer", form.fields)
        before = self.need.can_user_edit(reviewer)
        attempt = reconcile_graph(self.dat)["review"]
        act_on_node(self.dat, "review", reviewer, outcome="approve", attempt_id=attempt.pk, request_id=uuid4())
        self.assertFalse(before)
        self.assertFalse(self.need.can_user_edit(reviewer))

    def test_required_content_readiness_is_enforced(self):
        from dat.models import DATPart
        part = DATPart.objects.filter(sub_section__section__dat=self.dat, sub_section__section__metadata__slug="architecture").select_related("sub_section").first()
        part.required = True
        part.save(update_fields=["required"])
        part.update_value(None)
        nodes = self.review_nodes({"section": "architecture", "subsection": part.sub_section.slug, "part": part.key}, {"all_roles": ["rssi"]})
        nodes[0]["ready_when"] = {"all": [{"required_content_complete": True}, {"not": {"not": {"required_content_complete": True}}}]}
        self.configure(nodes)
        self.assertEqual(reconcile_graph(self.dat)["review"].status, "waiting")
        part.update_value("Complete")
        self.assertEqual(reconcile_graph(self.dat)["review"].status, "ready")

    def test_restart_dry_run_then_apply_preserves_decisions_and_edit_assignments(self):
        self.prepare()
        initial = ensure_workflow_instance(self.dat)
        previous_version = initial.definition_version_id
        assignment = DATSectionParticipant.objects.get(section=self.need)
        spec = deepcopy(self.spec)
        spec["name"] = "Updated workflow"
        publish_graph(spec)
        output = StringIO()
        call_command("migrate_workflow_instances", "dat-validation", "--restart-active", "--object-id", str(self.dat.pk), stdout=output)
        initial.refresh_from_db()
        self.assertEqual(initial.definition_version_id, previous_version)
        call_command("migrate_workflow_instances", "dat-validation", "--restart-active", "--object-id", str(self.dat.pk), "--apply", stdout=output)
        initial.refresh_from_db()
        self.assertNotEqual(initial.definition_version_id, previous_version)
        self.assertEqual(initial.current_state, "nouvelle_demande")
        self.assertEqual(DATSectionParticipant.objects.get(section=self.need).pk, assignment.pk)
        self.assertEqual(WorkflowApprovalDecision.objects.filter(attempt__instance=initial).count(), 3)
        self.assertEqual(reconcile_graph(self.dat)["need"].status, "ready")

    def test_api_enforces_dat_visibility_permissions_and_stale_attempt(self):
        self.prepare()
        attempt = reconcile_graph(self.dat)["cyber"]
        url = reverse("workflows:node_action", args=[self.dat.pk, "cyber"])
        data = {"outcome": "approve", "attempt_id": str(attempt.pk), "request_id": str(uuid4())}
        self.client.force_login(self.actors["porteur-demande"])
        self.assertEqual(self.client.post(url, data, content_type="application/json").status_code, 403)
        self.client.force_login(self.actors["analyste-secu"])
        data["attempt_id"] = str(uuid4())
        self.assertEqual(self.client.post(url, data, content_type="application/json").status_code, 409)
        data["attempt_id"] = str(attempt.pk)
        self.assertEqual(self.client.post(url, data, content_type="application/json").status_code, 200)
        outsider = get_user_model().objects.create_user(username="outside-review")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse("workflows:graph_detail", args=[self.dat.pk])).status_code, 404)

    def test_dat_deletion_cascades_graph_evidence(self):
        from .models import WorkflowInstance
        self.prepare()
        instance = ensure_workflow_instance(self.dat)
        self.dat.delete()
        self.assertFalse(WorkflowInstance.objects.filter(pk=instance.pk).exists())

    def test_reviewed_attachment_copy_survives_source_deletion(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import InMemoryStorage
        from dat.models import DATSectionAttachment

        storage = InMemoryStorage()
        storage.save("attachments/reviewed.txt", ContentFile(b"Reviewed version"))
        section = self.dat.sections.get(metadata__slug="cybersecurite")
        with mock.patch("workflows.graph_artifacts.SeaweedFSStorage", return_value=storage):
            attachment = DATSectionAttachment.objects.create(section=section, storage_path="attachments/reviewed.txt", original_name="reviewed.txt", display_name="reviewed.txt", extension=".txt", size=16)
            self.prepare()
            decision = self.act("cyber", "analyste-secu")
            artifact = decision.snapshot.content["artifacts"][0]
            storage.delete(attachment.storage_path)
            attachment.delete()
        with storage.open(artifact["path"], "rb") as saved:
            self.assertEqual(saved.read(), b"Reviewed version")
        self.assertNotEqual(reconcile_graph(self.dat)["cyber"].pk, decision.attempt_id)

    def test_terminal_export_metadata_can_update_without_reopening_review(self):
        self.reach_ssi()
        self.act("ssi", "rssi")
        self.act("hosting", "infra-exploitation")
        self.act("final", "comite-validation")
        self.dat.refresh_from_db()
        self.dat.pdf_export_in_progress = True
        self.dat.save(update_fields=["pdf_export_in_progress"])
        self.dat.refresh_from_db()
        self.assertEqual(self.dat.status, "valider")
        self.assertTrue(self.dat.pdf_export_in_progress)

    def test_authorized_metadata_edit_reopens_only_approvals_covering_dat_content(self):
        self.reach_ssi()
        self.act("ssi", "rssi")
        self.act("hosting", "infra-exploitation")
        final_decision = self.act("final", "comite-validation")
        self.dat.title = "New dossier title"
        self.dat.save(update_fields=["title"])
        attempts = reconcile_graph(self.dat)
        self.assertEqual(attempts["ssi"].status, "completed")
        self.assertEqual(attempts["hosting"].status, "completed")
        self.assertNotEqual(attempts["final"].pk, final_decision.attempt_id)
        self.assertEqual(attempts["final"].status, "ready")
        self.dat.refresh_from_db()
        self.assertEqual(self.dat.status, "en_cours")

    def test_reassigning_approver_preserves_old_editors_access(self):
        from dat.permissions import filter_dat_queryset_for_user
        self.reach_ssi()
        section = self.dat.sections.get(metadata__slug="cybersecurite")
        old = self.actors["analyste-secu"]
        DATSectionParticipant.objects.create(dat=self.dat, section=section, user=old)
        replacement = get_user_model().objects.create_user(username="new-cyber-approver", role=self.roles["analyste-secu"])
        assignment = self.dat.participants.get(role=self.roles["analyste-secu"])
        assignment.user = replacement
        assignment.save(update_fields=["user"])
        self.assertTrue(section.can_user_edit(old))
        self.assertFalse(section.can_user_edit(replacement))
        self.assertTrue(filter_dat_queryset_for_user(DAT.objects.filter(pk=self.dat.pk), old).exists())
        with self.assertRaises(WorkflowPermissionDenied):
            self.act("cyber", "analyste-secu")

    def test_terminal_refusal_cancels_remaining_reviews(self):
        nodes = deepcopy(self.spec["nodes"])
        next(node for node in nodes if node["id"] == "cyber")["on_reject"] = {"mode": "close"}
        self.configure(nodes)
        self.prepare()
        self.act("cyber", "analyste-secu", "reject", comment="Refus motivé")
        attempts = reconcile_graph(self.dat)
        self.assertEqual(attempts["cyber"].status, "rejected")
        self.assertEqual(attempts["urbanisation"].status, "cancelled")
        self.assertFalse(graph_context(self.dat, self.actors["urbaniste"])["has_actions"])
        self.dat.title = "Updated after refusal"
        self.dat.save(update_fields=["title"])
        self.dat.refresh_from_db()
        self.assertEqual(self.dat.status, "refuse")

    def test_prerequisite_refusal_returns_to_need(self):
        with self.assertRaises(WorkflowPermissionDenied):
            self.act("need", "porteur-demande", "reject", comment="Refus motivé")
        submission = self.act("need", "porteur-demande", "submit")
        self.act("prerequisites", "architecte-referent", "reject", comment="Besoin à préciser")
        attempts = reconcile_graph(self.dat)
        self.assertNotEqual(attempts["need"].pk, submission.attempt_id)
        self.assertEqual(attempts["need"].status, "ready")
        self.assertEqual(attempts["prerequisites"].status, "waiting")

    def test_preparation_refusal_only_allows_technical_architect_to_retry(self):
        self.act("need", "porteur-demande", "submit")
        self.act("prerequisites", "architecte-referent")
        refusal = self.act("preparation", "architecte-technique", "reject", comment="Préparation à reprendre")
        attempts = reconcile_graph(self.dat)
        self.assertEqual(attempts["preparation"].status, "refusal_blocked")
        self.assertEqual(attempts["urbanisation"].status, "waiting")
        self.assertEqual(attempts["cyber"].status, "waiting")
        with self.assertRaises(WorkflowPermissionDenied):
            self.act("preparation", "porteur-demande", "retry")
        self.act("preparation", "architecte-technique", "retry")
        attempts = reconcile_graph(self.dat)
        self.assertNotEqual(attempts["preparation"].pk, refusal.attempt_id)
        self.assertEqual(attempts["preparation"].status, "ready")

    def test_parallel_and_ssi_refusals_restart_preparation_and_both_branches(self):
        self.act("need", "porteur-demande", "submit")
        self.act("prerequisites", "architecte-referent")
        for node_id, role in (("urbanisation", "urbaniste"), ("cyber", "analyste-secu"), ("ssi", "rssi")):
            with self.subTest(node=node_id):
                self.act("preparation", "architecte-technique")
                if node_id != "urbanisation":
                    self.act("urbanisation", "urbaniste")
                if node_id != "cyber":
                    self.act("cyber", "analyste-secu")
                before = reconcile_graph(self.dat)
                self.act(node_id, role, "reject", comment="Dossier à revoir")
                after = reconcile_graph(self.dat)
                self.assertEqual(after["prerequisites"].pk, before["prerequisites"].pk)
                self.assertEqual(after["preparation"].status, "ready")
                for branch in ("urbanisation", "cyber"):
                    self.assertNotEqual(after[branch].pk, before[branch].pk)
                    self.assertEqual(after[branch].status, "waiting")
                self.assertTrue(any(
                    link["from"] == node_id and link["to"] == "preparation"
                    for link in graph_context(self.dat, self.actors[role])["refusal_links"]
                ))

    def test_hosting_refusal_returns_to_ssi_and_preserves_parallel_approvals(self):
        self.reach_ssi()
        self.act("ssi", "rssi")
        before = reconcile_graph(self.dat)
        decision = self.act("hosting", "infra-exploitation", "reject", comment="Hébergement à revoir")
        after = reconcile_graph(self.dat)
        for branch in ("urbanisation", "cyber"):
            self.assertEqual(after[branch].pk, before[branch].pk)
            self.assertEqual(after[branch].status, "completed")
        self.assertNotEqual(after["ssi"].pk, before["ssi"].pk)
        self.assertEqual(after["ssi"].status, "ready")
        self.assertEqual(after["hosting"].status, "waiting")
        self.assertEqual(decision.attempt_id, before["hosting"].pk)
        self.client.force_login(self.actors["rssi"])
        url = reverse("workflows:graph_detail", args=[self.dat.pk])
        link = {"from": "hosting", "to": "ssi", "decision_id": str(decision.pk), "pending": True}
        self.assertEqual(self.client.get(url, {"format": "json"}).json()["refusal_links"], [link])
        self.assertContains(self.client.get(url), 'href="#review-ssi"')
        self.act("ssi", "rssi")
        link["pending"] = False
        self.assertEqual(self.client.get(url, {"format": "json"}).json()["refusal_links"], [link])

    def test_final_refusal_closes_the_dat(self):
        self.reach_ssi()
        self.act("ssi", "rssi")
        self.act("hosting", "infra-exploitation")
        self.act("final", "comite-validation", "reject", comment="Refus du comité")
        graph = graph_context(self.dat, self.actors["comite-validation"])
        self.assertEqual(graph["instance"].current_state, "refuse")
        self.assertEqual(reconcile_graph(self.dat)["final"].status, "rejected")
        self.assertFalse(graph["has_actions"])

    def test_blocked_refusal_needs_configured_retry_role_even_after_edits(self):
        nodes = deepcopy(self.spec["nodes"])
        cyber = next(node for node in nodes if node["id"] == "cyber")
        cyber["approvers"]["all_roles"].append("infra-exploitation")
        cyber["on_reject"] = {"mode": "block", "retry_roles": ["infra-exploitation"]}
        self.configure(nodes)
        self.prepare()
        self.act("cyber", "infra-exploitation")
        refusal = self.act("cyber", "analyste-secu", "reject", comment="Mesures insuffisantes")
        self.act("urbanisation", "urbaniste")
        part = self.dat.sections.get(metadata__slug="cybersecurite").sub_sections.first().parts.first()
        part.update_value("Contenu à revoir")
        blocked = reconcile_graph(self.dat)["cyber"]
        self.assertEqual(blocked.pk, refusal.attempt_id)
        self.assertEqual(blocked.status, "refusal_blocked")
        self.assertEqual(reconcile_graph(self.dat)["ssi"].status, "waiting")

        with self.assertRaises(WorkflowPermissionDenied):
            self.act("cyber", "porteur-demande", "retry")
        retry_actor = self.actors["infra-exploitation"]
        self.client.force_login(retry_actor)
        url = reverse("workflows:graph_detail", args=[self.dat.pk])
        node = next(item for item in self.client.get(url, {"format": "json"}).json()["nodes"] if item["id"] == "cyber")
        self.assertTrue(node["can_retry"])
        self.assertContains(self.client.get(url), 'value="retry"')

        request_id = uuid4()
        retry = act_on_node(self.dat, "cyber", retry_actor, outcome="retry", attempt_id=blocked.pk, request_id=request_id)
        self.assertEqual(act_on_node(self.dat, "cyber", retry_actor, outcome="retry", attempt_id=blocked.pk, request_id=request_id).pk, retry.pk)
        with self.assertRaises(WorkflowConflict):
            act_on_node(self.dat, "cyber", retry_actor, outcome="retry", attempt_id=blocked.pk, request_id=uuid4())
        attempts = reconcile_graph(self.dat)
        self.assertEqual(attempts["cyber"].number, blocked.number + 1)
        self.assertEqual(attempts["cyber"].status, "ready")
        self.assertEqual(attempts["urbanisation"].status, "completed")
        self.assertEqual(retry.snapshot_id, refusal.snapshot_id)
        self.assertEqual(set(blocked.decisions.values_list("outcome", flat=True)), {"approve", "reject", "retry"})

    def test_optional_section_referent_gate_reopens_when_enforced(self):
        self.configure(self.review_nodes({"section": "architecture"}, {"section_referent": True}))
        attempts = reconcile_graph(self.dat)
        self.assertEqual(attempts["review"].status, "skipped")
        self.assertIsNone(attempts["review"].snapshot_id)
        self.assertEqual(attempts["final"].status, "ready")
        with override_settings(DAT_WORKFLOW_REQUIRE_SECTION_REFERENTS=True):
            required = reconcile_graph(self.dat)
            self.assertEqual(required["review"].blockers, [{"code": "missing_section_referent", "section": "architecture"}])
            self.assertEqual(required["final"].status, "waiting")
            self.assertNotEqual(required["final"].pk, attempts["final"].pk)

    @override_settings(DAT_WORKFLOW_REQUIRE_SECTION_REFERENTS=True)
    def test_section_referent_approval_follows_explicit_active_assignment(self):
        self.configure(self.review_nodes({"section": "architecture"}, {"section_referent": True}))
        section = self.dat.sections.get(metadata__slug="architecture")
        referent = get_user_model().objects.create_user(username="section-only", is_active=False)
        assignment = DATSectionResponsible.objects.create(dat=self.dat, section=section, user=referent)
        self.assertEqual(reconcile_graph(self.dat)["review"].status, "waiting")
        referent.is_active = True
        referent.save(update_fields=["is_active"])
        ready = reconcile_graph(self.dat)["review"]
        with self.assertRaises(WorkflowPermissionDenied):
            self.act("review", "architecte-referent")
        self.client.force_login(referent)
        self.assertContains(self.client.get(reverse("workflows:my_approvals")), self.dat.reference)
        decision = act_on_node(self.dat, "review", referent, outcome="approve", attempt_id=ready.pk, request_id=uuid4())
        decision.refresh_from_db()
        self.assertEqual(decision.assignment_id, assignment.pk)
        self.assertEqual(reconcile_graph(self.dat)["final"].status, "ready")
        assignment.user = self.actors["architecte-referent"]
        assignment.save(update_fields=["user"])
        after = reconcile_graph(self.dat)
        self.assertEqual(after["review"].status, "ready")
        self.assertNotEqual(after["review"].pk, ready.pk)
        self.assertEqual(after["final"].status, "waiting")

    def test_cutover_creates_fresh_attempts_for_unpinned_active_dat(self):
        instance = ensure_workflow_instance(self.dat)
        previous_id = instance.pk
        instance.delete()
        self.dat._workflow_instance_cache = {}
        result = migrate_workflow_instances(workflow_code="dat-validation", restart_active=True, object_ids=[self.dat.pk])
        self.assertEqual(result.migrated, 1)
        fresh = ensure_workflow_instance(self.dat)
        self.assertNotEqual(fresh.pk, previous_id)
        self.assertEqual(reconcile_graph(self.dat)["need"].status, "ready")
        self.assertFalse(WorkflowApprovalDecision.objects.filter(attempt__instance=fresh).exists())

    def test_cutover_preserves_authoritative_terminal_instance_even_if_projection_is_old(self):
        instance = ensure_workflow_instance(self.dat)
        instance.current_state = "valider"
        instance.save(update_fields=["current_state"])
        old_version = instance.definition_version_id
        spec = deepcopy(self.spec)
        spec["name"] = "New circuit for active DATs"
        publish_graph(spec)
        result = migrate_workflow_instances(workflow_code="dat-validation", restart_active=True, object_ids=[self.dat.pk])
        instance.refresh_from_db()
        self.assertEqual(result.migrated, 0)
        self.assertEqual(instance.definition_version_id, old_version)
        self.assertEqual(instance.current_state, "valider")

    def test_session_json_decision_requires_csrf_token(self):
        from django.test import Client
        self.prepare()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actors["analyste-secu"])
        attempt = reconcile_graph(self.dat)["cyber"]
        response = client.post(reverse("workflows:node_action", args=[self.dat.pk, "cyber"]), {"outcome": "approve", "attempt_id": str(attempt.pk), "request_id": str(uuid4())}, content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_restart_preview_does_not_write_snapshot_files(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import InMemoryStorage
        from dat.models import DATSectionAttachment

        storage = InMemoryStorage()
        storage.save("attachments/preview.txt", ContentFile(b"Preview source"))
        section = self.dat.sections.get(metadata__slug="cybersecurite")
        DATSectionAttachment.objects.create(section=section, storage_path="attachments/preview.txt", original_name="preview.txt", display_name="preview.txt", extension=".txt", size=14)
        spec = deepcopy(self.spec)
        spec["nodes"][0]["target"] = {"section": "cybersecurite"}
        publish_graph(spec)
        before = storage.listdir("")
        with mock.patch("workflows.graph_artifacts.SeaweedFSStorage", return_value=storage):
            call_command("migrate_workflow_instances", "dat-validation", "--restart-active", "--object-id", str(self.dat.pk), stdout=StringIO())
        self.assertEqual(storage.listdir(""), before)


class GraphConcurrencyTests(GraphFixture, TransactionTestCase):
    workflow_file = "dat-validation-refusal-routes.json"

    def concurrent(self, calls):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connections

        barrier = Barrier(len(calls))

        def run(call):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return call()
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = [pool.submit(run, call) for call in calls]
            return [future.result(timeout=20) for future in futures]

    def test_parallel_decisions_activate_one_ssi_attempt(self):
        from django.db import connection
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock concurrency requires PostgreSQL.")
        self.prepare()
        attempts = reconcile_graph(self.dat)

        def approve(node, role):
            dat = DAT.objects.get(pk=self.dat.pk)
            return act_on_node(dat, node, self.actors[role], outcome="approve", attempt_id=attempts[node].pk, request_id=uuid4())

        self.concurrent([lambda: approve("urbanisation", "urbaniste"), lambda: approve("cyber", "analyste-secu")])
        ssi = reconcile_graph(self.dat)["ssi"]
        self.assertEqual(ssi.status, "ready")
        self.assertEqual(ssi.dependencies, {node: str(attempts[node].pk) for node in ("urbanisation", "cyber")})
        self.assertEqual(ensure_workflow_instance(self.dat).node_attempts.filter(node_id="ssi").count(), 1)

    def test_duplicate_concurrent_request_is_counted_once(self):
        self.prepare()
        attempt = reconcile_graph(self.dat)["cyber"]
        request_id = uuid4()

        def approve():
            return act_on_node(DAT.objects.get(pk=self.dat.pk), "cyber", self.actors["analyste-secu"], outcome="approve", attempt_id=attempt.pk, request_id=request_id).pk

        results = self.concurrent([approve, approve])
        self.assertEqual(results[0], results[1])
        self.assertEqual(WorkflowApprovalDecision.objects.filter(request_id=request_id).count(), 1)


class GraphValidationTests(TestCase):
    def test_required_content_treats_false_and_zero_as_values_but_not_whitespace(self):
        from .graph_targets import TargetFacts
        facts = object.__new__(TargetFacts)
        for value, expected in ((False, True), (0, True), ("   ", False), (None, False), ([], False)):
            with self.subTest(value=value):
                self.assertEqual(facts.condition({"required_content_complete": True}, {"parts": [{"required": True, "value": value}]}), expected)

    def test_rejects_cycles_unknown_roles_and_code_in_configuration(self):
        spec = load_graph()
        with self.assertRaises(WorkflowConfigurationError):
            validate_graph(spec, role_slugs=[])
        invalid = deepcopy(spec)
        invalid["nodes"][0]["after"] = ["final"]
        with self.assertRaises(WorkflowConfigurationError):
            validate_graph(invalid)
        invalid = deepcopy(spec)
        invalid["nodes"][1]["guard"] = "import os"
        with self.assertRaises(WorkflowConfigurationError):
            validate_graph(invalid)

    def test_rejects_invalid_refusal_policies_and_referent_targets(self):
        spec = load_graph()
        for policy in (
            {"mode": "return", "restart_at": "prerequisites"},
            {"mode": "return", "restart_at": "final"},
            {"mode": "block", "retry_roles": []},
            {"mode": "close", "retry_roles": ["rssi"]},
        ):
            with self.subTest(policy=policy):
                invalid = deepcopy(spec)
                invalid["nodes"][1]["on_reject"] = policy
                with self.assertRaises(WorkflowConfigurationError):
                    validate_graph(invalid)
        invalid = deepcopy(spec)
        invalid["nodes"][1]["on_reject"] = {"mode": "block", "retry_roles": ["unknown-retry-role"]}
        with self.assertRaisesMessage(WorkflowConfigurationError, "unknown-retry-role"):
            validate_graph(invalid, role_slugs={role for node in spec["nodes"] for role in node.get("approvers", {}).get("all_roles", ())})
        invalid = deepcopy(spec)
        invalid["nodes"][-1]["approvers"] = {"section_referent": True}
        with self.assertRaises(WorkflowConfigurationError):
            validate_graph(invalid)
