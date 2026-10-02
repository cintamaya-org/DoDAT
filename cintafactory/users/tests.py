# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import base64
import json
from importlib import import_module
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SuspiciousOperation, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from .models import BusinessDirection, BusinessGroup, OAuthAccount, TechnicalDirection, Role
from .forms import BusinessGroupForm, UserForm
from . import oauth_views
from .oauth_providers import OAuthProvider, get_oauth_provider, list_enabled_oauth_providers
from .oauth_service import OAuthError, build_authorize_url, resolve_oauth_user
from .oauth_views import SESSION_NEXT_KEY, SESSION_PROVIDER_KEY, SESSION_STATE_KEY
from .profile_pictures import (
    DEFAULT_PROFILE_EXTENSION,
    _extract_extension,
    build_profile_picture_storage_name,
    process_profile_picture_upload,
)

from PIL import Image


class _OAuthHTTPResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


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
    def _provider_config(self, slug):
        shared = {
            "client_id": f"{slug}-client",
            "client_secret": f"{slug}-secret",
            "allow_user_creation": False,
        }
        if slug == "microsoft":
            return {
                **shared,
                "label": "Microsoft",
                "authorize_url": "https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize",
                "token_url": "https://login.microsoftonline.com/tenant/oauth2/v2.0/token",
                "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
                "scopes": ("openid", "email", "profile"),
                "token_endpoint_auth_method": "client_secret_post",
            }
        if slug == "amazon":
            return {
                **shared,
                "label": "Amazon",
                "authorize_url": "https://www.amazon.com/ap/oa",
                "token_url": "https://api.amazon.com/auth/o2/token",
                "userinfo_url": "https://api.amazon.com/user/profile",
                "scopes": ("profile", "profile:user_id"),
                "userinfo_mapping": {
                    "user_id": "user_id",
                    "email": "email",
                    "full_name": "name",
                },
                "token_endpoint_auth_method": "client_secret_post",
            }
        return {
            **shared,
            "label": "Okta",
            "authorize_url": "https://example.okta.com/oauth2/default/v1/authorize",
            "token_url": "https://example.okta.com/oauth2/default/v1/token",
            "userinfo_url": "https://example.okta.com/oauth2/default/v1/userinfo",
            "scopes": ("openid", "email", "profile"),
            "token_endpoint_auth_method": "client_secret_basic",
        }

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

    def test_provider_is_disabled_without_endpoints_or_supported_auth_method(self):
        with self.settings(
            OAUTH_PROVIDERS={
                "missing-endpoint": {
                    "client_id": "id",
                    "client_secret": "secret",
                    "authorize_url": "https://example.com/authorize",
                    "token_url": "https://example.com/token",
                },
                "unsupported-auth": {
                    "client_id": "id",
                    "client_secret": "secret",
                    "authorize_url": "https://example.com/authorize",
                    "token_url": "https://example.com/token",
                    "userinfo_url": "https://example.com/userinfo",
                    "token_endpoint_auth_method": "private_key_jwt",
                },
            }
        ):
            self.assertFalse(get_oauth_provider("missing-endpoint").enabled)
            self.assertFalse(get_oauth_provider("unsupported-auth").enabled)

    def test_requested_provider_configs_have_expected_login_settings(self):
        providers = {
            slug: self._provider_config(slug)
            for slug in ("microsoft", "amazon", "okta")
        }
        with self.settings(OAUTH_PROVIDERS=providers):
            microsoft = get_oauth_provider("microsoft")
            amazon = get_oauth_provider("amazon")
            okta = get_oauth_provider("okta")

        self.assertTrue(all(provider.enabled for provider in (microsoft, amazon, okta)))
        self.assertFalse(any(provider.allow_user_creation for provider in (microsoft, amazon, okta)))
        self.assertEqual(amazon.scopes, ("profile", "profile:user_id"))
        self.assertEqual(amazon.userinfo_mapping["user_id"], "user_id")
        self.assertEqual(okta.token_endpoint_auth_method, "client_secret_basic")

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

    def test_mocked_provider_login_round_trips_for_all_requested_providers(self):
        provider_profiles = {
            "microsoft": {
                "sub": "microsoft-user",
                "email": "microsoft@example.com",
                "email_verified": True,
                "given_name": "Mira",
                "family_name": "Microsoft",
            },
            "amazon": {
                "user_id": "amazon-user",
                "email": "amazon@example.com",
                "name": "Amina Amazon",
            },
            "okta": {
                "sub": "okta-user",
                "email": "okta@example.com",
                "email_verified": True,
                "given_name": "Oona",
                "family_name": "Okta",
            },
        }

        for slug, profile in provider_profiles.items():
            with self.subTest(provider=slug):
                self.client.logout()
                config = self._provider_config(slug)
                local_user = get_user_model().objects.create_user(
                    username=f"{slug}-local",
                    email=profile["email"],
                    password="pwd",
                )
                with self.settings(
                    OAUTH_PROVIDERS={slug: config},
                    OAUTH_ALLOW_EMAIL_LINKING=True,
                ):
                    start = self.client.get(
                        reverse("oauth_login", args=[slug]),
                        {"next": "/after-sso/"},
                    )
                    self.assertEqual(start.status_code, 302)
                    authorize = parse_qs(urlparse(start["Location"]).query)
                    self.assertEqual(authorize["response_type"], ["code"])
                    self.assertEqual(authorize["scope"], [" ".join(config["scopes"])])
                    state = authorize["state"][0]
                    self.assertEqual(self.client.session[SESSION_PROVIDER_KEY], slug)

                    def mock_urlopen(request, timeout):
                        if request.full_url == config["token_url"]:
                            self.assertEqual(timeout, settings.OAUTH_HTTP_TIMEOUT)
                            payload = parse_qs(request.data.decode("utf-8"))
                            self.assertEqual(payload["code"], ["mock-code"])
                            self.assertEqual(
                                payload["redirect_uri"],
                                [f"http://testserver{reverse('oauth_callback', args=[slug])}"],
                            )
                            if config["token_endpoint_auth_method"] == "client_secret_basic":
                                credentials = base64.b64encode(
                                    f'{config["client_id"]}:{config["client_secret"]}'.encode("utf-8")
                                ).decode("ascii")
                                self.assertEqual(
                                    request.get_header("Authorization"),
                                    f"Basic {credentials}",
                                )
                                self.assertNotIn("client_secret", payload)
                            else:
                                self.assertEqual(payload["client_id"], [config["client_id"]])
                                self.assertEqual(payload["client_secret"], [config["client_secret"]])
                            return _OAuthHTTPResponse({"access_token": "mock-access-token"})
                        if request.full_url == config["userinfo_url"]:
                            self.assertEqual(
                                request.get_header("Authorization"),
                                "Bearer mock-access-token",
                            )
                            return _OAuthHTTPResponse(profile)
                        self.fail(f"Unexpected OAuth URL: {request.full_url}")

                    with patch("users.oauth_service.urlopen", side_effect=mock_urlopen):
                        callback = self.client.get(
                            reverse("oauth_callback", args=[slug]),
                            {"state": state, "code": "mock-code"},
                        )

                self.assertEqual(callback.status_code, 302)
                self.assertEqual(callback["Location"], "/after-sso/")
                local_user.refresh_from_db()
                account = local_user.oauth_accounts.get(provider=slug)
                expected_id = profile.get("user_id") or profile.get("sub")
                self.assertEqual(account.provider_user_id, expected_id)
                self.assertEqual(account.access_token, "mock-access-token")
                self.assertEqual(
                    str(self.client.session["_auth_user_id"]),
                    str(local_user.pk),
                )

    def test_oauth_login_rejects_external_next_url(self):
        config = self._provider_config("okta")
        with self.settings(OAUTH_PROVIDERS={"okta": config}):
            response = self.client.get(
                reverse("oauth_login", args=["okta"]),
                {"next": "https://attacker.example/"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session[SESSION_NEXT_KEY], settings.LOGIN_REDIRECT_URL)

    def test_oauth_callback_rejects_state_from_another_provider(self):
        config = self._provider_config("okta")
        with self.settings(OAUTH_PROVIDERS={"okta": config}):
            session = self.client.session
            session[SESSION_STATE_KEY] = "valid-state"
            session[SESSION_PROVIDER_KEY] = "microsoft"
            session.save()

            response = self.client.get(
                reverse("oauth_callback", args=["okta"]),
                {"state": "valid-state", "code": "mock-code"},
            )

        self.assertEqual(response.status_code, 400)

    def test_oauth_callback_rejects_wrong_state_value(self):
        config = self._provider_config("okta")
        with self.settings(OAUTH_PROVIDERS={"okta": config}):
            session = self.client.session
            session[SESSION_STATE_KEY] = "expected-state"
            session[SESSION_PROVIDER_KEY] = "okta"
            session.save()

            response = self.client.get(
                reverse("oauth_callback", args=["okta"]),
                {"state": "wrong-state", "code": "mock-code"},
            )

        self.assertEqual(response.status_code, 400)

    def test_oauth_provider_error_requires_valid_state_and_clears_flow(self):
        config = self._provider_config("okta")
        with self.settings(OAUTH_PROVIDERS={"okta": config}):
            session = self.client.session
            session[SESSION_STATE_KEY] = "valid-state"
            session[SESSION_PROVIDER_KEY] = "okta"
            session[SESSION_NEXT_KEY] = "/after-sso/"
            session.save()

            response = self.client.get(
                reverse("oauth_callback", args=["okta"]),
                {
                    "state": "valid-state",
                    "error": "access_denied",
                    "error_description": "private provider detail",
                },
            )

        self.assertEqual(response.status_code, 302)
        session = self.client.session
        self.assertNotIn(SESSION_STATE_KEY, session)
        self.assertNotIn(SESSION_PROVIDER_KEY, session)
        self.assertNotIn(SESSION_NEXT_KEY, session)


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

    def _preprovisioned_provider(self):
        return OAuthProvider(
            slug="microsoft",
            label="Microsoft",
            client_id="client",
            client_secret="secret",
            authorize_url="https://example.com/authorize",
            token_url="https://example.com/token",
            userinfo_url="https://example.com/userinfo",
            scopes=("openid", "email", "profile"),
            extra_authorize_params={},
            userinfo_mapping={"user_id": "sub", "email": "email", "email_verified": "email_verified"},
            allow_user_creation=False,
        )

    def test_preprovisioned_provider_links_unique_email_when_verified_claim_missing(self):
        user = self.UserModel.objects.create_user(
            username="preprovisioned",
            email="preprovisioned@example.com",
            password="pwd",
        )
        resolved_user, account = resolve_oauth_user(
            self._preprovisioned_provider(),
            {"sub": "new-provider-id", "email": "PREPROVISIONED@example.com"},
            {"access_token": "token"},
        )

        self.assertEqual(resolved_user, user)
        self.assertEqual(account.user, user)

    def test_preprovisioned_provider_rejects_unknown_identity_without_creating_user(self):
        users_before = self.UserModel.objects.count()
        accounts_before = OAuthAccount.objects.count()

        with self.assertRaises(OAuthError):
            resolve_oauth_user(
                self._preprovisioned_provider(),
                {"sub": "unknown-id", "email": "unknown@example.com"},
                {"access_token": "token"},
            )

        self.assertEqual(self.UserModel.objects.count(), users_before)
        self.assertEqual(OAuthAccount.objects.count(), accounts_before)

    def test_preprovisioned_provider_requires_email_linking_setting(self):
        user = self.UserModel.objects.create_user(
            username="linking-disabled",
            email="linking-disabled@example.com",
            password="pwd",
        )
        with self.settings(OAUTH_ALLOW_EMAIL_LINKING=False):
            with self.assertRaises(OAuthError):
                resolve_oauth_user(
                    self._preprovisioned_provider(),
                    {"sub": "linking-disabled-id", "email": user.email},
                    {"access_token": "token"},
                )
        self.assertFalse(user.oauth_accounts.exists())

    def test_preprovisioned_provider_rejects_ambiguous_email(self):
        self.UserModel.objects.create_user(
            username="duplicate-one",
            email="duplicate@example.com",
            password="pwd",
        )
        self.UserModel.objects.create_user(
            username="duplicate-two",
            email="DUPLICATE@example.com",
            password="pwd",
        )

        with self.assertRaisesMessage(OAuthError, "Plusieurs comptes locaux"):
            resolve_oauth_user(
                self._preprovisioned_provider(),
                {"sub": "duplicate-id", "email": "duplicate@example.com"},
                {"access_token": "token"},
            )

    def test_preprovisioned_provider_rejects_explicitly_unverified_email(self):
        self.UserModel.objects.create_user(
            username="unverified",
            email="unverified@example.com",
            password="pwd",
        )

        with self.assertRaisesMessage(OAuthError, "n'est pas verifiee"):
            resolve_oauth_user(
                self._preprovisioned_provider(),
                {
                    "sub": "unverified-id",
                    "email": "unverified@example.com",
                    "email_verified": False,
                },
                {"access_token": "token"},
            )

    def test_preprovisioned_provider_rejects_inactive_users(self):
        user = self.UserModel.objects.create_user(
            username="inactive-sso",
            email="inactive-sso@example.com",
            password="pwd",
        )
        user.is_active = False
        user.save(update_fields=["is_active"])

        with self.assertRaisesMessage(OAuthError, "desactive"):
            resolve_oauth_user(
                self._preprovisioned_provider(),
                {"sub": "inactive-id", "email": "inactive-sso@example.com"},
                {"access_token": "token"},
            )

    def test_preprovisioned_provider_reuses_existing_identity_without_email(self):
        user = self.UserModel.objects.create_user(
            username="linked-sso",
            email="linked-sso@example.com",
            password="pwd",
        )
        account = user.oauth_accounts.create(
            provider="microsoft",
            provider_user_id="existing-provider-id",
            email=user.email,
        )

        resolved_user, resolved_account = resolve_oauth_user(
            self._preprovisioned_provider(),
            {"sub": "existing-provider-id"},
            {"access_token": "renewed-token"},
        )

        self.assertEqual(resolved_user, user)
        self.assertEqual(resolved_account.pk, account.pk)
        resolved_account.refresh_from_db()
        self.assertEqual(resolved_account.access_token, "renewed-token")


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


class UserFormCoverageTests(TestCase):
    def setUp(self):
        self.UserModel = get_user_model()
        self.admin = self.UserModel.objects.create_superuser(
            username="form-admin",
            email="form-admin@example.com",
            password="pwd",
        )
        self.direction_a = TechnicalDirection.objects.create(name="Form Direction A", slug="form-direction-a")
        self.direction_b = TechnicalDirection.objects.create(name="Form Direction B", slug="form-direction-b")
        self.role_a = Role.objects.create(name="Form Role A", slug="form-role-a", technical_direction=self.direction_a)
        self.role_b = Role.objects.create(name="Form Role B", slug="form-role-b", technical_direction=self.direction_b)
        self.role_transverse = Role.objects.create(name="Form Transverse", slug="form-transverse")
        self.group_a = BusinessGroup.objects.create(
            name="Form Group A",
            direction=self.direction_a,
            responsible=self.admin,
        )
        self.group_b = BusinessGroup.objects.create(
            name="Form Group B",
            direction=self.direction_b,
            responsible=self.admin,
        )

    def _data(self, *, role=None, group=None, **overrides):
        data = {
            "username": "form-new-user",
            "email": "form-new-user@example.com",
            "first_name": "Form",
            "last_name": "User",
            "role": str(role.pk) if role else "",
            "business_group": str(group.pk) if group else "",
            "is_active": "on",
            "is_staff": "",
            "is_superuser": "",
            "password1": "",
            "password2": "",
        }
        data.update(overrides)
        return data

    def test_new_user_role_group_rules_cover_missing_mismatched_and_transverse_roles(self):
        missing_group = UserForm(data=self._data(role=self.role_a))
        self.assertFalse(missing_group.is_valid())
        self.assertIn("business_group", missing_group.errors)

        mismatched_group = UserForm(data=self._data(role=self.role_a, group=self.group_b))
        self.assertFalse(mismatched_group.is_valid())
        self.assertIn("role", mismatched_group.errors)

        transverse_group = UserForm(data=self._data(role=self.role_transverse, group=self.group_a))
        self.assertFalse(transverse_group.is_valid())
        self.assertIn("business_group", transverse_group.errors)

        missing_role = UserForm(data=self._data())
        self.assertFalse(missing_role.is_valid())
        self.assertIn("role", missing_role.errors)

    def test_password_pair_errors_and_validation_are_handled(self):
        base = {"role": self.role_a, "group": self.group_a}
        missing_first = UserForm(data=self._data(**base, password2="Long-passphrase-493!"))
        self.assertFalse(missing_first.is_valid())
        self.assertIn("password1", missing_first.errors)

        missing_confirmation = UserForm(data=self._data(**base, password1="Long-passphrase-493!"))
        self.assertFalse(missing_confirmation.is_valid())
        self.assertIn("password2", missing_confirmation.errors)

        mismatch = UserForm(
            data=self._data(**base, password1="Long-passphrase-493!", password2="Another-passphrase-294!"),
        )
        self.assertFalse(mismatch.is_valid())
        self.assertIn("password2", mismatch.errors)

        weak = UserForm(
            data=self._data(**base, password1="Long-passphrase-493!", password2="Long-passphrase-493!"),
        )
        with mock.patch("users.forms.validate_password", side_effect=ValidationError("weak password")):
            self.assertFalse(weak.is_valid())
        self.assertIn("password1", weak.errors)

    def test_valid_save_sets_password_or_unusable_password(self):
        no_password = UserForm(data=self._data(role=self.role_a, group=self.group_a))
        self.assertTrue(no_password.is_valid(), no_password.errors)
        user = no_password.save()
        self.assertFalse(user.has_usable_password())

        with mock.patch("users.forms.validate_password") as validate_password:
            with_password = UserForm(
                data=self._data(
                    role=self.role_a,
                    group=self.group_a,
                    username="form-password-user",
                    email="form-password-user@example.com",
                    password1="Long-passphrase-493!",
                    password2="Long-passphrase-493!",
                )
            )
            self.assertTrue(with_password.is_valid(), with_password.errors)
            saved = with_password.save()
        validate_password.assert_called_once()
        self.assertTrue(saved.check_password("Long-passphrase-493!"))

    def test_profile_picture_cleaner_processes_uploaded_files(self):
        upload = SimpleUploadedFile("avatar.png", b"fake image", content_type="image/png")
        processed = SimpleUploadedFile("processed.png", b"processed", content_type="image/png")
        form = UserForm(data=self._data(role=self.role_a, group=self.group_a), files={"profile_picture": upload})
        with mock.patch("users.forms.process_profile_picture_upload", return_value=processed) as process:
            self.assertTrue(form.is_valid(), form.errors)
        process.assert_called_once_with(upload, field_name="profile_picture")
        self.assertIs(form.cleaned_data["profile_picture"], processed)


class OAuthViewTests(TestCase):
    provider_config = {
        "client_id": "demo-client",
        "client_secret": "demo-secret",
        "authorize_url": "https://provider.example.test/authorize",
        "token_url": "https://provider.example.test/token",
        "userinfo_url": "https://provider.example.test/userinfo",
        "scopes": ["openid", "email"],
    }

    def _configure_provider(self, *, enabled=True):
        config = dict(self.provider_config)
        if not enabled:
            config["client_secret"] = ""
        return self.settings(
            OAUTH_PROVIDERS={"demo": config},
            LOGIN_REDIRECT_URL="/home/",
        )

    def _seed_callback_session(self, *, provider="demo", next_url="/dashboard/"):
        session = self.client.session
        session[SESSION_STATE_KEY] = "known-state"
        session[SESSION_PROVIDER_KEY] = provider
        session[SESSION_NEXT_KEY] = next_url
        session.save()

    def test_login_redirect_stores_state_provider_and_safe_next(self):
        with self._configure_provider():
            response = self.client.get(reverse("oauth_login", args=["demo"]), {"next": "/dashboard/"})

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        session = self.client.session
        self.assertEqual(session[SESSION_STATE_KEY], query["state"][0])
        self.assertEqual(session[SESSION_PROVIDER_KEY], "demo")
        self.assertEqual(session[SESSION_NEXT_KEY], "/dashboard/")
        self.assertEqual(query["client_id"], ["demo-client"])

    def test_login_handles_unknown_disabled_and_external_next_provider(self):
        with self._configure_provider():
            response = self.client.get(
                reverse("oauth_login", args=["demo"]),
                {"next": "https://evil.example/"},
            )
            self.assertEqual(self.client.session[SESSION_NEXT_KEY], "/home/")
            self.assertEqual(response.status_code, 302)

        with self._configure_provider(enabled=False):
            disabled = self.client.get(reverse("oauth_login", args=["demo"]))
        self.assertEqual(disabled.status_code, 302)
        self.assertEqual(disabled["Location"], reverse("login"))

        with self._configure_provider():
            missing = self.client.get(reverse("oauth_login", args=["missing"]))
        self.assertEqual(missing.status_code, 404)

    @mock.patch("users.oauth_views.login")
    @mock.patch("users.oauth_views.resolve_oauth_user")
    @mock.patch("users.oauth_views.fetch_userinfo", return_value={"sub": "provider-user"})
    @mock.patch("users.oauth_views.exchange_code_for_token", return_value={"access_token": "access"})
    def test_callback_exchanges_code_logs_user_in_and_redirects_to_safe_next(
        self, exchange, fetch, resolve, login_user
    ):
        user = get_user_model().objects.create_user(username="oauth-view-user", password="pwd")
        resolve.return_value = (user, None)
        self._seed_callback_session()
        with self._configure_provider(), self.settings(AUTHENTICATION_BACKENDS=["custom.backend"]):
            response = self.client.get(
                reverse("oauth_callback", args=["demo"]),
                {"state": "known-state", "code": "auth-code"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashboard/")
        self.assertEqual(exchange.call_args.args[1], "auth-code")
        fetch.assert_called_once()
        resolve.assert_called_once()
        login_user.assert_called_once_with(mock.ANY, user, backend="custom.backend")
        session = self.client.session
        self.assertNotIn(SESSION_STATE_KEY, session)
        self.assertNotIn(SESSION_PROVIDER_KEY, session)
        self.assertNotIn(SESSION_NEXT_KEY, session)

    def test_callback_rejects_bad_state_unknown_provider_and_disabled_provider(self):
        with self._configure_provider():
            invalid_state = self.client.get(
                reverse("oauth_callback", args=["demo"]),
                {"state": "wrong", "code": "auth-code"},
            )
            self.assertEqual(invalid_state.status_code, 400)
            unknown = self.client.get(reverse("oauth_callback", args=["missing"]))
            self.assertEqual(unknown.status_code, 404)

        with self._configure_provider(enabled=False):
            disabled = self.client.get(reverse("oauth_callback", args=["demo"]))
        self.assertEqual(disabled.status_code, 302)
        self.assertEqual(disabled["Location"], reverse("login"))

    def test_callback_error_missing_code_and_oauth_failure_clear_session(self):
        with self._configure_provider():
            self._seed_callback_session()
            rejected = self.client.get(
                reverse("oauth_callback", args=["demo"]),
                {"state": "known-state", "error": "access_denied"},
            )
            self.assertEqual(rejected.status_code, 302)
            self.assertNotIn(SESSION_NEXT_KEY, self.client.session)

            self._seed_callback_session()
            missing_code = self.client.get(
                reverse("oauth_callback", args=["demo"]),
                {"state": "known-state"},
            )
            self.assertEqual(missing_code.status_code, 302)
            self.assertNotIn(SESSION_NEXT_KEY, self.client.session)

            self._seed_callback_session()
            with mock.patch(
                "users.oauth_views.exchange_code_for_token",
                return_value={},
            ):
                missing_token = self.client.get(
                    reverse("oauth_callback", args=["demo"]),
                    {"state": "known-state", "code": "auth-code"},
                )
            self.assertEqual(missing_token.status_code, 302)
            self.assertNotIn(SESSION_NEXT_KEY, self.client.session)

            self._seed_callback_session()
            with mock.patch("users.oauth_views.exchange_code_for_token", side_effect=OAuthError("provider offline")):
                failed = self.client.get(
                    reverse("oauth_callback", args=["demo"]),
                    {"state": "known-state", "code": "auth-code"},
                )
            self.assertEqual(failed.status_code, 302)
            self.assertNotIn(SESSION_NEXT_KEY, self.client.session)
