# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.request import Request, urlopen

from django.utils import timezone

from cintafactory.url_safety import is_http_url

from .external import ExternalNotificationBackend, ExternalNotificationEvent, ExternalNotificationResult


class TeamsWebhookBackend(ExternalNotificationBackend):
    """Microsoft Teams incoming webhook backend."""

    slug = "teams-webhook"
    verbose_name = "Microsoft Teams Webhook"

    def _resolve_url(self) -> str:
        url = str(self.config.get("url") or "")
        return url.strip()

    def is_enabled(self) -> bool:
        url = self._resolve_url()
        return bool(url) and is_http_url(url)

    def send(self, event: ExternalNotificationEvent) -> ExternalNotificationResult | None:
        url = self._resolve_url()
        if not is_http_url(url):
            return ExternalNotificationResult(
                backend=self.slug,
                sent=False,
                detail="invalid url",
            )

        payload = self._build_payload(event)
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = int(self.config.get("timeout", 10))
        with urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None)
            if status and 200 <= status < 300:
                return ExternalNotificationResult(backend=self.slug, sent=True)
            return ExternalNotificationResult(
                backend=self.slug,
                sent=False,
                detail=f"http {status}",
            )

    def _build_payload(self, event: ExternalNotificationEvent) -> Mapping[str, Any]:
        occurred_at = event.resolved_occurred_at().astimezone(timezone.get_current_timezone())
        dat_label = " · ".join(item for item in [event.dat_display_reference or event.dat_reference, event.dat_title] if item)
        facts = []
        if dat_label:
            facts.append({"title": "DAT", "value": dat_label})
        if event.user_display:
            facts.append({"title": "Utilisateur", "value": event.user_display})
        facts.append({"title": "Date", "value": f"{occurred_at:%Y-%m-%d %H:%M:%S}"})
        if event.target_url:
            facts.append({"title": "URL", "value": event.target_url})
        if event.extra_data:
            status_from = event.extra_data.get("status_from")
            status_to = event.extra_data.get("status_to")
            if status_from or status_to:
                facts.append(
                    {
                        "title": "Statut section",
                        "value": f"{status_from or '—'} → {status_to or '—'}",
                    }
                )
            section_title = event.extra_data.get("section_title")
            if section_title:
                facts.append({"title": "Section", "value": str(section_title)})

        body_blocks = []
        if event.title:
            body_blocks.append(
                {
                    "type": "TextBlock",
                    "text": event.title,
                    "weight": "Bolder",
                    "size": "Medium",
                    "wrap": True,
                }
            )
        if event.message:
            body_blocks.append({"type": "TextBlock", "text": event.message, "wrap": True})
        if facts:
            body_blocks.append(
                {
                    "type": "FactSet",
                    "facts": facts,
                }
            )
        actions = []
        if event.target_url:
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "Ouvrir le DAT",
                    "url": event.target_url,
                }
            )

        card = {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": body_blocks,
            "actions": actions,
        }
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }
