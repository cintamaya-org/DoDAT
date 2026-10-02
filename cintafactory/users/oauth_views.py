# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.core.exceptions import SuspiciousOperation
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET

from .oauth_providers import get_oauth_provider, list_oauth_providers
from .oauth_service import (
    OAuthError,
    build_authorize_url,
    build_oauth_state,
    exchange_code_for_token,
    fetch_userinfo,
    resolve_oauth_user,
)


logger = logging.getLogger(__name__)

SESSION_STATE_KEY = "oauth_state"
SESSION_PROVIDER_KEY = "oauth_provider"
SESSION_NEXT_KEY = "oauth_next"


class LoginViewWithProviders(auth_views.LoginView):
    template_name = "registration/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = getattr(self, "request", None)
        print(f"[OAuth] login page load path={getattr(request, 'path', '?')}")
        icon_map = {
            "google": "imgs/google_logo.svg",
            "microsoft": "imgs/microsoft_logo.svg",
            "amazon": "imgs/amazon_logo.svg",
            "okta": "imgs/okta_logo.svg",
        }
        providers = []
        for provider in list_oauth_providers():
            has_client_id = bool(provider.client_id)
            has_credentials = has_client_id and bool(provider.client_secret)
            reason_parts = []
            if not has_client_id:
                reason_parts.append("missing_client_id")
            if not provider.client_secret:
                reason_parts.append("missing_client_auth")
            print(
                "[OAuth] provider status:"
                f" {provider.slug}"
                f" enabled={provider.enabled}"
                f" client_id_set={has_client_id}"
                f" credentials_set={has_credentials}"
                f" reason={','.join(reason_parts) or 'none'}"
            )
            providers.append(
                {
                    "slug": provider.slug,
                    "label": provider.label,
                    "login_url": reverse("oauth_login", args=[provider.slug]),
                    "enabled": provider.enabled,
                    "icon": icon_map.get(provider.slug, ""),
                }
            )
        context["oauth_providers"] = providers
        return context


@require_GET
def oauth_login(request: HttpRequest, provider: str) -> HttpResponse:
    provider_config = get_oauth_provider(provider)
    if provider_config is None:
        logger.warning("OAuth provider not found: %s", provider)
        raise Http404("Fournisseur OAuth introuvable.")
    if not provider_config.enabled:
        logger.warning("OAuth provider not configured: %s", provider_config.slug)
        messages.error(request, "Le fournisseur OAuth n'est pas configure.")
        return redirect("login")
    state = build_oauth_state()
    request.session[SESSION_STATE_KEY] = state
    request.session[SESSION_PROVIDER_KEY] = provider_config.slug
    next_url = request.GET.get("next") or settings.LOGIN_REDIRECT_URL
    request.session[SESSION_NEXT_KEY] = next_url
    redirect_uri = request.build_absolute_uri(reverse("oauth_callback", args=[provider_config.slug]))
    print(f"[OAuth] login redirect_uri={redirect_uri}")
    auth_url = build_authorize_url(provider_config, redirect_uri, state)
    return redirect(auth_url)


@require_GET
def oauth_callback(request: HttpRequest, provider: str) -> HttpResponse:
    provider_config = get_oauth_provider(provider)
    if provider_config is None:
        logger.warning("OAuth callback provider not found: %s", provider)
        raise Http404("Fournisseur OAuth introuvable.")
    if not provider_config.enabled:
        logger.warning("OAuth callback provider not configured: %s", provider_config.slug)
        messages.error(request, "Le fournisseur OAuth n'est pas configure.")
        return redirect("login")
    if request.GET.get("error"):
        error = request.GET.get("error_description") or request.GET.get("error")
        logger.warning("OAuth callback error: provider=%s error=%s", provider_config.slug, error)
        messages.error(request, f"Authentification OAuth refusee: {error}")
        return redirect("login")
    state = request.GET.get("state")
    expected_state = request.session.get(SESSION_STATE_KEY)
    if not state or expected_state != state:
        logger.warning(
            "OAuth callback invalid state: provider=%s expected=%s received=%s",
            provider_config.slug,
            expected_state,
            state,
        )
        raise SuspiciousOperation("OAuth state invalide.")
    code = request.GET.get("code")
    if not code:
        logger.warning("OAuth callback missing code: provider=%s", provider_config.slug)
        messages.error(request, "Le code OAuth est manquant.")
        return redirect("login")
    redirect_uri = request.build_absolute_uri(reverse("oauth_callback", args=[provider_config.slug]))
    try:
        token_data = exchange_code_for_token(provider_config, code, redirect_uri)
        access_token = token_data.get("access_token")
        if not access_token:
            raise OAuthError("Le fournisseur OAuth n'a pas renvoye de jeton d'acces.")
        userinfo = fetch_userinfo(provider_config, access_token)
        user, _account = resolve_oauth_user(
            provider_config,
            userinfo,
            token_data,
            request_user=request.user if request.user.is_authenticated else None,
        )
        logger.info(
            "OAuth callback success: provider=%s user_id=%s email=%s",
            provider_config.slug,
            getattr(user, "id", None),
            getattr(user, "email", None),
        )
    except OAuthError as exc:
        logger.warning("OAuth callback failed: %s", exc)
        messages.error(request, str(exc))
        return redirect("login")

    backend = None
    backends = getattr(settings, "AUTHENTICATION_BACKENDS", None)
    if backends:
        backend = backends[0]
    if backend is None:
        backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user, backend=backend)
    request.session.pop(SESSION_STATE_KEY, None)
    request.session.pop(SESSION_PROVIDER_KEY, None)
    next_url = request.session.pop(SESSION_NEXT_KEY, None) or settings.LOGIN_REDIRECT_URL
    return redirect(next_url)
