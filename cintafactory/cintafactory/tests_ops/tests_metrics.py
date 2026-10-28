# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from unittest import mock

from django.test import SimpleTestCase

from ..operations import metrics


class PrometheusMetricsTests(SimpleTestCase):
    def test_labels_are_sorted_and_empty_labels_are_omitted(self):
        self.assertEqual(metrics._format_labels({}), "")
        self.assertEqual(
            metrics._render_counter("requests", 2, {"route": "/health", "method": "GET"}),
            'requests{method="GET",route="/health"} 2',
        )

    def test_render_includes_allowed_counters_job_states_and_readiness(self):
        manager = mock.MagicMock()
        manager.values.return_value.annotate.return_value.order_by.return_value = [
            {"status": "queued", "total": 2},
            {"status": "running", "total": 1},
        ]
        manager.filter.return_value.count.side_effect = [2, 1]

        counters = [
            ("cinta_baseline_events_total", {}, 3.0),
            ("cinta_web_requests_total", {"method": "GET"}, 4.0),
            ("unapproved_metric", {}, 99.0),
        ]
        with (
            mock.patch.object(metrics.AsyncJob, "objects", manager),
            mock.patch.object(metrics, "iter_counters", return_value=counters),
            mock.patch.object(
                metrics,
                "collect_readiness",
                return_value={"seaweedfs": False, "postgres": True},
            ),
        ):
            rendered = metrics.render_prometheus_metrics()

        self.assertIn("cinta_baseline_events_total 3.0", rendered)
        self.assertIn('cinta_web_requests_total{method="GET"} 4.0', rendered)
        self.assertNotIn("unapproved_metric", rendered)
        self.assertIn('cinta_async_jobs_status{status="queued"} 2.0', rendered)
        self.assertIn('cinta_async_jobs_status{status="running"} 1.0', rendered)
        self.assertIn("cinta_async_queue_backlog 2.0", rendered)
        self.assertIn("cinta_async_workers_running 1.0", rendered)
        self.assertIn('cinta_dependency_up{dependency="postgres"} 1.0', rendered)
        self.assertIn('cinta_dependency_up{dependency="seaweedfs"} 0.0', rendered)
        self.assertTrue(rendered.endswith("\n"))
