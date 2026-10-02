# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from dataclasses import replace

from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from dat.models import Application, DAT, DATParticipant, DATStatus, DATHistory, DATHistoryAction
from users.models import BusinessDirection, Role, TechnicalDirection
from .definitions import WORKFLOW_DEFINITIONS, WorkflowTransitionDefinition
from .exceptions import WorkflowConfigurationError
from .models import (
    HistoryNotificationSeen,
    NotificationMessage,
    NotificationType,
    UserNotification,
    Workflow,
    WorkflowDefinitionVersion,
    WorkflowInstance,
    WorkflowTransitionEvent,
)
from .notifications import get_unread_notification_count, mark_all_notifications_as_seen, mark_notifications_as_seen
from .services import (
    ensure_workflow_instance,
    migrate_workflow_instances,
    transition_workflow,
    workflow_can,
    workflow_has_capability,
)
from .sync import sync_workflow_definitions
from .views import WorkflowBoardView


ROLE_FIXTURES = {
    "porteur-demande": "Porteur de la demande",
    "architecte-referent": "Architecte Referent",
    "architecte-technique": "Architecte Technique",
    "urbaniste": "Urbaniste",
    "analyste-secu": "Analyste Secu",
    "rssi": "RSSI",
    "infra-exploitation": "Infra / Exploitation",
    "comite-validation": "Comite de validation",
}


class WorkflowSyncTests(TestCase):
    def setUp(self):
        self.roles = {slug: create_role(slug, name) for slug, name in ROLE_FIXTURES.items()}

    def test_sync_creates_workflow_steps_and_permissions(self):
        sync_workflow_definitions()

        workflow = Workflow.objects.get(code="dat-validation")
        steps = list(workflow.steps.order_by("order"))

        self.assertEqual(len(steps), 6)
        draft = next(step for step in steps if step.key == "nouvelle-demande")
        self.assertTrue(draft.is_initial)
        self.assertEqual(draft.write_permissions.count(), 1)
        self.assertEqual(draft.write_permissions.first().role, self.roles["porteur-demande"])

        review_step = next(step for step in steps if step.key == "en-attente-revue")
        self.assertEqual(
            {perm.role for perm in review_step.write_permissions},
            {
                self.roles["architecte-referent"],
                self.roles["comite-validation"],
            },
        )
        review_read_roles = {perm.role for perm in review_step.read_permissions}
        self.assertIn(self.roles["porteur-demande"], review_read_roles)

    def test_sync_publishes_immutable_version_and_reuses_same_checksum(self):
        sync_workflow_definitions()
        workflow = Workflow.objects.get(code="dat-validation")
        first_version = workflow.active_version

        sync_workflow_definitions()
        workflow.refresh_from_db()

        self.assertEqual(workflow.active_version, first_version)
        self.assertEqual(WorkflowDefinitionVersion.objects.filter(workflow=workflow).count(), 1)

    def test_existing_instance_remains_pinned_when_new_version_is_published(self):
        sync_workflow_definitions()
        owner = get_user_model().objects.create_user(username="version-owner", password="pwd")
        application = Application.objects.create(
            code="versioned-workflow-app",
            name="Versioned workflow",
            business_direction=get_default_business_direction(),
        )
        dat = DAT.objects.create(
            reference="DAT-VERSION-1",
            title="Pinned",
            application=application,
            owner=owner,
        )
        instance = ensure_workflow_instance(dat)
        original_version = instance.definition_version

        modified = replace(WORKFLOW_DEFINITIONS[0], name="Validation des DAT v2")
        sync_workflow_definitions((modified,))
        instance.refresh_from_db()

        self.assertEqual(instance.definition_version, original_version)
        self.assertEqual(WorkflowInstance.objects.filter(pk=instance.pk).count(), 1)
        self.assertNotEqual(instance.workflow.active_version, original_version)

    def test_instance_migration_is_explicit_and_audited(self):
        sync_workflow_definitions()
        owner = get_user_model().objects.create_user(username="migration-owner", password="pwd")
        application = Application.objects.create(
            code="migration-workflow-app",
            name="Migration workflow",
            business_direction=get_default_business_direction(),
        )
        dat = DAT.objects.create(
            reference="DAT-MIGRATION-1",
            title="Migrated",
            application=application,
            owner=owner,
        )
        instance = ensure_workflow_instance(dat)
        original_version = instance.definition_version

        modified = replace(WORKFLOW_DEFINITIONS[0], name="Validation des DAT v2")
        sync_workflow_definitions((modified,))
        workflow = Workflow.objects.get(code="dat-validation")

        result = migrate_workflow_instances(workflow_code="dat-validation")
        instance.refresh_from_db()

        self.assertEqual(result.examined, 1)
        self.assertEqual(result.migrated, 1)
        self.assertNotEqual(instance.definition_version, original_version)
        self.assertEqual(instance.definition_version, workflow.active_version)
        self.assertTrue(
            WorkflowTransitionEvent.objects.filter(
                instance=instance,
                event="workflow-migrated",
                metadata__from_version=original_version.version,
                metadata__to_version=workflow.active_version.version,
            ).exists()
        )

    def test_sync_supports_step_key_rename_without_state_collision(self):
        sync_workflow_definitions()
        definition = WORKFLOW_DEFINITIONS[0]
        modified = replace(
            definition,
            steps=tuple(
                replace(step, key="quality-gate")
                if step.status == "en_attente_de_revue"
                else step
                for step in definition.steps
            ),
        )

        sync_workflow_definitions((modified,))

        workflow = Workflow.objects.get(code="dat-validation")
        self.assertFalse(workflow.steps.filter(key="en-attente-revue").exists())
        self.assertEqual(
            workflow.steps.get(state="en_attente_de_revue").key,
            "quality-gate",
        )

    def test_sync_supports_atomic_state_code_swap(self):
        sync_workflow_definitions()
        definition = WORKFLOW_DEFINITIONS[0]
        state_map = {"en_cours": "reserve", "reserve": "en_cours"}
        modified = replace(
            definition,
            steps=tuple(
                replace(step, status=state_map.get(step.status, step.status))
                for step in definition.steps
            ),
            transitions=tuple(
                replace(
                    transition,
                    sources=tuple(
                        state_map.get(source, source) for source in transition.sources
                    ),
                    target=state_map.get(transition.target, transition.target),
                )
                for transition in definition.transitions
            ),
        )

        sync_workflow_definitions((modified,))

        workflow = Workflow.objects.get(code="dat-validation")
        self.assertEqual(workflow.steps.get(key="en-cours").state, "reserve")
        self.assertEqual(workflow.steps.get(key="reserve").state, "en_cours")

    def test_sync_rejects_ambiguous_equal_priority_routes(self):
        sync_workflow_definitions()
        definition = WORKFLOW_DEFINITIONS[0]
        first = WorkflowTransitionDefinition(
            event="ambiguous-route",
            sources=("nouvelle_demande",),
            target="en_cours",
            order=7,
        )
        modified = replace(
            definition,
            transitions=definition.transitions + (first, replace(first, target="refuse")),
        )

        with self.assertRaisesRegex(WorkflowConfigurationError, "ambiguous"):
            sync_workflow_definitions((modified,))

    def test_sync_rejects_rebinding_existing_code_to_another_model(self):
        sync_workflow_definitions()
        workflow = Workflow.objects.get(code="dat-validation")
        original_content_type = workflow.content_type
        original_version = workflow.active_version
        modified = replace(WORKFLOW_DEFINITIONS[0], model="dat.Application")

        with self.assertRaisesRegex(WorkflowConfigurationError, "already bound"):
            sync_workflow_definitions((modified,))

        workflow.refresh_from_db()
        self.assertEqual(workflow.content_type, original_content_type)
        self.assertEqual(workflow.active_version, original_version)


class WorkflowEngineTests(TestCase):
    def setUp(self):
        self.roles = {slug: create_role(slug, name) for slug, name in ROLE_FIXTURES.items()}
        sync_workflow_definitions()
        self.owner = get_user_model().objects.create_user(username="engine-owner", password="pwd")
        self.reviewer = get_user_model().objects.create_user(username="engine-reviewer", password="pwd")
        self.application = Application.objects.create(
            code="engine-app",
            name="Engine app",
            business_direction=get_default_business_direction(),
        )
        self.dat = DAT.objects.create(
            reference="DAT-ENGINE-1",
            title="Engine",
            application=self.application,
            owner=self.owner,
        )
        DATParticipant.objects.create(
            dat=self.dat,
            role=self.roles["architecte-referent"],
            user=self.reviewer,
        )

    @staticmethod
    def _status_map(*, validated: bool, responsible_validated: bool = False):
        return {
            "architecture": {
                "has_status": True,
                "value": "valide" if validated else "en_cours",
                "responsable_value": "valide" if responsible_validated else "en_cours",
            }
        }

    def test_automatic_and_human_transitions_use_engine_and_project_legacy_status(self):
        draft = ensure_workflow_instance(self.dat)
        self.assertEqual(draft.current_state, "nouvelle_demande")

        progress = transition_workflow(
            self.dat,
            "sections_changed",
            self.owner,
            context={"status_map": self._status_map(validated=False), "force_in_progress": True},
            strict=False,
        )
        self.assertTrue(progress.changed)
        self.assertEqual(progress.to_state, "en_cours")

        review = transition_workflow(
            self.dat,
            "sections_changed",
            self.owner,
            context={"status_map": self._status_map(validated=True)},
            strict=False,
        )
        self.assertEqual(review.to_state, "en_attente_de_revue")
        self.assertTrue(workflow_can(self.dat, "approve", self.reviewer))

        approved = transition_workflow(self.dat, "approve", self.reviewer)
        self.dat.refresh_from_db()
        self.assertEqual(approved.to_state, "valider")
        self.assertEqual(self.dat.status, "valider")
        self.assertTrue(workflow_has_capability(self.dat, "terminal"))
        self.assertEqual(WorkflowTransitionEvent.objects.filter(instance=approved.instance).count(), 3)


class WorkflowBoardViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="architect", password="pwd")
        self.roles = {slug: create_role(slug, name) for slug, name in ROLE_FIXTURES.items()}
        sync_workflow_definitions()
        self.other_user = get_user_model().objects.create_user(username="other-user", password="pwd")
        self.client = Client()
        self.client.force_login(self.user)
        self.business_direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="workflow-app",
            name="Workflow App",
            business_direction=self.business_direction,
        )

    def test_board_renders_columns_with_dat_items(self):
        DAT.objects.create(
            reference="DAT-001",
            title="Initial",
            status=DATStatus.NOUVELLE_DEMANDE,
            application=self.application,
            owner=self.user,
        )
        DAT.objects.create(
            reference="DAT-002",
            title="Technique",
            status=DATStatus.EN_COURS,
            application=self.application,
            owner=self.user,
        )
        DAT.objects.create(
            reference="DAT-003",
            title="Referent",
            status=DATStatus.EN_ATTENTE_DE_REVUE,
            application=self.application,
            owner=self.user,
        )

        response = self.client.get(reverse("workflows:index"))

        self.assertEqual(response.status_code, 200)
        columns = response.context["columns"]
        self.assertEqual(len(columns), 3)

        initial_column = columns[0]
        self.assertEqual(initial_column["status_codes"], [DATStatus.NOUVELLE_DEMANDE])
        self.assertEqual(len(initial_column["items"]), 1)
        self.assertContains(response, "DAT-001")
        self.assertContains(response, "Projets en cours")

        in_progress_column = columns[1]
        in_progress_statuses = set(in_progress_column["status_codes"])
        self.assertIn(DATStatus.EN_COURS, in_progress_statuses)
        self.assertIn(DATStatus.EN_ATTENTE_DE_REVUE, columns[1]["status_codes"])

    def test_board_alias_works(self):
        response = self.client.get(reverse("workflows:board"))
        self.assertEqual(response.status_code, 200)

    def test_board_hides_unassigned_dats(self):
        DAT.objects.create(
            reference="DAT-OWNED",
            title="My DAT",
            status=DATStatus.NOUVELLE_DEMANDE,
            application=self.application,
            owner=self.user,
        )
        DAT.objects.create(
            reference="DAT-FOREIGN",
            title="Other DAT",
            status=DATStatus.EN_COURS,
            application=self.application,
            owner=self.other_user,
        )

        response = self.client.get(reverse("workflows:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DAT-OWNED")
        self.assertNotContains(response, "DAT-FOREIGN")

    def test_board_admin_sees_all_dats(self):
        admin = get_user_model().objects.create_user(
            username="board-admin",
            password="pwd",
            is_staff=True,
        )
        DAT.objects.create(
            reference="DAT-ADMIN-1",
            title="Admin Visible",
            status=DATStatus.NOUVELLE_DEMANDE,
            application=self.application,
        )
        DAT.objects.create(
            reference="DAT-ADMIN-2",
            title="Admin Visible 2",
            status=DATStatus.EN_ATTENTE_DE_REVUE,
            application=self.application,
            owner=self.other_user,
        )

        self.client.force_login(admin)
        response = self.client.get(reverse("workflows:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DAT-ADMIN-1")
        self.assertContains(response, "DAT-ADMIN-2")

    def test_progress_button_not_rendered(self):
        referent_role = self.roles["architecte-referent"]
        porteur_role = self.roles["porteur-demande"]
        porteur = get_user_model().objects.create_user(username="board-porteur", password="pwd")
        dat = DAT.objects.create(
            reference="DAT-BOARD-REF",
            title="Board Referent",
            status=DATStatus.EN_ATTENTE_DE_REVUE,
            application=self.application,
            owner=porteur,
        )
        DATParticipant.objects.create(dat=dat, role=porteur_role, user=porteur)
        self.user.role = referent_role
        self.user.save(update_fields=["role"])
        DATParticipant.objects.create(dat=dat, role=referent_role, user=self.user)

        response = self.client.get(reverse("workflows:index"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Passer a l'etape suivante")

    def test_dat_board_ordering_index_is_installed(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, DAT._meta.db_table)

        self.assertIn("dat_updated_pk_desc_idx", constraints)

    def test_board_cursor_pagination_avoids_count_and_enriches_only_current_page(self):
        DAT.objects.bulk_create(
            [
                DAT(
                    reference=f"DAT-PAGINATION-{index:02d}",
                    title=f"Pagination {index:02d}",
                    status=DATStatus.NOUVELLE_DEMANDE,
                    application=self.application,
                    business_direction=self.business_direction,
                    owner=self.user,
                )
                for index in range(30)
            ]
        )

        request = RequestFactory().get(reverse("workflows:board"))
        request.user = self.user
        view = WorkflowBoardView()
        view.setup(request)
        with CaptureQueriesContext(connection) as queries:
            fetched_items = view.get_dat_items()

        self.assertEqual(len(fetched_items), 25)
        self.assertFalse(
            any("COUNT(" in query["sql"].upper() for query in queries),
            "DAT page retrieval must not issue an exact-count query.",
        )
        dat_page_queries = [
            query["sql"].upper()
            for query in queries
            if 'FROM "DAT_DAT"' in query["sql"].upper()
        ]
        self.assertTrue(any("LIMIT 26" in query for query in dat_page_queries))
        self.assertTrue(all("OFFSET" not in query for query in dat_page_queries))

        first_page = self.client.get(reverse("workflows:board"))
        self.assertEqual(first_page.status_code, 200)
        self.assertFalse(first_page.context["has_previous"])
        self.assertTrue(first_page.context["has_next"])
        first_items = [
            dat.pk
            for column in first_page.context["columns"]
            for dat in column["items"]
        ]
        self.assertEqual(len(first_items), 25)

        second_page = self.client.get(first_page.context["next_url"])
        self.assertEqual(second_page.status_code, 200)
        second_items = [
            dat.pk
            for column in second_page.context["columns"]
            for dat in column["items"]
        ]
        self.assertEqual(len(second_items), 5)
        self.assertFalse(set(first_items).intersection(second_items))
        self.assertEqual(len(set(first_items + second_items)), 30)
        self.assertTrue(second_page.context["has_previous"])
        self.assertFalse(second_page.context["has_next"])

        returned_to_first = self.client.get(second_page.context["previous_url"])
        returned_items = [
            dat.pk
            for column in returned_to_first.context["columns"]
            for dat in column["items"]
        ]
        self.assertEqual(returned_items, first_items)

    def test_invalid_board_cursor_returns_first_page(self):
        DAT.objects.bulk_create(
            [
                DAT(
                    reference=f"DAT-INVALID-CURSOR-{index:02d}",
                    title=f"Invalid cursor {index:02d}",
                    status=DATStatus.NOUVELLE_DEMANDE,
                    application=self.application,
                    business_direction=self.business_direction,
                    owner=self.user,
                )
                for index in range(30)
            ]
        )

        response = self.client.get(
            reverse("workflows:board"),
            {"cursor": "not-a-valid-signed-cursor", "direction": "previous"},
        )

        self.assertEqual(response.status_code, 200)
        rendered_items = sum(
            (column["items"] for column in response.context["columns"]),
            [],
        )
        self.assertEqual(len(rendered_items), 25)
        self.assertFalse(response.context["has_previous"])
        self.assertTrue(response.context["has_next"])

    def test_my_tasks_board_uses_cursor_pagination(self):
        DAT.objects.bulk_create(
            [
                DAT(
                    reference=f"DAT-TASK-PAGINATION-{index:02d}",
                    title=f"Task pagination {index:02d}",
                    status=DATStatus.NOUVELLE_DEMANDE,
                    application=self.application,
                    business_direction=self.business_direction,
                    owner=self.user,
                )
                for index in range(30)
            ]
        )

        first_page = self.client.get(reverse("workflows:my_tasks"))
        second_page = self.client.get(first_page.context["next_url"])

        self.assertEqual(first_page.status_code, 200)
        self.assertTrue(first_page.context["has_next"])
        self.assertEqual(second_page.status_code, 200)
        self.assertTrue(second_page.context["has_previous"])
        self.assertFalse(second_page.context["has_next"])


class WorkflowNotificationsViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="notif-user", password="pwd")
        self.client = Client()
        self.client.force_login(self.user)
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-notif",
            name="Application Notifications",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-NOTIF",
            title="DAT Notifications",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        self.history = DATHistory.objects.create(
            dat=self.dat,
            action=DATHistoryAction.STATUS_CHANGED,
            performed_by=self.user,
            performed_by_display="Notif User",
            details={"from": "Nouvelle demande", "to": "En Attente de revue"},
        )
        self.notification_type = NotificationType.objects.create(
            title="Export PDF lancé",
            level=NotificationType.LEVEL_INFO,
        )
        self.notification_message = NotificationMessage.objects.create(
            content="Votre export PDF est en préparation.",
        )
        self.user_notification = UserNotification.objects.create(
            user=self.user,
            notification_type=self.notification_type,
            notification_message=self.notification_message,
            dat=self.dat,
            target_url="/dat/1/",
        )

    def test_notifications_view_combines_sources_and_marks_as_viewed(self):
        response = self.client.get(reverse("workflows:notifications"))
        self.assertEqual(response.status_code, 200)
        notifications = response.context["notifications"]
        expected_history_count = DATHistory.objects.filter(dat=self.dat).count()
        self.assertEqual(len(notifications), expected_history_count + 1)
        self.assertContains(response, "Export PDF lancé")
        self.assertContains(response, "En Attente de revue")

        self.assertTrue(
            HistoryNotificationSeen.objects.filter(user=self.user, history=self.history).exists()
        )

        self.user_notification.refresh_from_db()
        self.assertIsNotNone(self.user_notification.viewed_at)

    def test_notification_levels_are_displayed_in_french(self):
        expected_labels = {
            NotificationType.LEVEL_INFO: "Information",
            NotificationType.LEVEL_SUCCESS: "Succès",
            NotificationType.LEVEL_WARNING: "Avertissement",
            NotificationType.LEVEL_ERROR: "Erreur",
        }
        for index, level in enumerate(expected_labels, start=1):
            notification_type = NotificationType.objects.create(
                title=f"Notification niveau {index}",
                level=level,
            )
            UserNotification.objects.create(
                user=self.user,
                notification_type=notification_type,
                notification_message=self.notification_message,
                dat=self.dat,
            )

        response = self.client.get(reverse("workflows:notifications"))

        for label in expected_labels.values():
            self.assertContains(response, label)
        self.assertNotRegex(
            response.content.decode(),
            r">\s*(?:Info|Success|Warning|Error)\s*<",
        )

    def test_notification_model_labels_are_french(self):
        self.assertEqual(str(NotificationType._meta.verbose_name), "Type de notification")
        self.assertEqual(str(NotificationType._meta.verbose_name_plural), "Types de notification")
        self.assertEqual(str(NotificationMessage._meta.verbose_name), "Message de notification")
        self.assertEqual(
            str(NotificationMessage._meta.verbose_name_plural),
            "Messages de notification",
        )
        self.assertEqual(str(UserNotification._meta.verbose_name), "Notification utilisateur")
        self.assertEqual(
            str(UserNotification._meta.verbose_name_plural),
            "Notifications utilisateur",
        )
        self.assertEqual(
            str(HistoryNotificationSeen._meta.verbose_name),
            "Consultation d’historique de workflow",
        )
        self.assertEqual(
            str(HistoryNotificationSeen._meta.verbose_name_plural),
            "Consultations d’historique de workflow",
        )

    def test_notifications_are_scoped_to_connected_user(self):
        other_user = get_user_model().objects.create_user(username="notif-other", password="pwd")
        other_notification_type = NotificationType.objects.create(
            title="Autre notification",
            level=NotificationType.LEVEL_INFO,
        )
        other_notification_message = NotificationMessage.objects.create(
            content="Notification pour un autre utilisateur.",
        )
        other_notification = UserNotification.objects.create(
            user=other_user,
            notification_type=other_notification_type,
            notification_message=other_notification_message,
            target_url="/dat/2/",
        )

        self.client.force_login(other_user)
        response = self.client.get(reverse("workflows:notifications"))
        self.assertEqual(response.status_code, 200)

        titles = [entry["title"] for entry in response.context["notifications"]]
        self.assertIn(other_notification.title, titles)
        self.assertNotIn(self.user_notification.title, titles)

        self.assertFalse(
            HistoryNotificationSeen.objects.filter(user=other_user, history=self.history).exists()
        )

        self.user_notification.refresh_from_db()
        self.assertIsNone(self.user_notification.viewed_at)

    def test_notifications_do_not_reappear_as_unread_once_viewed(self):
        first_response = self.client.get(reverse("workflows:notifications"))
        self.assertEqual(first_response.status_code, 200)

        self.user_notification.refresh_from_db()
        self.assertIsNotNone(self.user_notification.viewed_at)

        second_response = self.client.get(reverse("workflows:notifications"))
        self.assertEqual(second_response.status_code, 200)

        notifications = second_response.context["notifications"]
        unread_flags = [entry.get("is_unread") for entry in notifications]
        self.assertFalse(any(unread_flags))
        self.assertEqual(second_response.context["notifications_unread_count"], 0)

    def test_mark_all_as_seen_marks_history_and_user_notifications(self):
        extra_history = DATHistory.objects.create(
            dat=self.dat,
            action=DATHistoryAction.UPDATED,
            performed_by=self.user,
            performed_by_display="Notif User",
            details={"changes": {"title": {"from": "Old", "to": "New"}}},
        )

        response = self.client.post(
            reverse("workflows:notifications"),
            data={"mark_all": "1"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [str(message) for message in get_messages(response.wsgi_request)],
            ["Toutes les notifications ont été marquées comme lues."],
        )
        self.assertTrue(
            HistoryNotificationSeen.objects.filter(user=self.user, history=self.history).exists()
        )
        self.assertTrue(
            HistoryNotificationSeen.objects.filter(user=self.user, history=extra_history).exists()
        )

        self.user_notification.refresh_from_db()
        self.assertIsNotNone(self.user_notification.viewed_at)

    def test_notifications_are_paginated_across_both_sources(self):
        UserNotification.objects.bulk_create(
            [
                UserNotification(
                    user=self.user,
                    notification_type=self.notification_type,
                    notification_message=self.notification_message,
                    dat=self.dat,
                )
                for _index in range(30)
            ]
        )

        first_page = self.client.get(reverse("workflows:notifications"))
        second_page = self.client.get(reverse("workflows:notifications"), {"page": 2})

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(first_page.context["notifications"]), 25)
        self.assertEqual(second_page.context["page_obj"].number, 2)
        self.assertLessEqual(len(second_page.context["notifications"]), 25)
        self.assertContains(second_page, "Page 2 sur 2")


def get_default_business_direction():
    direction, _ = BusinessDirection.objects.get_or_create(
        slug="direction-metier-test",
        defaults={"name": "Direction Métier Test"},
    )
    return direction


def get_default_technical_direction():
    direction, _ = TechnicalDirection.objects.get_or_create(
        slug="direction-technique-test",
        defaults={"name": "Direction Technique Test"},
    )
    return direction


def create_role(slug: str, name: str) -> Role:
    direction = get_default_technical_direction()
    role, _ = Role.objects.get_or_create(
        slug=slug,
        defaults={"name": name, "technical_direction": direction},
    )
    updates = {}
    if role.name != name:
        updates["name"] = name
    if role.slug != slug:
        updates["slug"] = slug
    if role.technical_direction_id != direction.id:
        updates["technical_direction"] = direction
    if updates:
        Role.objects.filter(pk=role.pk).update(**updates)
        role.refresh_from_db()
    return role


class NotificationHelpersTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(username="notif-helper", password="pwd")
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="notif-helper-app",
            name="Notification Helper App",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-NOTIF-HELPER",
            title="Notifications",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        DATHistory.objects.filter(dat=self.dat).delete()
        self.history = DATHistory.objects.create(
            dat=self.dat,
            action=DATHistoryAction.CREATED,
            performed_by=self.user,
            performed_by_display="Notif Helper",
            details={},
        )
        self.user_notification = UserNotification.objects.create(
            user=self.user,
            notification_type=NotificationType.objects.create(title="Notice", level=NotificationType.LEVEL_INFO),
            notification_message=NotificationMessage.objects.create(content="Message"),
            dat=self.dat,
            target_url="/dat/1/",
        )

    def _request(self):
        request = self.factory.get("/workflows/notifications")
        request.user = self.user
        return request

    def test_get_unread_notification_count_with_limit(self):
        request = self._request()
        self.assertEqual(get_unread_notification_count(request, limit=10), 2)
        HistoryNotificationSeen.objects.create(user=self.user, history=self.history)
        self.user_notification.viewed_at = self.user_notification.created_at
        self.user_notification.save(update_fields=["viewed_at"])
        self.assertEqual(get_unread_notification_count(request, limit=10), 0)

    def test_mark_notifications_as_seen(self):
        request = self._request()
        mark_notifications_as_seen(request, [self.history.id, "bad-id"])
        self.assertTrue(
            HistoryNotificationSeen.objects.filter(user=self.user, history=self.history).exists()
        )

    def test_mark_all_notifications_as_seen(self):
        mark_all_notifications_as_seen(self.user)
        self.assertTrue(
            HistoryNotificationSeen.objects.filter(user=self.user, history=self.history).exists()
        )
        self.user_notification.refresh_from_db()
        self.assertIsNotNone(self.user_notification.viewed_at)
