# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

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
            username="limited-provisioner", password="pwd", role=self.role, can_use_api=True,
        )
        self.actor.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="users",
                codename__in=[
                    "view_user", "add_user", "change_user", "delete_user",
                    "view_businessgroup", "add_businessgroup", "change_businessgroup", "delete_businessgroup",
                ],
            )
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = reverse("api:user-list")

    def _payload(self, **extra):
        return {"username": "provisioned-user", "password": "chosen-password", "role": str(self.role.pk), **extra}

    def test_non_superuser_cannot_read_or_write_user_and_group_routes(self):
        group_url = reverse("api:business-group-list")
        for method, url, data in (
            (self.api.get, self.url, None),
            (self.api.options, self.url, None),
            (self.api.post, self.url, self._payload()),
            (self.api.get, group_url, None),
            (self.api.options, group_url, None),
            (self.api.post, group_url, {}),
        ):
            with self.subTest(method=method.__name__, url=url):
                response = method(url, data, format="json") if data is not None else method(url)
                self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.filter(username="provisioned-user").exists())

    def test_superuser_can_read_user_and_group_routes(self):
        administrator = get_user_model().objects.create_superuser(
            username="users-api-administrator", password="pwd", role=self.role, can_use_api=True,
        )
        self.api.force_authenticate(administrator)

        self.assertEqual(self.api.get(self.url).status_code, 200)
        self.assertEqual(self.api.get(reverse("api:business-group-list")).status_code, 200)

    def test_api_documentation_requires_superuser_and_api_opt_in(self):
        for name in ("api-schema", "api-docs", "api-redoc"):
            with self.subTest(endpoint=name):
                self.assertEqual(self.api.get(reverse(name)).status_code, 403)

        administrator = get_user_model().objects.create_superuser(
            username="docs-api-administrator", password="pwd", role=self.role,
        )
        self.api.force_authenticate(administrator)
        for name in ("api-schema", "api-docs", "api-redoc"):
            with self.subTest(endpoint=name, opt_in=False):
                self.assertEqual(self.api.get(reverse(name)).status_code, 403)

        administrator.can_use_api = True
        administrator.save(update_fields=["can_use_api"])
        for name in ("api-schema", "api-docs", "api-redoc"):
            with self.subTest(endpoint=name, user="superuser"):
                self.assertEqual(self.api.get(reverse(name)).status_code, 200)

    def test_superuser_can_provision_privileged_account(self):
        administrator = get_user_model().objects.create_superuser(
            username="provisioning-administrator", password="pwd", role=self.role, can_use_api=True,
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
        response = self.api.delete(reverse("api:user-detail", args=[privileged.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=privileged.pk).exists())

    def test_limited_user_cannot_update_normal_account(self):
        target = get_user_model().objects.create_user(username="normal-target", password="pwd", role=self.role)
        response = self.api.patch(
            reverse("api:user-detail", args=[target.pk]), {"first_name": "Updated"}, format="json",
        )
        self.assertEqual(response.status_code, 403)
        target.refresh_from_db()
        self.assertEqual(target.first_name, "")
