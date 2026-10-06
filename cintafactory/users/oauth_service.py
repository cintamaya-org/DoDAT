# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import logging
import secrets
from base64 import b64encode
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify

from .models import OAuthAccount
from .oauth_providers import OAuthProvider
from cintafactory.url_safety import is_http_url


logger = logging.getLogger(__name__)


class OAuthError(Exception):
    def __init__(self, message: str, *, details: Any | None = None):
        super().__init__(message)
        self.details = details


def build_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorize_url(provider: OAuthProvider, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
    }
    params.update(provider.extra_authorize_params)
    return f"{provider.authorize_url}?{urlencode(params)}"


def exchange_code_for_token(provider: OAuthProvider, code: str, redirect_uri: str) -> dict[str, Any]:
    payload = {
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    headers = {}
    if provider.token_endpoint_auth_method == "client_secret_basic":
        client_id = quote_plus(provider.client_id, safe="")
        client_secret = quote_plus(provider.client_secret, safe="")
        credentials = b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {credentials}"
    elif provider.token_endpoint_auth_method == "client_secret_post":
        payload["client_id"] = provider.client_id
        payload["client_secret"] = provider.client_secret
    else:
        raise OAuthError("Méthode d'authentification OAuth non prise en charge.")
    return _request_json(provider.token_url, headers=headers, data=payload)


def fetch_userinfo(provider: OAuthProvider, access_token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    return _request_json(provider.userinfo_url, headers=headers)


def resolve_oauth_user(
    provider: OAuthProvider,
    userinfo: dict[str, Any],
    token_data: dict[str, Any],
    *,
    request_user=None,
):
    mapping = provider.userinfo_mapping
    provider_user_id = _string_claim(userinfo, mapping.get("user_id"))
    if not provider_user_id:
        raise OAuthError("Identifiant utilisateur manquant dans la réponse du fournisseur.")
    email = _string_claim(userinfo, mapping.get("email"))
    email_verified = _extract_value(userinfo, mapping.get("email_verified"))
    first_name = _string_claim(userinfo, mapping.get("first_name"))
    last_name = _string_claim(userinfo, mapping.get("last_name"))
    full_name = _string_claim(userinfo, mapping.get("full_name"))
    if full_name and not (first_name or last_name):
        parts = full_name.strip().split()
        if parts:
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    account = OAuthAccount.objects.select_related("user").filter(
        provider=provider.slug,
        provider_user_id=provider_user_id,
    ).first()
    if account:
        _ensure_active_user(account.user)
        _update_account_tokens(account, token_data, userinfo, email=email)
        return account.user, account

    if request_user is not None and getattr(request_user, "is_authenticated", False):
        _ensure_active_user(request_user)
        account = OAuthAccount.objects.create(
            user=request_user,
            provider=provider.slug,
            provider_user_id=provider_user_id,
            email=email or "",
            raw_profile=userinfo,
        )
        _update_account_tokens(account, token_data, userinfo, save=True)
        return request_user, account

    allow_email_linking = getattr(settings, "OAUTH_ALLOW_EMAIL_LINKING", True)
    if not provider.allow_user_creation:
        if email and allow_email_linking:
            if _is_explicitly_false(email_verified):
                raise OAuthError("L'adresse e-mail fournie par le fournisseur n'est pas vérifiée.")
            matches = list(
                get_user_model()
                .objects.filter(email__iexact=email)
                .order_by("id")[:2]
            )
            if len(matches) > 1:
                raise OAuthError("Plusieurs comptes locaux correspondent à l'adresse fournie.")
            if matches:
                existing = matches[0]
                _ensure_active_user(existing)
                account = OAuthAccount.objects.create(
                    user=existing,
                    provider=provider.slug,
                    provider_user_id=provider_user_id,
                    email=email,
                    raw_profile=userinfo,
                )
                _update_account_tokens(account, token_data, userinfo, save=True)
                return existing, account
        raise OAuthError("Aucun compte local unique ne correspond à l'identité OAuth.")

    if email and allow_email_linking and email_verified is not False:
        existing = (
            get_user_model()
            .objects.filter(email__iexact=email)
            .order_by("id")
            .first()
        )
        if existing:
            account = OAuthAccount.objects.create(
                user=existing,
                provider=provider.slug,
                provider_user_id=provider_user_id,
                email=email,
                raw_profile=userinfo,
            )
            _update_account_tokens(account, token_data, userinfo, save=True)
            return existing, account

    UserModel = get_user_model()
    username = _build_unique_username(UserModel, email or full_name or provider_user_id, provider.slug)
    user = UserModel.objects.create_user(
        username=username,
        email=email or "",
        first_name=first_name,
        last_name=last_name,
        password=None,
    )
    account = OAuthAccount.objects.create(
        user=user,
        provider=provider.slug,
        provider_user_id=provider_user_id,
        email=email or "",
        raw_profile=userinfo,
    )
    _update_account_tokens(account, token_data, userinfo, save=True)
    return user, account


def _request_json(url: str, *, headers: dict[str, str] | None = None, data: dict[str, Any] | None = None):
    timeout = getattr(settings, "OAUTH_HTTP_TIMEOUT", 10)
    if not is_http_url(url):
        logger.warning("OAuth URL rejected (non-http scheme): %s", url)
        raise OAuthError("URL du fournisseur OAuth invalide.", details=url)
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    request_data = None
    if data is not None:
        encoded = urlencode(data).encode("utf-8")
        request_data = encoded
        req_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=request_data, headers=req_headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
        logger.warning("OAuth HTTP error (%s)", exc.code)
        raise OAuthError("Erreur lors de l'appel OAuth.", details=body) from exc
    except URLError as exc:
        logger.warning("OAuth URL error: %s", exc)
        raise OAuthError("Impossible de contacter le fournisseur OAuth.", details=str(exc)) from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("OAuth JSON error: %s", exc)
        raise OAuthError("Réponse OAuth invalide.", details=payload) from exc


def _build_unique_username(user_model, base: str, provider_slug: str) -> str:
    cleaned = slugify(base) if base else ""
    if not cleaned:
        cleaned = f"{provider_slug}-user"
    candidate = cleaned
    counter = 1
    while user_model.objects.filter(username=candidate).exists():
        counter += 1
        candidate = f"{cleaned}-{counter}"
    return candidate


def _extract_value(payload: dict[str, Any], field: str | None):
    if not field:
        return None
    value = payload.get(field)
    return value


def _string_claim(payload: dict[str, Any], field: str | None) -> str:
    value = _extract_value(payload, field)
    return value.strip() if isinstance(value, str) else ""


def _is_explicitly_false(value: Any) -> bool:
    if value is False or (isinstance(value, int) and not isinstance(value, bool) and value == 0):
        return True
    return isinstance(value, str) and value.strip().lower() in {"false", "0", "no"}


def _ensure_active_user(user) -> None:
    if not getattr(user, "is_active", True):
        raise OAuthError("Ce compte local est désactivé.")


def _update_account_tokens(
    account: OAuthAccount,
    token_data: dict[str, Any],
    profile: dict[str, Any],
    *,
    email: str | None = None,
    save: bool = True,
):
    account.access_token = token_data.get("access_token", "") or ""
    refresh_token = token_data.get("refresh_token")
    if refresh_token:
        account.refresh_token = refresh_token
    if email is not None and email != "":
        account.email = email
    account.token_type = token_data.get("token_type", "") or account.token_type
    scope = token_data.get("scope")
    if scope:
        account.scope = scope
    expires_in = token_data.get("expires_in")
    if expires_in:
        try:
            expires = timedelta(seconds=int(expires_in))
        except (TypeError, ValueError):
            expires = None
        if expires is not None:
            account.token_expires_at = timezone.now() + expires
    account.raw_profile = profile or account.raw_profile
    if save:
        account.save(update_fields=[
            "access_token",
            "refresh_token",
            "token_expires_at",
            "token_type",
            "scope",
            "email",
            "raw_profile",
            "updated_at",
        ])
