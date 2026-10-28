# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from . import utils


class DependencyGraphTests(SimpleTestCase):
    def test_graph_builder_handles_callable_failures_defaults_and_optional_fields(self):
        value_error = mock.Mock(side_effect=RuntimeError("not available"))
        include_error = mock.Mock(side_effect=RuntimeError("not available"))
        graph = utils.build_dependency_graph(
            node_specs=[
                None,
                {"title": "Missing id"},
                {"id": "excluded", "include_if": False},
                {"id": "failed condition", "include_if": include_error},
                {
                    "id": "resolved",
                    "title": "Resolved",
                    "value": lambda: "  Alice  ",
                    "url": "/users/1/",
                    "optional": True,
                    "layout": "vertical",
                },
                {"id": "failed value", "value": value_error, "default": "Unknown"},
                {"id": "empty value", "value": "  "},
            ],
            link_specs=[None, {"from": "missing target"}, {"from": "user", "to": "group"}],
        )

        self.assertEqual(
            graph,
            {
                "nodes": [
                    {
                        "id": "resolved",
                        "title": "Resolved",
                        "value": "Alice",
                        "url": "/users/1/",
                        "optional": True,
                        "layout": "vertical",
                    },
                    {"id": "failed value", "title": None, "value": "Unknown"},
                    {"id": "empty value", "title": None, "value": "Non défini"},
                ],
                "links": [{"from": "user", "to": "group", "route": "horizontal"}],
            },
        )

    def test_count_labels_and_fallback_relationship_counts(self):
        self.assertEqual(utils._display_value("  Team  "), "Team")
        self.assertEqual(utils._display_value(" ", "No team"), "No team")
        self.assertEqual(utils._with_count("Team", None, "member"), "Team")
        self.assertEqual(utils._with_count("Team", 1, "member"), "Team — 1 member")
        self.assertEqual(utils._with_count("Team", 3, "member", "people"), "Team — 3 people")
        self.assertEqual(utils._group_value_with_member_count(None), "Non défini")
        self.assertEqual(utils._role_value_with_member_count(None), "Non défini")

        group = SimpleNamespace(name="Platform", member_count=2)
        self.assertEqual(utils._group_value_with_member_count(group), "Platform — 2 membres")
        role = SimpleNamespace(name="Architect", member_count=1)
        self.assertEqual(utils._role_value_with_member_count(role), "Architect — 1 utilisateur")

        fallback_group = SimpleNamespace(name="Fallback", users=SimpleNamespace(count=lambda: 3))
        self.assertEqual(utils._group_value_with_member_count(fallback_group), "Fallback — 3 membres")
        fallback_role = SimpleNamespace(name="Fallback role", users=SimpleNamespace(count=lambda: 0))
        self.assertEqual(utils._role_value_with_member_count(fallback_role), "Fallback role — 0 utilisateurs")
        failed_count = SimpleNamespace(name="Unknown", users=SimpleNamespace(count=mock.Mock(side_effect=RuntimeError)))
        self.assertEqual(utils._group_value_with_member_count(failed_count), "Unknown")
        self.assertEqual(utils._role_value_with_member_count(failed_count), "Unknown")

    def test_reverse_helper_returns_none_for_missing_ids_and_unavailable_routes(self):
        self.assertIsNone(utils._reverse_or_none("users:user_change", None))
        with mock.patch.object(utils, "reverse", return_value="/users/7/"):
            self.assertEqual(utils._reverse_or_none("users:user_change", 7), "/users/7/")
        with mock.patch.object(utils, "reverse", side_effect=RuntimeError("route missing")):
            self.assertIsNone(utils._reverse_or_none("users:user_change", 7))

    def test_user_graph_prefers_group_direction_then_role_and_admin_fallback(self):
        group_direction = SimpleNamespace(pk=3, name="Group direction")
        role_direction = SimpleNamespace(pk=4, name="Role direction")
        responsible = SimpleNamespace(
            pk=5,
            username="manager",
            get_full_name=lambda: "Group Manager",
        )
        group = SimpleNamespace(
            pk=2,
            name=" Platform ",
            member_count=2,
            direction=group_direction,
            business_direction=SimpleNamespace(pk=6, name="Business direction"),
            responsible=responsible,
        )
        user = SimpleNamespace(
            pk=1,
            get_username=lambda: "alice",
            role=SimpleNamespace(name="Architect", technical_direction=role_direction, is_admin_role=True),
            business_group=group,
        )
        with mock.patch.object(utils, "reverse", side_effect=lambda name, kwargs: f"/{name}/{kwargs['pk']}/"):
            graph = utils.build_user_dependency_graph(user)

        by_id = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(by_id["user"]["value"], "alice — Architect")
        self.assertEqual(by_id["group"]["value"], "Platform — 2 membres")
        self.assertEqual(by_id["technical_direction"]["value"], "Group direction")
        self.assertEqual(by_id["responsible"]["value"], "Group Manager")
        self.assertEqual(by_id["technical_direction"]["url"], "/users:technicaldirection_change/3/")
        self.assertEqual(len(graph["links"]), 4)

        user.business_group = None
        user.role.technical_direction = role_direction
        fallback = utils.build_user_dependency_graph(user)
        self.assertEqual({node["id"]: node for node in fallback["nodes"]}["technical_direction"]["value"], "Role direction")
        user.role.technical_direction = None
        transverse = utils.build_user_dependency_graph(user)
        self.assertEqual({node["id"]: node for node in transverse["nodes"]}["technical_direction"]["value"], "Transverse")

    def test_group_graph_handles_missing_relationships(self):
        group = SimpleNamespace(
            pk=None,
            name="Group without links",
            member_count=None,
            users=SimpleNamespace(count=lambda: 0),
            direction=None,
            business_direction=None,
            responsible=None,
        )
        graph = utils.build_group_dependency_graph(group)
        by_id = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(by_id["group"]["value"], "Group without links — 0 membres")
        self.assertEqual(by_id["technical_direction"]["value"], "Non défini")
        self.assertTrue(by_id["business_direction"]["optional"])
        self.assertEqual(by_id["responsible"]["value"], "Non défini")
        self.assertEqual(len(graph["links"]), 3)
