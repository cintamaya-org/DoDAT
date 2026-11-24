# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from django.conf import settings
from django.utils import timezone
from django.utils.module_loading import import_string

from .config import load_external_notifications_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalNotificationEvent:
    """Portable payload for external notification handlers."""

    kind: str
    title: str
    message: str = ""
    level: str = "info"
    occurred_at: datetime | None = None
    user_id: str | None = None
    user_email: str | None = None
    user_display: str | None = None
    dat_id: str | None = None
    dat_reference: str | None = None
    dat_title: str | None = None
    dat_status: str | None = None
    target_url: str | None = None
    created_by_id: str | None = None
    created_by_display: str | None = None
    extra_data: dict[str, Any] = field(default_factory=dict)
    dat_display_reference: str | None = None

    def resolved_occurred_at(self) -> datetime:
        return self.occurred_at or timezone.now()


@dataclass(frozen=True)
class ExternalNotificationResult:
    backend: str
    sent: bool
    detail: str | None = None


class ExternalNotificationBackend(ABC):
    """
    Base class for external notification handlers.

    Subclasses should implement `send` and keep all delivery mechanics inside
    the subclass. The base class remains stable as the extension point.
    Example:

        class WebhookBackend(ExternalNotificationBackend):
            slug = "webhook"
            def send(self, event):
                ...
    """

    slug = "base"
    verbose_name = "External Notification Backend"

    def __init__(self, *, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def is_enabled(self) -> bool:
        return True

    def should_send(self, event: ExternalNotificationEvent) -> bool:
        return True

    @abstractmethod
    def send(self, event: ExternalNotificationEvent) -> ExternalNotificationResult | None:
        raise NotImplementedError


def _normalize_backend_entry(entry: Any) -> tuple[str, Mapping[str, Any]]:
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, Mapping):
        path = entry.get("path") or entry.get("backend") or entry.get("class")
        if not path:
            raise ValueError("External notification backend entry missing 'path'.")
        config = entry.get("config", {})
        if config is None:
            config = {}
        if not isinstance(config, Mapping):
            raise ValueError("External notification backend config must be a mapping.")
        return str(path), config
    raise ValueError("External notification backend entry must be a string or mapping.")


def get_external_notification_backends() -> list[ExternalNotificationBackend]:
    """Instantiate the configured external notification backends."""

    configured: Sequence[Any] = load_external_notifications_config()
    if not configured:
        configured = getattr(settings, "EXTERNAL_NOTIFICATION_BACKENDS", ())
    configs: Mapping[str, Mapping[str, Any]] = getattr(
        settings, "EXTERNAL_NOTIFICATION_BACKEND_CONFIG", {}
    )
    backends: list[ExternalNotificationBackend] = []
    for entry in configured:
        path, entry_config = _normalize_backend_entry(entry)
        if path in configs:
            merged_config = dict(configs.get(path, {}))
            merged_config.update(entry_config)
        else:
            merged_config = dict(entry_config)
        backend_cls = import_string(path)
        backends.append(backend_cls(config=merged_config))
    return backends


def dispatch_external_notification(
    event: ExternalNotificationEvent,
    *,
    backends: Iterable[ExternalNotificationBackend] | None = None,
    raise_errors: bool = False,
) -> list[ExternalNotificationResult]:
    """Dispatch an event to all enabled external notification backends."""

    results: list[ExternalNotificationResult] = []
    resolved_backends = list(backends) if backends is not None else get_external_notification_backends()
    for backend in resolved_backends:
        backend_name = getattr(backend, "slug", backend.__class__.__name__)
        if not backend.is_enabled():
            results.append(
                ExternalNotificationResult(backend=backend_name, sent=False, detail="disabled")
            )
            continue
        if not backend.should_send(event):
            results.append(
                ExternalNotificationResult(backend=backend_name, sent=False, detail="filtered")
            )
            continue
        try:
            result = backend.send(event)
            if result is None:
                result = ExternalNotificationResult(backend=backend_name, sent=True)
            results.append(result)
        except Exception as exc:
            if raise_errors:
                raise
            logger.exception("External notification backend failed: %s", backend_name)
            results.append(
                ExternalNotificationResult(
                    backend=backend_name,
                    sent=False,
                    detail=str(exc),
                )
            )
    return results


def build_event_from_user_notification(notification) -> ExternalNotificationEvent:
    """Best-effort conversion from a UserNotification instance to a portable payload."""

    dat = getattr(notification, "dat", None)
    display_reference = getattr(dat, "display_reference", None)
    user = getattr(notification, "user", None)
    created_by = getattr(notification, "created_by", None)
    user_display = None
    if user is not None:
        if hasattr(user, "get_full_name"):
            user_display = user.get_full_name()
        if not user_display and hasattr(user, "get_username"):
            user_display = user.get_username()
        if not user_display:
            user_display = getattr(user, "email", None)
    return ExternalNotificationEvent(
        kind="user_notification",
        title=getattr(notification, "title", "") or "",
        message=getattr(notification, "message", "") or "",
        level=getattr(notification, "level", "info") or "info",
        occurred_at=getattr(notification, "created_at", None),
        user_id=str(getattr(user, "pk", "")) if user is not None else None,
        user_email=getattr(user, "email", None),
        user_display=user_display,
        dat_id=str(getattr(dat, "pk", "")) if dat is not None else None,
        dat_reference=getattr(dat, "reference", None),
        dat_display_reference=display_reference() if callable(display_reference) else None,
        dat_title=getattr(dat, "title", None),
        dat_status=getattr(dat, "status", None),
        target_url=getattr(notification, "target_url", None),
        created_by_id=str(getattr(created_by, "pk", "")) if created_by is not None else None,
        created_by_display=getattr(notification, "created_by_display", None),
        extra_data=getattr(notification, "extra_data", None) or {},
    )
