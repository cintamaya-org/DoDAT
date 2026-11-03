# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError

from .api import UserSerializer
from .models import Role


class UserSerializerValidationTests(SimpleTestCase):
    def setUp(self):
        self.role = Role(name="Member", slug="member", is_admin_role=False)
        self.actor = get_user_model()(username="provisioner", role=self.role)

    def serializer(self, actor=None, instance=None):
        return UserSerializer(instance=instance, context={"request": SimpleNamespace(user=actor or self.actor)})

    def test_only_superusers_can_change_api_opt_in_including_revocation(self):
        for actor in (self.actor, AnonymousUser()):
            for value in (True, False):
                with self.subTest(actor=type(actor).__name__, value=value), self.assertRaises(PermissionDenied):
                    self.serializer(actor=actor).validate({"can_use_api": value})
        with self.assertRaises(PermissionDenied):
            UserSerializer().validate({"can_use_api": True})

    def test_superuser_can_assign_privileged_fields(self):
        self.actor.is_superuser = True
        role = Role(name="Admin", slug="admin", is_admin_role=True)
        attrs = {"is_staff": True, "is_superuser": True, "can_use_api": True, "role": role}
        self.assertIs(self.serializer().validate(attrs), attrs)

    def test_non_superuser_cannot_assign_staff_superuser_or_admin_roles(self):
        for role in (Role(slug="admin", is_admin_role=False), Role(slug="custom", is_admin_role=True)):
            with self.subTest(role=role.slug), self.assertRaises(ValidationError) as error:
                self.serializer().validate({"is_staff": True, "is_superuser": True, "role": role})
            self.assertEqual(set(error.exception.detail), {"is_staff", "is_superuser", "role"})

    def test_privileged_instances_cannot_be_edited_by_limited_callers(self):
        for changes in ({"is_staff": True}, {"is_superuser": True}, {"role": Role(slug="admin", is_admin_role=True)}):
            target = get_user_model()(username="target", role=self.role)
            for field, value in changes.items():
                setattr(target, field, value)
            with self.subTest(changes=tuple(changes)), self.assertRaises(PermissionDenied):
                self.serializer(instance=target).validate({"first_name": "Updated"})

    def test_group_default_role_is_pinned_and_privileged_default_is_rejected(self):
        group = object()
        with mock.patch("users.api._get_default_role_for_group", return_value=self.role) as default_role:
            attrs = self.serializer().validate({"business_group": group})
        default_role.assert_called_once_with(group)
        self.assertIs(attrs["role"], self.role)
        with mock.patch("users.api._get_default_role_for_group", return_value=Role(slug="admin", is_admin_role=True)):
            with self.assertRaises(ValidationError):
                self.serializer().validate({"business_group": group})

    def test_normal_update_keeps_existing_role_without_selecting_another_default(self):
        target = get_user_model()(username="target", role=self.role)
        attrs = {"first_name": "Updated", "is_staff": False}
        with mock.patch("users.api._get_default_role_for_group") as default_role:
            self.assertEqual(self.serializer(instance=target).validate(attrs), attrs)
        default_role.assert_not_called()


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class UserSerializerPasswordTests(SimpleTestCase):
    def test_create_hashes_password_without_passing_plaintext_to_model_create(self):
        user = get_user_model()(username="created")
        user.save = mock.Mock()
        with mock.patch("rest_framework.serializers.ModelSerializer.create", return_value=user) as create:
            result = UserSerializer().create({"username": "created", "password": "chosen-secret"})
        create.assert_called_once_with({"username": "created"})
        self.assertIs(result, user)
        self.assertNotEqual(user.password, "chosen-secret")
        self.assertTrue(user.check_password("chosen-secret"))
        user.save.assert_called_once_with(update_fields=["password"])

    def test_update_preserves_password_when_omitted_and_hashes_replacement(self):
        user = get_user_model()(username="target")
        user.set_password("original-secret")
        user.save = mock.Mock()
        serializer = UserSerializer()
        with mock.patch("rest_framework.serializers.ModelSerializer.update", return_value=user) as update:
            serializer.update(user, {"first_name": "Updated"})
            self.assertTrue(user.check_password("original-secret"))
            user.save.assert_not_called()
            serializer.update(user, {"password": "replacement-secret"})
        self.assertTrue(user.check_password("replacement-secret"))
        self.assertFalse(user.check_password("original-secret"))
        user.save.assert_called_once_with(update_fields=["password"])
        self.assertNotIn("password", update.call_args.args[1])
