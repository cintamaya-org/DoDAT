# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

from .permissions import GranularModelPermissions


class GranularModelPermissionsTests(SimpleTestCase):
    def setUp(self):
        self.permission = GranularModelPermissions()

    def request(self, user, method="GET"):
        return SimpleNamespace(user=user, method=method)

    def test_normalize_required_accepts_supported_values(self):
        self.assertIsNone(self.permission._normalize_required(None))
        self.assertEqual(
            self.permission._normalize_required("users.view_user"),
            ("users.view_user",),
        )
        self.assertEqual(
            self.permission._normalize_required(["users.view_user", "users.change_user"]),
            ("users.view_user", "users.change_user"),
        )
        self.assertIsNone(self.permission._normalize_required(object()))

    def test_action_requirement_overrides_view_requirement(self):
        view = SimpleNamespace(
            permission_required_by_action={"retrieve": "users.view_user"},
            action="retrieve",
            permission_required="users.change_user",
        )
        self.assertEqual(
            self.permission._get_view_required(view),
            ("users.view_user",),
        )

    def test_view_requirement_is_used_when_action_mapping_does_not_match(self):
        for mapping, action in (
            (None, "retrieve"),
            ({}, "retrieve"),
            ({"update": "users.change_user"}, "retrieve"),
        ):
            with self.subTest(mapping=mapping, action=action):
                view = SimpleNamespace(
                    permission_required_by_action=mapping,
                    action=action,
                    permission_required="users.view_user",
                )
                self.assertEqual(
                    self.permission._get_view_required(view),
                    ("users.view_user",),
                )

    def test_explicit_permission_requires_authenticated_user_and_permission(self):
        view = SimpleNamespace(permission_required="users.view_user")
        allowed = Mock(is_authenticated=True)
        allowed.has_perms.return_value = True
        self.assertTrue(self.permission.has_permission(self.request(allowed), view))
        allowed.has_perms.assert_called_once_with(("users.view_user",))

        denied = Mock(is_authenticated=True)
        denied.has_perms.return_value = False
        self.assertFalse(self.permission.has_permission(self.request(denied), view))

        unauthenticated = Mock(is_authenticated=False)
        self.assertFalse(self.permission.has_permission(self.request(unauthenticated), view))
        unauthenticated.has_perms.assert_not_called()
        self.assertFalse(self.permission.has_permission(self.request(None), view))

    def test_model_permission_fallback_uses_drf_requirements(self):
        user = Mock(is_authenticated=True)
        user.has_perms.return_value = True
        view = SimpleNamespace(queryset=get_user_model().objects.all())

        self.assertTrue(self.permission.has_permission(self.request(user, "PUT"), view))
        user.has_perms.assert_called_once_with(["users.change_user"])

        user.has_perms.return_value = False
        self.assertFalse(self.permission.has_permission(self.request(user, "PUT"), view))

    def test_missing_model_queryset_falls_back_to_authentication(self):
        view = SimpleNamespace()
        self.assertTrue(
            self.permission.has_permission(self.request(Mock(is_authenticated=True)), view)
        )
        self.assertFalse(
            self.permission.has_permission(self.request(Mock(is_authenticated=False)), view)
        )
        self.assertFalse(self.permission.has_permission(self.request(None), view))

    def test_object_permission_checks_explicit_requirement_against_object(self):
        obj = object()
        view = SimpleNamespace(
            permission_required_by_action={"update": ["users.change_user"]},
            action="update",
        )
        user = Mock(is_authenticated=True)
        user.has_perms.return_value = True

        self.assertTrue(self.permission.has_object_permission(self.request(user), view, obj))
        user.has_perms.assert_called_once_with(("users.change_user",), obj)

        user.has_perms.return_value = False
        self.assertFalse(self.permission.has_object_permission(self.request(user), view, obj))
        self.assertFalse(
            self.permission.has_object_permission(
                self.request(Mock(is_authenticated=False)), view, obj
            )
        )
        self.assertFalse(self.permission.has_object_permission(self.request(None), view, obj))

    def test_object_permission_fallback_allows_base_permission_behavior(self):
        self.assertTrue(
            self.permission.has_object_permission(
                self.request(Mock(is_authenticated=True)), SimpleNamespace(), object()
            )
        )
