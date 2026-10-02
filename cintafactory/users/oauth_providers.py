# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from django.conf import settings

from cintafactory.url_safety import is_http_url


@dataclass(frozen=True)
class OAuthProvider:
    slug: str
    label: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: tuple[str, ...]
    extra_authorize_params: Mapping[str, str]
    userinfo_mapping: Mapping[str, str]
    allow_user_creation: bool = True
    token_endpoint_auth_method: str = "client_secret_post"

    @property
    def enabled(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret
            and is_http_url(self.authorize_url)
            and is_http_url(self.token_url)
            and is_http_url(self.userinfo_url)
            and self.token_endpoint_auth_method in {"client_secret_basic", "client_secret_post"}
        )


def _load_provider(slug: str, config: Mapping[str, Any]) -> OAuthProvider:
    scopes = config.get("scopes") or ()
    if isinstance(scopes, (list, tuple)):
        scope_tuple = tuple(str(item) for item in scopes if item)
    else:
        scope_tuple = (str(scopes),) if scopes else ()
    extra = config.get("extra_authorize_params") or {}
    if not isinstance(extra, dict):
        extra = {}
    mapping = config.get("userinfo_mapping") or {}
    if not isinstance(mapping, dict):
        mapping = {}
    defaults = {
        "user_id": "sub",
        "email": "email",
        "email_verified": "email_verified",
        "first_name": "given_name",
        "last_name": "family_name",
        "full_name": "name",
    }
    defaults.update({str(key): str(value) for key, value in mapping.items() if value})
    return OAuthProvider(
        slug=slug,
        label=str(config.get("label") or slug.title()),
        client_id=str(config.get("client_id") or ""),
        client_secret=str(config.get("client_secret") or ""),
        authorize_url=str(config.get("authorize_url") or ""),
        token_url=str(config.get("token_url") or ""),
        userinfo_url=str(config.get("userinfo_url") or ""),
        scopes=scope_tuple,
        extra_authorize_params={str(key): str(value) for key, value in extra.items()},
        userinfo_mapping=defaults,
        allow_user_creation=_as_bool(config.get("allow_user_creation", True), default=True),
        token_endpoint_auth_method=str(
            config.get("token_endpoint_auth_method") or "client_secret_post"
        ).strip().lower(),
    )


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def get_oauth_provider(slug: str) -> OAuthProvider | None:
    providers = getattr(settings, "OAUTH_PROVIDERS", {}) or {}
    config = providers.get(slug)
    if not isinstance(config, Mapping):
        return None
    return _load_provider(slug, config)


def list_oauth_providers() -> list[OAuthProvider]:
    providers = getattr(settings, "OAUTH_PROVIDERS", {}) or {}
    items: list[OAuthProvider] = []
    for slug, config in providers.items():
        if not isinstance(config, Mapping):
            continue
        items.append(_load_provider(slug, config))
    return items


def list_enabled_oauth_providers() -> list[OAuthProvider]:
    return [provider for provider in list_oauth_providers() if provider.enabled]
