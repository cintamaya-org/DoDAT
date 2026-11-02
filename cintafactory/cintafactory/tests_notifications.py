# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import json
from datetime import datetime, timezone as datetime_timezone
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from .notifications.external import ExternalNotificationEvent
from .notifications.teams import TeamsWebhookBackend


class TeamsWebhookBackendTests(SimpleTestCase):
    def event(self, **overrides):
        values = {
            "kind": "section_status",
            "title": "Validation DAT",
            "message": "La section a changé de statut.",
            "occurred_at": datetime(2026, 10, 27, 12, 34, 56, tzinfo=datetime_timezone.utc),
        }
        values.update(overrides)
        return ExternalNotificationEvent(**values)

    def test_backend_requires_http_url(self):
        self.assertFalse(TeamsWebhookBackend(config={}).is_enabled())
        self.assertFalse(TeamsWebhookBackend(config={"url": "ftp://hooks.example.test"}).is_enabled())
        self.assertTrue(
            TeamsWebhookBackend(config={"url": " https://hooks.example.test/incoming "}).is_enabled()
        )

    def test_payload_includes_event_and_optional_context(self):
        backend = TeamsWebhookBackend()
        event = self.event(
            user_display="Alice",
            dat_reference="DAT-42",
            dat_title="Architecture",
            target_url="https://app.example.test/dat/42",
            extra_data={
                "status_from": "en cours",
                "status_to": "validé",
                "section_title": "Architecture technique",
            },
        )

        with timezone.override("UTC"):
            payload = backend._build_payload(event)

        card = payload["attachments"][0]["content"]
        self.assertEqual(card["type"], "AdaptiveCard")
        self.assertEqual(card["version"], "1.4")
        self.assertEqual(card["body"][0]["text"], "Validation DAT")
        self.assertEqual(card["body"][1]["text"], "La section a changé de statut.")
        facts = card["body"][2]["facts"]
        self.assertEqual(
            facts,
            [
                {"title": "DAT", "value": "DAT-42 · Architecture"},
                {"title": "Utilisateur", "value": "Alice"},
                {"title": "Date", "value": "2026-10-27 12:34:56"},
                {"title": "URL", "value": "https://app.example.test/dat/42"},
                {"title": "Statut section", "value": "en cours → validé"},
                {"title": "Section", "value": "Architecture technique"},
            ],
        )
        self.assertEqual(
            card["actions"],
            [{"type": "Action.OpenUrl", "title": "Ouvrir le DAT", "url": event.target_url}],
        )

    def test_payload_omits_absent_optional_content_and_handles_partial_status(self):
        with timezone.override("UTC"):
            empty_card = TeamsWebhookBackend()._build_payload(
                self.event(title="", message="", extra_data={})
            )["attachments"][0]["content"]
            partial_card = TeamsWebhookBackend()._build_payload(
                self.event(title="", message="", extra_data={"status_to": "validé"})
            )["attachments"][0]["content"]

        self.assertEqual(empty_card["body"], [
            {"type": "FactSet", "facts": [{"title": "Date", "value": "2026-10-27 12:34:56"}]}
        ])
        self.assertEqual(empty_card["actions"], [])
        self.assertEqual(
            partial_card["body"][0]["facts"][-1],
            {"title": "Statut section", "value": "— → validé"},
        )

    @mock.patch("cintafactory.notifications.teams.urlopen")
    def test_send_posts_json_and_accepts_success_status(self, urlopen):
        response = mock.MagicMock()
        response.status = 202
        urlopen.return_value.__enter__.return_value = response
        backend = TeamsWebhookBackend(config={"url": "https://hooks.example.test/incoming", "timeout": "4"})

        result = backend.send(self.event())

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://hooks.example.test/incoming")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data)["type"], "message")
        self.assertEqual(urlopen.call_args.kwargs, {"timeout": 4})
        self.assertTrue(result.sent)
        self.assertIsNone(result.detail)

    @mock.patch("cintafactory.notifications.teams.urlopen")
    def test_send_reports_non_success_status(self, urlopen):
        response = mock.MagicMock()
        response.status = 503
        urlopen.return_value.__enter__.return_value = response
        result = TeamsWebhookBackend(config={"url": "https://hooks.example.test/incoming"}).send(self.event())

        self.assertFalse(result.sent)
        self.assertEqual(result.detail, "http 503")

    @mock.patch("cintafactory.notifications.teams.urlopen")
    def test_send_rejects_invalid_url_without_network_call(self, urlopen):
        result = TeamsWebhookBackend(config={"url": "file:///etc/passwd"}).send(self.event())

        self.assertFalse(result.sent)
        self.assertEqual(result.detail, "invalid url")
        urlopen.assert_not_called()

    @mock.patch("cintafactory.notifications.teams.urlopen", side_effect=OSError("offline"))
    def test_send_propagates_transport_errors_to_dispatcher(self, _urlopen):
        with self.assertRaisesRegex(OSError, "offline"):
            TeamsWebhookBackend(config={"url": "https://hooks.example.test/incoming"}).send(self.event())
