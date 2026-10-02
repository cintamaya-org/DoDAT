# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from importlib import import_module
from io import BytesIO
from types import SimpleNamespace

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from .models import BusinessDirection, BusinessGroup, TechnicalDirection, Role
from .forms import BusinessGroupForm
from .oauth_providers import OAuthProvider, list_enabled_oauth_providers
from .oauth_service import build_authorize_url, resolve_oauth_user
from .profile_pictures import (
    DEFAULT_PROFILE_EXTENSION,
    _extract_extension,
    build_profile_picture_storage_name,
    process_profile_picture_upload,
)

from PIL import Image


class UserDetailViewTests(TestCase):
    def setUp(self):
        self.UserModel = get_user_model()
        self.superuser = self.UserModel.objects.create_superuser(
            username="super-admin",
            email="admin@example.com",
            password="pwd",
        )
        self.tech_direction = TechnicalDirection.objects.create(name="Tech Dir", slug="tech-dir-detail")
        self.business_direction = BusinessDirection.objects.create(name="Direction Métier", slug="business-dir-detail")
        self.role = Role.objects.create(
            name="Architecte Détail",
            slug="architecte-detail",
            technical_direction=self.tech_direction,
        )
        self.group = BusinessGroup.objects.create(
            name="Groupe Détail",
            direction=self.tech_direction,
            responsible=self.superuser,
            business_direction=self.business_direction,
        )
        self.superuser.business_group = self.group
        self.superuser.role = self.role
        self.superuser.save(update_fields=["business_group", "role"])
        self.target_user = self.UserModel.objects.create_user(
            username="detail-user",
            password="pwd",
            role=self.role,
            business_group=self.group,
        )

    def test_superuser_can_view_user_detail(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("users:user_detail", kwargs={"pk": self.target_user.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Profil utilisateur")
        self.assertContains(response, self.target_user.username)
        self.assertContains(response, self.role.name)

    def test_non_superuser_is_forbidden(self):
        outsider = self.UserModel.objects.create_user(
            username="outsider",
            password="pwd",
            role=self.role,
            business_group=self.group,
        )
        self.client.force_login(outsider)
        response = self.client.get(reverse("users:user_detail", kwargs={"pk": self.target_user.pk}))
        self.assertEqual(response.status_code, 403)


class ManagementListPaginationTests(TestCase):
    def setUp(self):
        self.UserModel = get_user_model()
        self.superuser = self.UserModel.objects.create_superuser(
            username="pagination-admin",
            email="pagination-admin@example.com",
            password="pwd",
        )
        self.client.force_login(self.superuser)
        self.initial_totals = {
            "users": self.UserModel.objects.count(),
            "groups": BusinessGroup.objects.count(),
            "technical_directions": TechnicalDirection.objects.count(),
            "business_directions": BusinessDirection.objects.count(),
            "roles": Role.objects.count(),
        }

        self.technical_directions = [
            TechnicalDirection(name=f"Direction {index:02d}", slug=f"direction-{index:02d}")
            for index in range(30)
        ]
        TechnicalDirection.objects.bulk_create(self.technical_directions)
        self.business_directions = [
            BusinessDirection(name=f"Métier {index:02d}", slug=f"metier-{index:02d}")
            for index in range(30)
        ]
        BusinessDirection.objects.bulk_create(self.business_directions)
        Role.objects.bulk_create(
            [
                Role(
                    name=f"Role {index:02d}",
                    slug=f"role-{index:02d}",
                    technical_direction=self.technical_directions[index],
                )
                for index in range(30)
            ]
        )
        BusinessGroup.objects.bulk_create(
            [
                BusinessGroup(
                    name=f"Groupe {index:02d}",
                    direction=self.technical_directions[index],
                    business_direction=self.business_directions[index],
                    responsible=self.superuser,
                )
                for index in range(30)
            ]
        )
        self.UserModel.objects.bulk_create(
            [self.UserModel(username=f"pagination-user-{index:02d}") for index in range(30)]
        )

    def assert_second_page_is_bounded(self, url, *, expected_total):
        response = self.client.get(url, {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["paginator"].count, expected_total)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertLessEqual(len(response.context["object_list"]), 25)
        self.assertContains(response, "Page 2 sur 2")
        self.assertContains(response, "Aller à la page")

    def test_user_crud_is_paginated(self):
        self.assert_second_page_is_bounded(
            "/users/manage/users/crud/",
            expected_total=self.initial_totals["users"] + 30,
        )

    def test_group_list_is_paginated(self):
        self.assert_second_page_is_bounded(
            reverse("users:group_list"),
            expected_total=self.initial_totals["groups"] + 30,
        )

    def test_technical_direction_list_is_paginated(self):
        self.assert_second_page_is_bounded(
            reverse("users:technical_direction_list"),
            expected_total=self.initial_totals["technical_directions"] + 30,
        )

    def test_business_direction_list_is_paginated(self):
        self.assert_second_page_is_bounded(
            reverse("users:business_direction_list"),
            expected_total=self.initial_totals["business_directions"] + 30,
        )

    def test_role_crud_is_paginated(self):
        self.assert_second_page_is_bounded(
            "/users/manage/roles/crud/",
            expected_total=self.initial_totals["roles"] + 30,
        )


class ResponsibleRemoteSelectTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            username="responsible-search-admin",
            password="pwd",
        )
        self.regular_user = get_user_model().objects.create_user(
            username="responsible-search-regular",
            password="pwd",
        )
        get_user_model().objects.bulk_create(
            [
                get_user_model()(
                    username=f"responsible-option-{index:02d}",
                    email=f"responsible-{index:02d}@example.com",
                )
                for index in range(35)
            ]
        )
        self.url = reverse("users:user_options")

    def test_endpoint_is_superuser_only(self):
        self.client.force_login(self.regular_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_endpoint_caps_results_and_searches_server_side(self):
        self.client.force_login(self.superuser)

        response = self.client.get(self.url)
        search_response = self.client.get(self.url, {"q": "responsible-34@example.com"})

        payload = response.json()
        self.assertEqual(len(payload["options"]), 30)
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["max_results"], 30)
        self.assertEqual(len(search_response.json()["options"]), 1)

    def test_group_form_loads_only_selected_responsible(self):
        form = BusinessGroupForm(
            data={
                "name": "Remote group",
                "direction": "",
                "responsible": self.regular_user.pk,
                "business_direction": "",
            }
        )

        responsible_field = form.fields["responsible"]
        self.assertEqual(list(responsible_field.queryset), [self.regular_user])
        self.assertEqual(responsible_field.widget.attrs["data-remote-select-limit"], "30")
        self.assertEqual(
            responsible_field.widget.attrs["data-remote-select-url"],
            self.url,
        )


class UserModelConstraintTests(TestCase):
    def setUp(self):
        self.UserModel = get_user_model()
        self.superuser = self.UserModel.objects.create_superuser(
            username="constraint-admin",
            email="constraint-admin@example.com",
            password="pwd",
        )
        self.direction_a = TechnicalDirection.objects.create(name="Direction A", slug="direction-a")
        self.direction_b = TechnicalDirection.objects.create(name="Direction B", slug="direction-b")
        self.role_a = Role.objects.create(name="Role A", slug="role-a", technical_direction=self.direction_a)
        self.role_b = Role.objects.create(name="Role B", slug="role-b", technical_direction=self.direction_b)
        self.role_transverse = Role.objects.create(name="Role Transverse", slug="role-transverse")
        self.group_a = BusinessGroup.objects.create(
            name="Groupe A",
            direction=self.direction_a,
            responsible=self.superuser,
        )
        self.group_b = BusinessGroup.objects.create(
            name="Groupe B",
            direction=self.direction_b,
            responsible=self.superuser,
        )
        self.superuser.business_group = self.group_a
        self.superuser.role = self.role_a
        self.superuser.save(update_fields=["business_group", "role"])

    def test_user_without_role_is_invalid(self):
        user = self.UserModel(
            username="no-role",
            email="no-role@example.com",
            business_group=self.group_a,
        )
        user.set_password("pwd")
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_user_role_must_match_group_direction(self):
        user = self.UserModel(
            username="direction-mismatch",
            email="mismatch@example.com",
            role=self.role_b,
            business_group=self.group_a,
        )
        user.set_password("pwd")
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_default_role_is_assigned_when_missing(self):
        user = self.UserModel.objects.create_user(
            username="auto-role",
            password="pwd",
            business_group=self.group_a,
        )
        self.assertEqual(user.business_group, self.group_a)
        self.assertEqual(user.role, self.role_a)

    def test_role_requires_group_when_direction_is_set(self):
        user = self.UserModel(
            username="needs-group",
            email="needs-group@example.com",
            role=self.role_a,
        )
        user.set_password("pwd")
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_directionless_role_cannot_have_group(self):
        user = self.UserModel(
            username="transverse-grouped",
            email="transverse@example.com",
            role=self.role_transverse,
            business_group=self.group_a,
        )
        user.set_password("pwd")
        with self.assertRaises(ValidationError):
            user.full_clean()


class OAuthProviderTests(TestCase):
    def test_cintamaya_provider_is_not_configured(self):
        self.assertNotIn("cintamaya", settings.OAUTH_PROVIDERS)

    def test_cintamaya_login_and_callback_routes_return_404(self):
        login_response = self.client.get(reverse("oauth_login", kwargs={"provider": "cintamaya"}))
        callback_response = self.client.get(reverse("oauth_callback", kwargs={"provider": "cintamaya"}))

        self.assertEqual(login_response.status_code, 404)
        self.assertEqual(callback_response.status_code, 404)

    def test_login_page_omits_cintamaya_provider(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cintamaya", {provider["slug"] for provider in response.context["oauth_providers"]})

    def test_profile_page_omits_cintamaya_provider(self):
        user = get_user_model().objects.create_user(
            username="oauth-profile-user",
            email="oauth-profile-user@example.com",
            password="pwd",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("account:profile"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cintamaya", {provider["slug"] for provider in response.context["oauth_providers"]})

    def test_migration_removes_only_cintamaya_oauth_accounts(self):
        user = get_user_model().objects.create_user(
            username="oauth-migration-user",
            email="oauth-migration-user@example.com",
            password="pwd",
        )
        cintamaya_account = user.oauth_accounts.create(
            provider="cintamaya",
            provider_user_id="cintamaya-user",
            email=user.email,
            access_token="cintamaya-access-token",
            refresh_token="cintamaya-refresh-token",
        )
        other_account = user.oauth_accounts.create(
            provider="google",
            provider_user_id="google-user",
            email=user.email,
            access_token="google-access-token",
        )
        migration = import_module("users.migrations.0002_remove_cintamaya_oauth_accounts")

        migration.remove_cintamaya_oauth_accounts(
            django_apps,
            SimpleNamespace(connection=connection),
        )

        self.assertFalse(user.oauth_accounts.filter(pk=cintamaya_account.pk).exists())
        self.assertTrue(user.oauth_accounts.filter(pk=other_account.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())

    def test_list_enabled_oauth_providers_filters_missing_credentials(self):
        self.assertEqual(list_enabled_oauth_providers(), [])
        with self.settings(
            OAUTH_PROVIDERS={
                "demo": {
                    "client_id": "id",
                    "client_secret": "secret",
                    "authorize_url": "https://example.com/auth",
                    "token_url": "https://example.com/token",
                    "userinfo_url": "https://example.com/user",
                    "scopes": ["profile", "email"],
                },
                "disabled": {
                    "client_id": "",
                    "client_secret": "",
                },
            }
        ):
            providers = list_enabled_oauth_providers()
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].slug, "demo")

    def test_build_authorize_url_includes_scopes(self):
        provider = OAuthProvider(
            slug="demo",
            label="Demo",
            client_id="client",
            client_secret="secret",
            authorize_url="https://example.com/authorize",
            token_url="https://example.com/token",
            userinfo_url="https://example.com/userinfo",
            scopes=("email", "profile"),
            extra_authorize_params={"prompt": "consent"},
            userinfo_mapping={},
        )
        url = build_authorize_url(provider, "https://app.local/callback", "state123")
        self.assertIn("client_id=client", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fapp.local%2Fcallback", url)
        self.assertIn("scope=email+profile", url)
        self.assertIn("prompt=consent", url)


class OAuthServiceTests(TestCase):
    def setUp(self):
        self.UserModel = get_user_model()
        self.provider = OAuthProvider(
            slug="demo",
            label="Demo",
            client_id="client",
            client_secret="secret",
            authorize_url="https://example.com/authorize",
            token_url="https://example.com/token",
            userinfo_url="https://example.com/userinfo",
            scopes=("email",),
            extra_authorize_params={},
            userinfo_mapping={"user_id": "sub", "email": "email"},
        )

    def test_resolve_oauth_user_links_existing_account(self):
        user = self.UserModel.objects.create_user(username="existing", email="existing@example.com", password="pwd")
        account = user.oauth_accounts.create(
            provider=self.provider.slug,
            provider_user_id="abc123",
            email=user.email,
        )
        resolved_user, resolved_account = resolve_oauth_user(
            self.provider,
            {"sub": "abc123", "email": user.email},
            {"access_token": "token"},
        )
        self.assertEqual(resolved_user, user)
        self.assertEqual(resolved_account.id, account.id)
        resolved_account.refresh_from_db()
        self.assertEqual(resolved_account.access_token, "token")

    def test_resolve_oauth_user_links_by_email(self):
        user = self.UserModel.objects.create_user(username="linked", email="link@example.com", password="pwd")
        resolved_user, resolved_account = resolve_oauth_user(
            self.provider,
            {"sub": "unique", "email": "link@example.com", "email_verified": True},
            {"access_token": "token"},
        )
        self.assertEqual(resolved_user, user)
        self.assertEqual(resolved_account.user, user)

    def test_resolve_oauth_user_creates_new_user(self):
        resolved_user, resolved_account = resolve_oauth_user(
            self.provider,
            {"sub": "provider-user", "email": ""},
            {"access_token": "token"},
        )
        self.assertEqual(resolved_account.user, resolved_user)
        self.assertTrue(resolved_user.username.startswith("provider-user") or resolved_user.username.startswith("demo-user"))


class ProfilePictureTests(TestCase):
    def test_extract_extension_handles_paths(self):
        self.assertEqual(_extract_extension("avatar.JPG"), ".jpg")
        self.assertEqual(_extract_extension("/var/lib/cinta/uploads/avatar.png"), ".png")
        self.assertEqual(_extract_extension(""), "")

    def test_storage_name_falls_back_on_invalid_extension(self):
        name = build_profile_picture_storage_name(None, "avatar.txt")
        self.assertTrue(name.endswith(DEFAULT_PROFILE_EXTENSION))

    def test_process_profile_picture_upload_resizes_and_converts(self):
        image = Image.new("RGB", (800, 600), color="red")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        upload = SimpleUploadedFile("avatar.png", buffer.read(), content_type="image/png")
        processed = process_profile_picture_upload(upload)
        self.assertEqual(processed.content_type, "image/png")
        processed.seek(0)
        processed_image = Image.open(processed)
        self.assertEqual(processed_image.size, (350, 350))

    def test_process_profile_picture_upload_rejects_extension(self):
        upload = SimpleUploadedFile("avatar.txt", b"not an image", content_type="text/plain")
        with self.assertRaises(ValidationError):
            process_profile_picture_upload(upload)
