# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from cintafactory.operations import health


class HealthViewsTests(SimpleTestCase):
    def test_health_live_returns_alive(self):
        response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "alive")

    @mock.patch("cintafactory.operations.views_health.overall_ready", return_value=(True, {"database": True}))
    def test_health_ready_returns_200_when_ready(self, _overall_ready):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ready")

    @mock.patch("cintafactory.operations.views_health.overall_ready", return_value=(False, {"database": False}))
    def test_health_ready_returns_503_when_not_ready(self, _overall_ready):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "not_ready")

    @mock.patch("cintafactory.operations.views_health.render_prometheus_metrics", return_value="cinta_web_requests_total 1\n")
    def test_metrics_endpoint_returns_prometheus_text(self, _render_metrics):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
        self.assertIn("cinta_web_requests_total", response.content.decode("utf-8"))


class CheckRuntimeDependenciesCommandTests(SimpleTestCase):
    @mock.patch("cintafactory.management.commands.check_runtime_dependencies.overall_ready")
    def test_command_passes_when_ready(self, overall_ready):
        overall_ready.return_value = (True, {"database": True, "queue": True})
        out = StringIO()
        call_command("check_runtime_dependencies", "--profile", "web", "--json-output", stdout=out)
        payload = json.loads(out.getvalue().strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["profile"], "web")

    @mock.patch("cintafactory.management.commands.check_runtime_dependencies.overall_ready")
    def test_command_fails_when_not_ready(self, overall_ready):
        overall_ready.return_value = (False, {"database": False, "queue": True})
        with self.assertRaises(CommandError):
            call_command("check_runtime_dependencies", "--profile", "worker")

    @mock.patch("cintafactory.management.commands.export_runtime_metrics.render_prometheus_metrics")
    def test_export_runtime_metrics_command_outputs_metrics(self, render_metrics):
        render_metrics.return_value = "cinta_dependency_up{dependency=\"database\"} 1\n"
        out = StringIO()
        call_command("export_runtime_metrics", stdout=out)
        self.assertIn("cinta_dependency_up", out.getvalue())


class RuntimeReadinessProbeTests(SimpleTestCase):
    def test_http_probe_handles_empty_success_client_error_server_error_and_exception(self):
        self.assertFalse(health._http_reachable("  "))

        response = mock.MagicMock(status=404)
        with mock.patch.object(health, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            self.assertTrue(health._http_reachable("http://probe.invalid"))
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_method(), "HEAD")

        with mock.patch.object(
            health,
            "urlopen",
            side_effect=health.HTTPError("http://probe.invalid", 429, "limited", {}, None),
        ):
            self.assertTrue(health._http_reachable("http://probe.invalid"))

        with mock.patch.object(
            health,
            "urlopen",
            side_effect=health.HTTPError("http://probe.invalid", 503, "unavailable", {}, None),
        ):
            self.assertFalse(health._http_reachable("http://probe.invalid"))

        with mock.patch.object(health, "urlopen", side_effect=OSError("offline")):
            self.assertFalse(health._http_reachable("http://probe.invalid"))

    def test_database_and_queue_probes_return_false_on_failures(self):
        connection = mock.MagicMock()
        with mock.patch.object(health, "connections", {"default": connection}):
            self.assertTrue(health._db_ready())
            connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with("select 1")

        connection.ensure_connection.side_effect = RuntimeError("database down")
        with mock.patch.object(health, "connections", {"default": connection}):
            self.assertFalse(health._db_ready())

        with mock.patch.object(health.AsyncJob.objects, "order_by", side_effect=RuntimeError("query failed")):
            self.assertFalse(health._queue_ready())

    @override_settings(CLAMAV_HOST="scanner.invalid", CLAMAV_PORT=3311)
    def test_clamav_probe_checks_pong_and_handles_bad_response_or_socket_error(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value.recv.return_value = b"PONG\n"
        with mock.patch.object(health.socket, "create_connection", return_value=sock) as connect:
            self.assertTrue(health._clamav_ready(timeout=4))
        connect.assert_called_once_with(("scanner.invalid", 3311), timeout=4)
        sock.__enter__.return_value.sendall.assert_called_once_with(b"PING\n")

        sock.__enter__.return_value.recv.return_value = b"NOPE\n"
        with mock.patch.object(health.socket, "create_connection", return_value=sock):
            self.assertFalse(health._clamav_ready())
        with mock.patch.object(health.socket, "create_connection", side_effect=OSError("offline")):
            self.assertFalse(health._clamav_ready())

    @override_settings(
        SEAWEEDFS_FILER_URL="http://filer.invalid/",
        DRAWIO_EXPORT_URL="not a URL",
        DRAWIO_BASE_URL="http://drawio.invalid/",
        LIKEC4_EXPORT_ENABLED=True,
        LIKEC4_EXPORT_URL="http://likec4.invalid/export",
    )
    def test_exporter_probes_use_valid_urls_and_fallbacks(self):
        with mock.patch.object(health, "_http_reachable", return_value=True) as probe:
            self.assertTrue(health._seaweedfs_ready())
            self.assertTrue(health._drawio_exporter_ready())
            self.assertTrue(health._likec4_exporter_ready())
        self.assertEqual(
            [call.args[0] for call in probe.call_args_list],
            ["http://filer.invalid/", "http://drawio.invalid/", "http://likec4.invalid/export"],
        )

    @override_settings(SEAWEEDFS_FILER_URL="", DRAWIO_EXPORT_URL="ftp://drawio.invalid/export", DRAWIO_BASE_URL="")
    def test_exporter_probes_report_missing_dependencies_and_disabled_likec4_as_ready(self):
        self.assertFalse(health._seaweedfs_ready())
        self.assertFalse(health._drawio_exporter_ready())
        with override_settings(LIKEC4_EXPORT_ENABLED=False, LIKEC4_EXPORT_URL=""):
            self.assertTrue(health._likec4_exporter_ready())
        with override_settings(LIKEC4_EXPORT_ENABLED=True, LIKEC4_EXPORT_URL=""):
            self.assertFalse(health._likec4_exporter_ready())

    def test_readiness_profile_selects_checks_and_overall_requires_all_ready(self):
        with (
            mock.patch.object(health, "_db_ready", return_value=True),
            mock.patch.object(health, "_queue_ready", return_value=True),
            mock.patch.object(health, "_seaweedfs_ready", return_value=False) as seaweedfs,
            mock.patch.object(health, "_clamav_ready", return_value=True),
            mock.patch.object(health, "_drawio_exporter_ready", return_value=True),
            mock.patch.object(health, "_likec4_exporter_ready", return_value=True),
        ):
            self.assertEqual(health.collect_readiness("worker"), {
                "database": True,
                "queue": True,
                "seaweedfs": False,
                "clamav": True,
                "drawio_exporter": True,
                "likec4_exporter": True,
            })
            self.assertEqual(health.collect_readiness("scheduler"), {"database": True, "queue": True})
            self.assertFalse(health.overall_ready("web")[0])
        seaweedfs.assert_called()
