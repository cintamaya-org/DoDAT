# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from workflows.exceptions import WorkflowConfigurationError

from .sections import SECTION_STATUS_VALIDATED_VALUE
from .workflow import DATWorkflowAdapter


class DATWorkflowAdapterTests(SimpleTestCase):
    def setUp(self):
        self.adapter = DATWorkflowAdapter()
        self.specification = {
            "steps": [
                {"status": "draft", "is_initial": True},
                {"status": "review"},
            ]
        }

    def test_initial_state_prefers_explicit_then_legacy_then_configured_default(self):
        self.assertEqual(
            self.adapter.initial_state(SimpleNamespace(_workflow_initial_state="review", status="draft"), self.specification),
            "review",
        )
        self.assertEqual(self.adapter.initial_state(SimpleNamespace(status="review"), self.specification), "review")
        self.assertEqual(self.adapter.initial_state(SimpleNamespace(status="unknown"), self.specification), "draft")
        with self.assertRaisesRegex(WorkflowConfigurationError, "no initial state"):
            self.adapter.initial_state(SimpleNamespace(), {"steps": [{"status": "draft"}]})

    def test_guards_cover_validated_sections_responsibles_and_force_context(self):
        validated = {
            "architecture": {
                "has_status": True,
                "value": SECTION_STATUS_VALIDATED_VALUE,
                "responsable_value": SECTION_STATUS_VALIDATED_VALUE,
            },
            "optional": {"has_status": False},
        }
        self.assertTrue(self.adapter.evaluate_guard("", object()))
        self.assertTrue(self.adapter.evaluate_guard("always", object()))
        self.assertTrue(self.adapter.evaluate_guard("all_sections_validated", object(), context={"status_map": validated}))
        self.assertTrue(self.adapter.evaluate_guard("sections_not_all_validated", object(), context={"status_map": {}}))
        self.assertTrue(self.adapter.evaluate_guard("all_responsible_validated", object(), context={"status_map": validated}))
        self.assertFalse(self.adapter.evaluate_guard("all_sections_validated", object(), context={"status_map": {}}))
        self.assertFalse(
            self.adapter.evaluate_guard(
                "all_responsible_validated",
                object(),
                context={"status_map": {"architecture": {"has_status": True, "responsable_value": "en_cours"}}},
            )
        )
        self.assertTrue(
            self.adapter.evaluate_guard(
                "force_in_progress",
                object(),
                context={"status_map": {}, "force_in_progress": True},
            )
        )
        self.assertFalse(
            self.adapter.evaluate_guard("force_in_progress", object(), context={"status_map": {}})
        )
        with self.assertRaisesRegex(WorkflowConfigurationError, "Unknown DAT workflow guard"):
            self.adapter.evaluate_guard("unknown", object(), context={"status_map": {}})

    def test_actions_skip_empty_names_reject_unknown_and_reset_supplied_or_built_status(self):
        dat = object()
        with mock.patch("dat.views.reset_section_statuses_to_default") as reset, mock.patch(
            "dat.views.build_section_status_map", return_value=({"architecture": {}}, {"en_cours": "En cours"})
        ) as build:
            self.adapter.perform_action("", dat)
            build.assert_not_called()

            supplied_map = {"architecture": {"value": "valide"}}
            supplied_choices = {"valide": "Validé"}
            self.adapter.perform_action(
                "reset_section_statuses",
                dat,
                context={"status_map": supplied_map, "status_choices": supplied_choices},
            )
            reset.assert_called_once_with(dat, status_map=supplied_map, status_choices=supplied_choices)
            build.assert_not_called()

            reset.reset_mock()
            self.adapter.perform_action("reset_section_statuses", dat)
            build.assert_called_once_with(dat)
            reset.assert_called_once_with(
                dat,
                status_map={"architecture": {}},
                status_choices={"en_cours": "En cours"},
            )

        with self.assertRaisesRegex(WorkflowConfigurationError, "Unknown DAT workflow action"):
            self.adapter.perform_action("unknown", dat)

    def test_actor_role_check_handles_admin_authentication_and_participant_match(self):
        dat = SimpleNamespace(
            participants=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(user_id=7, role=SimpleNamespace(slug="architect")),
                    SimpleNamespace(user_id=8, role=SimpleNamespace(slug="reviewer")),
                ]
            )
        )
        with mock.patch("dat.workflow.user_is_dat_admin", return_value=False) as is_admin:
            self.assertFalse(self.adapter.actor_has_any_role(dat, None, ("architect",)))
            self.assertFalse(
                self.adapter.actor_has_any_role(dat, SimpleNamespace(is_authenticated=False, pk=7), ("architect",))
            )
            self.assertFalse(is_admin.called)
            self.assertFalse(
                self.adapter.actor_has_any_role(dat, SimpleNamespace(is_authenticated=True), ("architect",))
            )
            self.assertTrue(
                self.adapter.actor_has_any_role(dat, SimpleNamespace(is_authenticated=True, pk=7), ("architect",))
            )
            self.assertFalse(
                self.adapter.actor_has_any_role(dat, SimpleNamespace(is_authenticated=True, pk=7), ("reviewer",))
            )

        with mock.patch("dat.workflow.user_is_dat_admin", return_value=True):
            self.assertTrue(
                self.adapter.actor_has_any_role(dat, SimpleNamespace(is_authenticated=True), ("any-role",))
            )

    def test_state_projection_skips_unchanged_state_and_saves_changed_state(self):
        unchanged = SimpleNamespace(status="draft", save=mock.Mock())
        self.adapter.project_state(unchanged, "draft")
        unchanged.save.assert_not_called()

        actor = SimpleNamespace(pk=4)
        changed = SimpleNamespace(status="draft", save=mock.Mock())
        self.adapter.project_state(changed, "review", actor=actor)
        self.assertEqual(changed.status, "review")
        self.assertIs(changed._history_actor, actor)
        changed.save.assert_called_once_with(update_fields=["status", "updated_at"])

    def test_status_map_uses_supplied_context_or_builds_from_dat(self):
        supplied = {"architecture": {"value": "valide"}}
        self.assertIs(self.adapter._status_map(object(), {"status_map": supplied}), supplied)
        with mock.patch("dat.views.build_section_status_map", return_value=(supplied, {"valide": "Validé"})) as build:
            dat = object()
            self.assertIs(self.adapter._status_map(dat, None), supplied)
            build.assert_called_once_with(dat)
