# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from collections.abc import Iterable

from rest_framework.permissions import BasePermission, DjangoModelPermissions


def is_authenticated_superuser(user: object) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    )


class IsSuperuser(BasePermission):
    """Allow access only to authenticated Django superusers."""

    def has_permission(self, request, view) -> bool:
        return is_authenticated_superuser(getattr(request, "user", None))


class CanUseAPI(BasePermission):
    """Require an authenticated user whose API access was explicitly enabled."""

    message = "API access is disabled for this account."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(
            getattr(user, "is_authenticated", False)
            and getattr(user, "can_use_api", False)
        )


class GranularModelPermissions(DjangoModelPermissions):
    """Support per-view or per-action permission overrides with model perms fallback."""

    def _normalize_required(self, required: object) -> tuple[str, ...] | None:
        if required is None:
            return None
        if isinstance(required, str):
            return (required,)
        if isinstance(required, Iterable):
            return tuple(required)
        return None

    def _get_view_required(self, view: object) -> tuple[str, ...] | None:
        mapping = getattr(view, "permission_required_by_action", None)
        action = getattr(view, "action", None)
        if mapping and action in mapping:
            return self._normalize_required(mapping[action])
        required = getattr(view, "permission_required", None)
        return self._normalize_required(required)

    def has_permission(self, request, view) -> bool:
        required = self._get_view_required(view)
        if required is not None:
            user = request.user
            return bool(user and user.is_authenticated and user.has_perms(required))
        try:
            return super().has_permission(request, view)
        except AssertionError:
            user = request.user
            return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj) -> bool:
        required = self._get_view_required(view)
        if required is not None:
            user = request.user
            return bool(user and user.is_authenticated and user.has_perms(required, obj))
        return super().has_object_permission(request, view, obj)
