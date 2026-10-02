# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from unittest import mock
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .. import async_jobs
from ..async_jobs import enqueue_likec4_export_job
from ..logging.logging_utils import bind_request_context, clear_request_context
from ..models import AsyncJob

User = get_user_model()


class AsyncJobServiceTests(TestCase):
    def tearDown(self) -> None:
        clear_request_context()
        super().tearDown()

    @override_settings(ASYNC_JOBS_RUNNER_MODE="inline", ASYNC_JOBS_LIKEC4_BACKOFF_SECONDS=[0.0])
    def test_enqueue_uses_existing_active_job_for_same_idempotency_key(self):
        user = User.objects.create_user(username="job-owner", password="pwd")
        existing = AsyncJob.objects.create(
            job_type="exports.likec4",
            queue_name="exports.likec4",
            status=AsyncJob.Status.QUEUED,
            resource_ref="diagrams/abc/likec4.c4",
            requested_by=user,
            max_attempts=2,
            idempotency_key="likec4_export:diagrams/abc/likec4.c4",
            payload={"storage_path": "diagrams/abc/likec4.c4", "source": "test", "backoff_seconds": [0.0]},
        )

        job = enqueue_likec4_export_job("diagrams/abc/likec4.c4", requested_by=user, source="test")

        self.assertEqual(job.id, existing.id)
        self.assertEqual(AsyncJob.objects.filter(idempotency_key=existing.idempotency_key).count(), 1)

    @mock.patch("cintafactory.async_jobs.dispatch_async_job")
    @override_settings(ASYNC_JOBS_RUNNER_MODE="external")
    def test_enqueue_does_not_start_inline_or_thread_runner_in_external_mode(self, dispatch_job):
        user = User.objects.create_user(username="job-owner-2", password="pwd")

        job = enqueue_likec4_export_job("diagrams/def/likec4.c4", requested_by=user, source="test")

        self.assertEqual(job.status, AsyncJob.Status.QUEUED)
        dispatch_job.assert_not_called()

    @override_settings(ASYNC_JOBS_RUNNER_MODE="external")
    def test_enqueue_persists_request_trace_id_in_payload(self):
        user = User.objects.create_user(username="job-owner-trace", password="pwd")
        bind_request_context(request_id="req-trace-123")
        job = enqueue_likec4_export_job("diagrams/trace/likec4.c4", requested_by=user, source="test")
        self.assertEqual(job.payload.get("trace_id"), "req-trace-123")


class AsyncJobApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="jobs-user", password="pwd")
        self.other = User.objects.create_user(username="jobs-other", password="pwd")
        self.staff = User.objects.create_user(username="jobs-staff", password="pwd", is_staff=True)

        self.user_job = AsyncJob.objects.create(
            job_type="exports.likec4",
            queue_name="exports.likec4",
            status=AsyncJob.Status.QUEUED,
            resource_ref="diagrams/user/likec4.c4",
            requested_by=self.user,
            max_attempts=2,
            idempotency_key="likec4_export:diagrams/user/likec4.c4",
            payload={"storage_path": "diagrams/user/likec4.c4"},
        )
        self.other_job = AsyncJob.objects.create(
            job_type="exports.likec4",
            queue_name="exports.likec4",
            status=AsyncJob.Status.QUEUED,
            resource_ref="diagrams/other/likec4.c4",
            requested_by=self.other,
            max_attempts=2,
            idempotency_key="likec4_export:diagrams/other/likec4.c4",
            payload={"storage_path": "diagrams/other/likec4.c4"},
        )

    def test_jobs_list_requires_authentication(self):
        response = self.client.get(reverse("api:async-job-list"))
        self.assertIn(response.status_code, {401, 403})

    def test_jobs_list_returns_only_requester_jobs(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("api:async-job-list"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["job_id"], str(self.user_job.id))

    def test_jobs_list_allows_staff_to_see_all_jobs(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("api:async-job-list"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)

    def test_jobs_list_supports_resource_ref_filter(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("api:async-job-list"), {"resource_ref": "diagrams/user/likec4.c4"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["resource_ref"], "diagrams/user/likec4.c4")

    def test_job_detail_hides_other_users_job(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("api:async-job-detail", args=[self.user_job.id]))
        self.assertEqual(response.status_code, 200)
        response_other = self.client.get(
            reverse("api:async-job-detail", args=[self.other_job.id])
        )
        self.assertEqual(response_other.status_code, 404)

    def test_cancel_requires_staff(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("api:async-job-cancel", args=[self.user_job.id]))
        self.assertEqual(response.status_code, 403)

    def test_cancel_updates_status(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("api:async-job-cancel", args=[self.user_job.id]))
        self.assertEqual(response.status_code, 200)
        self.user_job.refresh_from_db()
        self.assertEqual(self.user_job.status, AsyncJob.Status.CANCELLED)

    @mock.patch("cintafactory.api.jobs.dispatch_async_job")
    def test_requeue_resets_job_and_dispatches(self, dispatch_job):
        self.user_job.status = AsyncJob.Status.DEAD_LETTERED
        self.user_job.attempt_count = 2
        self.user_job.last_error = "boom"
        self.user_job.save(update_fields=["status", "attempt_count", "last_error", "updated_at"])
        self.client.force_login(self.staff)
        response = self.client.post(reverse("api:async-job-requeue", args=[self.user_job.id]))
        self.assertEqual(response.status_code, 200)
        self.user_job.refresh_from_db()
        self.assertEqual(self.user_job.status, AsyncJob.Status.QUEUED)
        self.assertEqual(self.user_job.attempt_count, 0)
        self.assertEqual(self.user_job.last_error, "")
        dispatch_job.assert_called_once_with(self.user_job.id)

    def test_ignore_sets_cancelled_with_reason(self):
        self.user_job.status = AsyncJob.Status.DEAD_LETTERED
        self.user_job.save(update_fields=["status", "updated_at"])
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("api:async-job-ignore", args=[self.user_job.id]),
            data={"reason": "known issue"},
        )
        self.assertEqual(response.status_code, 200)
        self.user_job.refresh_from_db()
        self.assertEqual(self.user_job.status, AsyncJob.Status.CANCELLED)
        self.assertIn("known issue", self.user_job.last_error)


class AsyncJobRunnerTests(SimpleTestCase):
    def tearDown(self):
        clear_request_context()
        super().tearDown()

    def _mock_job_lookup(self, job):
        patcher = mock.patch.object(async_jobs.AsyncJob.objects, "filter")
        query = patcher.start()
        self.addCleanup(patcher.stop)
        query.return_value.first.return_value = job
        query.return_value.only.return_value.first.return_value = job
        return query

    def test_backoff_configuration_and_runner_modes_are_normalized(self):
        with override_settings(ASYNC_JOBS_LIKEC4_BACKOFF_SECONDS=["2", "invalid", None]):
            self.assertEqual(async_jobs._likec4_backoff_schedule(), [2.0])
        with override_settings(ASYNC_JOBS_LIKEC4_BACKOFF_SECONDS=[]):
            self.assertEqual(async_jobs._likec4_backoff_schedule(), [5.0, 20.0])
        with override_settings(ASYNC_JOBS_RUNNER_MODE="invalid"):
            self.assertEqual(async_jobs._job_runner_mode(), "thread")
        with mock.patch.object(async_jobs, "dispatch_async_job") as dispatch:
            with override_settings(ASYNC_JOBS_RUNNER_MODE="external"):
                async_jobs._start_job_runner("job-external")
            dispatch.assert_not_called()

            with override_settings(ASYNC_JOBS_RUNNER_MODE="inline"):
                async_jobs._start_job_runner("job-inline")
            dispatch.assert_called_once_with("job-inline")

        with mock.patch.object(async_jobs, "Thread") as thread:
            with override_settings(ASYNC_JOBS_RUNNER_MODE="thread"):
                async_jobs._start_job_runner("job-thread")
        thread.assert_called_once_with(
            target=async_jobs.dispatch_async_job,
            args=("job-thread",),
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()

    def test_drawio_and_pdf_enqueues_store_normalized_payloads_without_dispatching(self):
        query_patcher = mock.patch.object(async_jobs.AsyncJob.objects, "filter")
        create_patcher = mock.patch.object(async_jobs.AsyncJob.objects, "create")
        query = query_patcher.start()
        create = create_patcher.start()
        self.addCleanup(query_patcher.stop)
        self.addCleanup(create_patcher.stop)
        query.return_value.order_by.return_value.first.return_value = None
        drawio_job = SimpleNamespace(id="drawio-job")
        pdf_job = SimpleNamespace(id="pdf-job")
        create.side_effect = [drawio_job, pdf_job]
        anonymous = SimpleNamespace(is_authenticated=False)
        authenticated = SimpleNamespace(is_authenticated=True)

        with (
            mock.patch.object(async_jobs, "_start_job_runner") as start,
            mock.patch.object(async_jobs, "get_request_context", return_value={"request_id": "trace-42"}),
            override_settings(
                ASYNC_JOBS_RUNNER_MODE="external",
                ASYNC_JOBS_DRAWIO_BACKOFF_SECONDS=[0],
                ASYNC_JOBS_PDF_BACKOFF_SECONDS=["1", "bad", 3],
            ),
        ):
            returned_drawio = async_jobs.enqueue_drawio_export_job(
                "diagram-42",
                xml_payload="<mxGraphModel />",
                requested_by=anonymous,
            )
            returned_pdf = async_jobs.enqueue_pdf_export_job(
                "123",
                requested_by=authenticated,
                source="admin",
            )

        self.assertIs(returned_drawio, drawio_job)
        self.assertIs(returned_pdf, pdf_job)
        drawio_payload = create.call_args_list[0].kwargs
        self.assertIsNone(drawio_payload["requested_by"])
        self.assertEqual(drawio_payload["max_attempts"], 1)
        self.assertEqual(
            drawio_payload["payload"],
            {
                "diagram_id": "diagram-42",
                "xml_payload": "<mxGraphModel />",
                "source": "",
                "backoff_seconds": [0.0],
                "trace_id": "trace-42",
            },
        )
        pdf_payload = create.call_args_list[1].kwargs
        self.assertIs(pdf_payload["requested_by"], authenticated)
        self.assertEqual(pdf_payload["max_attempts"], 2)
        self.assertEqual(pdf_payload["payload"]["dat_id"], 123)
        self.assertEqual(pdf_payload["payload"]["base_url"], "")
        self.assertEqual(pdf_payload["payload"]["source"], "admin")
        self.assertEqual(pdf_payload["payload"]["trace_id"], "trace-42")
        start.assert_has_calls([mock.call("drawio-job"), mock.call("pdf-job")])

    def test_likec4_runner_retries_then_succeeds_and_records_metrics(self):
        job = SimpleNamespace(
            id="likec4-job",
            status=AsyncJob.Status.QUEUED,
            payload={
                "storage_path": "data/context.c4",
                "source": "admin",
                "backoff_seconds": [0.25, 0],
            },
        )
        query = self._mock_job_lookup(job)
        with (
            mock.patch("diagrams.likec4_exports.enqueue_likec4_export", side_effect=[False, True]) as export,
            mock.patch.object(async_jobs.time, "sleep") as sleep,
            mock.patch.object(async_jobs, "emit_baseline_metric") as metric,
        ):
            async_jobs._run_likec4_job(job.id)

        self.assertEqual(export.call_args_list, [
            mock.call("data/context.c4", source="admin"),
            mock.call("data/context.c4", source="admin"),
        ])
        sleep.assert_called_once_with(0.25)
        update_calls = query.return_value.update.call_args_list
        self.assertEqual(len(update_calls), 3)
        self.assertEqual(update_calls[-1].kwargs["status"], AsyncJob.Status.SUCCEEDED)
        self.assertEqual(update_calls[-1].kwargs["result_payload"], {
            "storage_path": "data/context.c4",
            "source": "admin",
        })
        metric.assert_called_once()
        self.assertTrue(metric.call_args.kwargs["success"])

    def test_export_runners_fail_missing_resources_and_after_final_retry(self):
        missing_path = SimpleNamespace(
            id="missing-path",
            status=AsyncJob.Status.QUEUED,
            payload={"backoff_seconds": [0]},
        )
        query = self._mock_job_lookup(missing_path)
        with mock.patch("diagrams.likec4_exports.enqueue_likec4_export") as export:
            async_jobs._run_likec4_job(missing_path.id)
        export.assert_not_called()
        self.assertEqual(
            query.return_value.update.call_args.kwargs["status"],
            AsyncJob.Status.DEAD_LETTERED,
        )

        diagram_job = SimpleNamespace(
            id="missing-diagram",
            status=AsyncJob.Status.QUEUED,
            payload={"diagram_id": "gone", "backoff_seconds": [0]},
        )
        query = self._mock_job_lookup(diagram_job)
        with mock.patch("diagrams.models.DrawIODiagram.objects.filter") as find_diagram:
            find_diagram.return_value.first.return_value = None
            async_jobs._run_drawio_job(diagram_job.id)
        self.assertEqual(
            query.return_value.update.call_args.kwargs["last_error"],
            "Diagram not found.",
        )

        pdf_job = SimpleNamespace(
            id="failed-pdf",
            status=AsyncJob.Status.QUEUED,
            payload={"dat_id": 123, "backoff_seconds": [0, 0]},
        )
        query = self._mock_job_lookup(pdf_job)
        with (
            mock.patch("dat.tasks._run_pdf_generation", return_value=False) as generate,
            mock.patch.object(async_jobs.time, "sleep") as sleep,
            mock.patch.object(async_jobs, "emit_baseline_metric") as metric,
        ):
            async_jobs._run_pdf_job(pdf_job.id)
        self.assertEqual(generate.call_count, 2)
        sleep.assert_called_once_with(0.0)
        self.assertEqual(query.return_value.update.call_args.kwargs["status"], AsyncJob.Status.DEAD_LETTERED)
        metric.assert_called_once()
        self.assertFalse(metric.call_args.kwargs["success"])

    def test_runners_skip_missing_or_inactive_jobs_and_close_worker_connections(self):
        query = self._mock_job_lookup(None)
        with mock.patch("diagrams.likec4_exports.enqueue_likec4_export") as export:
            async_jobs._run_likec4_job("missing")
        export.assert_not_called()
        query.return_value.first.assert_called_once()

        inactive = SimpleNamespace(id="inactive", status=AsyncJob.Status.SUCCEEDED, payload={})
        self._mock_job_lookup(inactive)
        with mock.patch("diagrams.likec4_exports.enqueue_likec4_export") as export:
            async_jobs._run_likec4_job(inactive.id)
        export.assert_not_called()

        worker = object()
        main = object()
        self._mock_job_lookup(None)
        with (
            mock.patch.object(async_jobs, "current_thread", return_value=worker),
            mock.patch.object(async_jobs, "main_thread", return_value=main),
            mock.patch.object(async_jobs, "close_old_connections") as close_connections,
        ):
            async_jobs._run_likec4_job("worker-missing")
        self.assertEqual(close_connections.call_count, 2)

    def test_drawio_and_pdf_runners_record_success_results(self):
        drawio_job = SimpleNamespace(
            id="drawio-job",
            status=AsyncJob.Status.QUEUED,
            payload={"diagram_id": "diagram-1", "xml_payload": "", "backoff_seconds": [0]},
        )
        query = self._mock_job_lookup(drawio_job)
        diagram = SimpleNamespace(png_paths=["diagrams/diagram-1/views/main.png"], read_xml=mock.Mock(return_value=""))
        with (
            mock.patch("diagrams.models.DrawIODiagram.objects.filter") as find_diagram,
            mock.patch("diagrams.views._export_drawio_views", return_value=True) as export,
            mock.patch.object(async_jobs, "emit_baseline_metric"),
        ):
            find_diagram.return_value.first.return_value = diagram
            async_jobs._run_drawio_job(drawio_job.id)
        export.assert_called_once_with(diagram, "<mxGraphModel/>")
        self.assertEqual(query.return_value.update.call_args.kwargs["status"], AsyncJob.Status.SUCCEEDED)
        self.assertEqual(query.return_value.update.call_args.kwargs["result_payload"]["png_paths"], diagram.png_paths)

        pdf_job = SimpleNamespace(
            id="pdf-job",
            status=AsyncJob.Status.QUEUED,
            payload={"dat_id": "456", "base_url": "https://app.example.test", "backoff_seconds": [0]},
        )
        query = self._mock_job_lookup(pdf_job)
        with (
            mock.patch("dat.tasks._run_pdf_generation", return_value=True) as generate,
            mock.patch.object(async_jobs, "emit_baseline_metric"),
        ):
            async_jobs._run_pdf_job(pdf_job.id)
        generate.assert_called_once_with(456, base_url="https://app.example.test")
        self.assertEqual(query.return_value.update.call_args.kwargs["result_payload"], {"dat_id": 456})

    def test_dispatch_routes_jobs_marks_unknown_types_and_always_clears_context(self):
        job = SimpleNamespace(job_type=async_jobs.LIKEC4_EXPORT_JOB_TYPE, payload={"trace_id": "trace-9"})
        query = self._mock_job_lookup(job)
        with (
            mock.patch.object(async_jobs, "bind_request_context") as bind,
            mock.patch.object(async_jobs, "clear_request_context") as clear,
            mock.patch.object(async_jobs, "_run_likec4_job") as run_likec4,
        ):
            async_jobs.dispatch_async_job("job-9")
        bind.assert_called_once_with(request_id="trace-9", job_id="job-9")
        run_likec4.assert_called_once_with("job-9")
        clear.assert_called_once_with()

        unsupported = SimpleNamespace(job_type="unknown", payload={})
        query = self._mock_job_lookup(unsupported)
        with (
            mock.patch.object(async_jobs, "clear_request_context") as clear,
            mock.patch.object(async_jobs.logger, "warning") as warning,
        ):
            async_jobs.dispatch_async_job("job-unsupported")
        self.assertEqual(
            query.return_value.update.call_args.kwargs["last_error"],
            "Unsupported job_type: unknown",
        )
        clear.assert_called_once_with()
        warning.assert_called_once()

        for job_type, runner_name in (
            (async_jobs.DRAWIO_EXPORT_JOB_TYPE, "_run_drawio_job"),
            (async_jobs.PDF_EXPORT_JOB_TYPE, "_run_pdf_job"),
        ):
            with self.subTest(job_type=job_type):
                self._mock_job_lookup(SimpleNamespace(job_type=job_type, payload={}))
                with mock.patch.object(async_jobs, runner_name) as runner:
                    async_jobs.dispatch_async_job("routed-job")
                runner.assert_called_once_with("routed-job")

        self._mock_job_lookup(None)
        with (
            mock.patch.object(async_jobs, "bind_request_context") as bind,
            mock.patch.object(async_jobs, "clear_request_context") as clear,
        ):
            async_jobs.dispatch_async_job("missing")
        bind.assert_not_called()
        clear.assert_not_called()

    def test_runner_exceptions_dead_letter_and_serialization_uses_safe_defaults(self):
        job = SimpleNamespace(
            id="errored-job",
            status=AsyncJob.Status.QUEUED,
            payload={"storage_path": "data/a.c4", "backoff_seconds": [0]},
        )
        query = self._mock_job_lookup(job)
        with (
            mock.patch("diagrams.likec4_exports.enqueue_likec4_export", side_effect=RuntimeError("export failed")),
            mock.patch.object(async_jobs, "emit_baseline_metric"),
            mock.patch.object(async_jobs.logger, "exception"),
        ):
            async_jobs._run_likec4_job(job.id)
        self.assertEqual(query.return_value.update.call_args.kwargs["status"], AsyncJob.Status.DEAD_LETTERED)
        self.assertEqual(query.return_value.update.call_args.kwargs["last_error"], "RuntimeError: export failed")

        serialized = async_jobs.serialize_async_job(
            SimpleNamespace(
                id="serialized",
                job_type="exports.pdf",
                status=AsyncJob.Status.FAILED,
                resource_ref="123",
                attempt_count=2,
                max_attempts=3,
                created_at=timezone.now(),
                started_at=None,
                finished_at=None,
                last_error=None,
                result_payload=None,
            )
        )
        self.assertEqual(serialized["job_id"], "serialized")
        self.assertIsNone(serialized["started_at"])
        self.assertIsNone(serialized["finished_at"])
        self.assertEqual(serialized["last_error"], "")
        self.assertEqual(serialized["result_payload"], {})

    def test_drawio_and_pdf_runner_exceptions_are_dead_lettered(self):
        diagram_job = SimpleNamespace(
            id="broken-diagram-job",
            status=AsyncJob.Status.QUEUED,
            payload={"diagram_id": "diagram-2", "backoff_seconds": [0]},
        )
        query = self._mock_job_lookup(diagram_job)
        diagram = SimpleNamespace(read_xml=mock.Mock(return_value="<mxGraphModel />"))
        with (
            mock.patch("diagrams.models.DrawIODiagram.objects.filter") as find_diagram,
            mock.patch("diagrams.views._export_drawio_views", side_effect=RuntimeError("render failed")),
            mock.patch.object(async_jobs.logger, "exception"),
        ):
            find_diagram.return_value.first.return_value = diagram
            async_jobs._run_drawio_job(diagram_job.id)
        self.assertEqual(query.return_value.update.call_args.kwargs["status"], AsyncJob.Status.DEAD_LETTERED)
        self.assertEqual(query.return_value.update.call_args.kwargs["last_error"], "RuntimeError: render failed")

        pdf_job = SimpleNamespace(
            id="broken-pdf-job",
            status=AsyncJob.Status.QUEUED,
            payload={"dat_id": 44, "backoff_seconds": [0]},
        )
        query = self._mock_job_lookup(pdf_job)
        with (
            mock.patch("dat.tasks._run_pdf_generation", side_effect=RuntimeError("render failed")),
            mock.patch.object(async_jobs.logger, "exception"),
        ):
            async_jobs._run_pdf_job(pdf_job.id)
        self.assertEqual(query.return_value.update.call_args.kwargs["status"], AsyncJob.Status.DEAD_LETTERED)
        self.assertEqual(query.return_value.update.call_args.kwargs["last_error"], "RuntimeError: render failed")

    def test_failure_helper_selects_failed_status_and_truncates_error(self):
        query = self._mock_job_lookup(None)
        message = "x" * 4500
        async_jobs._set_job_failed(SimpleNamespace(id="retryable-job"), message, dead_lettered=False)
        self.assertEqual(query.return_value.update.call_args.kwargs["status"], AsyncJob.Status.FAILED)
        self.assertEqual(len(query.return_value.update.call_args.kwargs["last_error"]), 4000)
