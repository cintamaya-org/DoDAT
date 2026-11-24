# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from .external import ExternalNotificationBackend, ExternalNotificationEvent, ExternalNotificationResult

logger = logging.getLogger(__name__)


class EmailNotificationBackend(ExternalNotificationBackend):
    """Email-based external notification backend."""

    slug = "email"
    verbose_name = "Email Notification"

    default_subject_template = "cintafactory/notifications/email_subject.txt"
    default_text_template = "cintafactory/notifications/email_body.txt"

    def _normalize_recipients(self, raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            candidate = raw.strip()
            return [candidate] if candidate else []
        if isinstance(raw, Sequence):
            recipients = []
            for item in raw:
                candidate = str(item or "").strip()
                if candidate:
                    recipients.append(candidate)
            return recipients
        return []

    def _collect_recipients(self, event: ExternalNotificationEvent) -> list[str]:
        recipients = self._normalize_recipients(self.config.get("to"))
        if self.config.get("use_event_user_email", True) and event.user_email:
            event_email = event.user_email.strip()
            if event_email and event_email not in recipients:
                recipients.append(event_email)
        return recipients

    def _build_context(self, event: ExternalNotificationEvent) -> dict[str, Any]:
        context: dict[str, Any] = {
            "event": event,
            "kind": event.kind,
            "title": event.title,
            "message": event.message,
            "level": event.level,
            "occurred_at": event.resolved_occurred_at(),
            "user_id": event.user_id,
            "user_email": event.user_email,
            "user_display": event.user_display,
            "dat_id": event.dat_id,
            "dat_reference": event.dat_reference,
            "dat_display_reference": event.dat_display_reference or event.dat_reference,
            "dat_title": event.dat_title,
            "dat_status": event.dat_status,
            "target_url": event.target_url,
            "created_by_id": event.created_by_id,
            "created_by_display": event.created_by_display,
            "extra_data": event.extra_data,
            "backend_config": self.config,
        }
        custom_context = self.config.get("context", {})
        if isinstance(custom_context, Mapping):
            context.update(custom_context)
        return context

    def is_enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def send(self, event: ExternalNotificationEvent) -> ExternalNotificationResult | None:
        logger.info(
            "external_email.build.start backend=%s kind=%s dat_id=%s dat_reference=%s user_email=%s",
            self.slug,
            event.kind,
            event.dat_id,
            event.dat_reference,
            event.user_email,
        )
        to = self._collect_recipients(event)
        if not to:
            logger.warning(
                "external_email.build.skipped backend=%s reason=no_recipients kind=%s dat_id=%s",
                self.slug,
                event.kind,
                event.dat_id,
            )
            return ExternalNotificationResult(
                backend=self.slug,
                sent=False,
                detail="no recipients",
            )

        context = self._build_context(event)
        subject_template = str(self.config.get("subject_template") or self.default_subject_template)
        text_template = str(self.config.get("text_template") or self.default_text_template)
        html_template = str(self.config.get("html_template") or "").strip()

        try:
            subject = render_to_string(subject_template, context).strip().replace("\n", " ").replace("\r", " ")
            body_text = render_to_string(text_template, context)
            body_html = render_to_string(html_template, context) if html_template else ""
        except Exception:
            logger.exception(
                "external_email.build.failed backend=%s kind=%s subject_template=%s text_template=%s html_template=%s",
                self.slug,
                event.kind,
                subject_template,
                text_template,
                html_template or "-",
            )
            raise

        message = EmailMultiAlternatives(
            subject=subject,
            body=body_text,
            from_email=self.config.get("from_email"),
            to=to,
            cc=self._normalize_recipients(self.config.get("cc")),
            bcc=self._normalize_recipients(self.config.get("bcc")),
            reply_to=self._normalize_recipients(self.config.get("reply_to")),
        )
        if body_html:
            message.attach_alternative(body_html, "text/html")

        logger.info(
            "external_email.build.done backend=%s kind=%s recipients=%s cc=%s bcc=%s subject_template=%s text_template=%s",
            self.slug,
            event.kind,
            len(message.to or []),
            len(message.cc or []),
            len(message.bcc or []),
            subject_template,
            text_template,
        )

        connection = get_connection(fail_silently=False)
        try:
            logger.info(
                "external_email.connect.start backend=%s host=%s port=%s tls=%s ssl=%s timeout=%s",
                self.slug,
                getattr(settings, "EMAIL_HOST", ""),
                getattr(settings, "EMAIL_PORT", ""),
                getattr(settings, "EMAIL_USE_TLS", False),
                getattr(settings, "EMAIL_USE_SSL", False),
                getattr(settings, "EMAIL_TIMEOUT", ""),
            )
            connection.open()
            logger.info("external_email.connect.ok backend=%s", self.slug)
            sent = connection.send_messages([message]) or 0
            logger.info(
                "external_email.send.done backend=%s sent_count=%s kind=%s dat_id=%s",
                self.slug,
                sent,
                event.kind,
                event.dat_id,
            )
        except Exception:
            logger.exception(
                "external_email.send.failed backend=%s kind=%s dat_id=%s dat_reference=%s",
                self.slug,
                event.kind,
                event.dat_id,
                event.dat_reference,
            )
            raise
        finally:
            try:
                connection.close()
            except Exception:
                logger.exception("external_email.connect.close_failed backend=%s", self.slug)

        return ExternalNotificationResult(
            backend=self.slug,
            sent=bool(sent),
            detail=f"sent={sent}",
        )
