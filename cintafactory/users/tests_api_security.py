# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-License-Identifier: AGPL-3.0-only

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import Role


class UserProvisioningSecurityTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name="API security member", slug="api-security-member")
        self.admin_role, _ = Role.objects.get_or_create(
            slug="admin", defaults={"name": "API security administrator", "is_admin_role": True},
        )
        self.actor = get_user_model().objects.create_user(
            username="limited-provisioner", password="pwd", role=self.role,
        )
        self.actor.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="users", codename__in=[
                "add_user", "change_user", "delete_user",
            ])
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = reverse("api:user-list")

    def _payload(self, **extra):
        return {"username": "provisioned-user", "password": "chosen-password", "role": str(self.role.pk), **extra}

    def test_limited_creator_cannot_grant_staff_or_superuser(self):
        for field in ("is_staff", "is_superuser"):
            with self.subTest(field=field):
                response = self.api.post(self.url, self._payload(**{field: True}), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
                self.assertFalse(get_user_model().objects.filter(username="provisioned-user").exists())

    def test_limited_creator_cannot_assign_admin_role(self):
        response = self.api.post(self.url, self._payload(role=str(self.admin_role.pk)), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("role", response.data)

    def test_omitted_role_cannot_fall_back_to_admin(self):
        payload = self._payload()
        payload.pop("role")
        with mock.patch("users.api._get_default_role_for_group", return_value=self.admin_role):
            response = self.api.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_user_model().objects.filter(username="provisioned-user").exists())

    def test_limited_creator_can_create_normal_user_with_password(self):
        response = self.api.post(
            self.url, self._payload(is_staff=False, is_superuser=False), format="json",
        )
        self.assertEqual(response.status_code, 201)
        user = get_user_model().objects.get(username="provisioned-user")
        self.assertTrue(user.check_password("chosen-password"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.role, self.role)

    def test_superuser_can_provision_privileged_account(self):
        administrator = get_user_model().objects.create_superuser(
            username="provisioning-administrator", password="pwd", role=self.role,
        )
        self.api.force_authenticate(administrator)
        response = self.api.post(
            self.url, self._payload(is_staff=True, is_superuser=True, role=str(self.admin_role.pk)), format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(get_user_model().objects.get(username="provisioned-user").is_superuser)

    def test_limited_user_cannot_reset_privileged_password_with_object_backend(self):
        privileged = get_user_model().objects.create_user(
            username="privileged-target", password="original", role=self.role, is_staff=True,
        )
        with mock.patch("cintafactory.api.permissions.GranularModelPermissions.has_object_permission", return_value=True):
            response = self.api.patch(
                reverse("api:user-detail", args=[privileged.pk]), {"password": "attacker-choice"}, format="json",
            )
        self.assertEqual(response.status_code, 403)
        privileged.refresh_from_db()
        self.assertTrue(privileged.check_password("original"))

    def test_limited_user_cannot_delete_admin_role_account_with_object_backend(self):
        privileged = get_user_model().objects.create_user(
            username="admin-role-target", password="original", role=self.admin_role,
        )
        with mock.patch("cintafactory.api.permissions.GranularModelPermissions.has_object_permission", return_value=True):
            response = self.api.delete(reverse("api:user-detail", args=[privileged.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=privileged.pk).exists())

    def test_limited_user_can_update_normal_account_with_object_backend(self):
        target = get_user_model().objects.create_user(username="normal-target", password="pwd", role=self.role)
        with mock.patch("cintafactory.api.permissions.GranularModelPermissions.has_object_permission", return_value=True):
            response = self.api.patch(
                reverse("api:user-detail", args=[target.pk]), {"first_name": "Updated"}, format="json",
            )
        self.assertEqual(response.status_code, 200)
        target.refresh_from_db()
        self.assertEqual(target.first_name, "Updated")
