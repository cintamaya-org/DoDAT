# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
import time
from hashlib import sha256
from threading import Thread, current_thread, main_thread
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.http import Http404
from django.utils import timezone

from .logging.logging_utils import bind_request_context, clear_request_context, get_request_context
from .models import AsyncJob
from .operations.slo_baseline import emit_baseline_metric

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = (
    AsyncJob.Status.QUEUED,
    AsyncJob.Status.RUNNING,
)
LIKEC4_EXPORT_JOB_TYPE = "exports.likec4"
DRAWIO_EXPORT_JOB_TYPE = "exports.drawio"
PDF_EXPORT_JOB_TYPE = "exports.pdf"
ASYNC_JOB_EXECUTE_METRIC = "async_job.execute"


def _configured_backoff_schedule(setting_name: str, fallback: list[float]) -> list[float]:
    configured = getattr(settings, setting_name, None)
    if isinstance(configured, (list, tuple)):
        return _normalize_backoff(configured, fallback)
    return fallback


def _likec4_backoff_schedule() -> list[float]:
    return _configured_backoff_schedule("ASYNC_JOBS_LIKEC4_BACKOFF_SECONDS", [5.0, 20.0])


def _drawio_backoff_schedule() -> list[float]:
    return _configured_backoff_schedule("ASYNC_JOBS_DRAWIO_BACKOFF_SECONDS", [5.0, 20.0])


def _pdf_backoff_schedule() -> list[float]:
    return _configured_backoff_schedule("ASYNC_JOBS_PDF_BACKOFF_SECONDS", [10.0, 30.0, 120.0])


def _job_runner_mode() -> str:
    value = str(getattr(settings, "ASYNC_JOBS_RUNNER_MODE", "thread")).strip().lower()
    if value in {"inline", "thread", "external"}:
        return value
    return "thread"


def _start_job_runner(job_id) -> None:
    mode = _job_runner_mode()
    if mode == "external":
        return
    if mode == "inline":
        dispatch_async_job(job_id)
    else:
        thread = Thread(target=dispatch_async_job, args=(job_id,), daemon=True)
        thread.start()


def enqueue_likec4_export_job(
    storage_path: str,
    *,
    requested_by=None,
    source: str | None = None,
) -> AsyncJob:
    from diagrams.access import get_diagram_for_user
    from diagrams.likec4_scope import refresh_revision
    from diagrams.models import LikeC4Diagram

    if not getattr(requested_by, "is_authenticated", False):
        raise ValueError("LikeC4 export requires an authenticated requester.")
    try:
        diagram = get_diagram_for_user(LikeC4Diagram, requested_by, storage_path=storage_path)
    except (Http404, ValidationError, ValueError, TypeError):
        raise ValueError("LikeC4 export requires an accessible registered diagram.") from None
    revision = refresh_revision(diagram)
    request_context = get_request_context()
    trace_id = str(request_context.get("request_id", "") or "")
    idempotency_key = f"likec4_export:{diagram.pk}:{revision}"
    existing = (
        AsyncJob.objects.filter(idempotency_key=idempotency_key, status__in=_ACTIVE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if existing:
        return existing

    backoff = _likec4_backoff_schedule()
    max_attempts = max(1, len(backoff))
    job = AsyncJob.objects.create(
        job_type=LIKEC4_EXPORT_JOB_TYPE,
        queue_name=LIKEC4_EXPORT_JOB_TYPE,
        status=AsyncJob.Status.QUEUED,
        resource_ref=str(diagram.pk),
        requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
        max_attempts=max_attempts,
        payload={
            "storage_path": storage_path,
            "diagram_id": str(diagram.pk),
            "revision": revision,
            "source": source or "",
            "backoff_seconds": backoff,
            "trace_id": trace_id,
        },
        idempotency_key=idempotency_key,
    )

    _start_job_runner(job.id)
    return job


def enqueue_drawio_export_job(
    diagram_id,
    *,
    xml_payload: str,
    requested_by=None,
    source: str | None = None,
) -> AsyncJob:
    from diagrams.access import get_diagram_for_user
    from diagrams.models import DrawIODiagram

    if not getattr(requested_by, "is_authenticated", False):
        raise ValueError("Draw.io export requires an authenticated requester.")
    try:
        diagram = get_diagram_for_user(DrawIODiagram, requested_by, pk=diagram_id)
    except (Http404, ValidationError, ValueError, TypeError):
        raise ValueError("Draw.io export requires an accessible registered diagram.") from None

    normalized_xml = xml_payload or "<mxGraphModel/>"
    revision = sha256(normalized_xml.encode("utf-8")).hexdigest()
    request_context = get_request_context()
    trace_id = str(request_context.get("request_id", "") or "")
    idempotency_key = f"drawio_export:{diagram.pk}:{revision}"
    existing = (
        AsyncJob.objects.filter(idempotency_key=idempotency_key, status__in=_ACTIVE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if existing:
        return existing
    backoff = _drawio_backoff_schedule()
    job = AsyncJob.objects.create(
        job_type=DRAWIO_EXPORT_JOB_TYPE,
        queue_name=DRAWIO_EXPORT_JOB_TYPE,
        status=AsyncJob.Status.QUEUED,
        resource_ref=str(diagram.pk),
        requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
        max_attempts=max(1, len(backoff)),
        payload={
            "diagram_id": str(diagram.pk),
            "xml_payload": normalized_xml,
            "revision": revision,
            "source": source or "",
            "backoff_seconds": backoff,
            "trace_id": trace_id,
        },
        idempotency_key=idempotency_key,
    )
    _start_job_runner(job.id)
    return job


def enqueue_pdf_export_job(
    dat_id,
    *,
    requested_by=None,
    base_url: str | None = None,
    source: str | None = None,
) -> AsyncJob:
    request_context = get_request_context()
    trace_id = str(request_context.get("request_id", "") or "")
    idempotency_key = f"pdf_export:{dat_id}"
    existing = (
        AsyncJob.objects.filter(idempotency_key=idempotency_key, status__in=_ACTIVE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if existing:
        return existing
    backoff = _pdf_backoff_schedule()
    job = AsyncJob.objects.create(
        job_type=PDF_EXPORT_JOB_TYPE,
        queue_name=PDF_EXPORT_JOB_TYPE,
        status=AsyncJob.Status.QUEUED,
        resource_ref=str(dat_id),
        requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
        max_attempts=max(1, len(backoff)),
        payload={
            "dat_id": int(dat_id),
            "base_url": base_url or "",
            "source": source or "",
            "backoff_seconds": backoff,
            "trace_id": trace_id,
        },
        idempotency_key=idempotency_key,
    )
    _start_job_runner(job.id)
    return job


def _run_likec4_job(job_id) -> None:
    if current_thread() is not main_thread():
        close_old_connections()
    started = time.perf_counter()
    try:
        job = AsyncJob.objects.filter(id=job_id).first()
        if not job:
            return
        if job.status not in _ACTIVE_STATUSES:
            return
        payload = dict(job.payload or {})
        storage_path = str(payload.get("storage_path", "")).strip()
        diagram_id = str(payload.get("diagram_id", "")).strip()
        revision = str(payload.get("revision", "")).strip()
        source = str(payload.get("source", "")).strip() or None
        if not storage_path:
            _set_job_failed(job, "Missing storage_path in payload.", dead_lettered=True)
            return
        from diagrams.likec4_scope import refresh_revision
        from diagrams.models import LikeC4Diagram

        diagram = LikeC4Diagram.objects.filter(storage_path=storage_path).first()
        if diagram is None or (diagram_id and str(diagram.pk) != diagram_id):
            _set_job_failed(job, "Unresolved LikeC4 diagram reference.", dead_lettered=True)
            return
        requester_id = getattr(job, "requested_by_id", None)
        requester = getattr(job, "requested_by", None)
        if requester_id and requester is not None:
            try:
                diagram = get_diagram_for_user(
                    LikeC4Diagram, requester, pk=diagram.pk, storage_path=storage_path,
                )
            except (Http404, ValidationError, ValueError, TypeError):
                _set_job_failed(job, "Requester no longer has diagram access.", dead_lettered=True)
                return
        current_revision = refresh_revision(diagram)
        # Resolve legacy path-only jobs through their exact registered object.
        if not diagram_id or not revision:
            diagram_id, revision = str(diagram.pk), current_revision
            payload.update({"diagram_id": diagram_id, "revision": revision})
            AsyncJob.objects.filter(pk=job.pk).update(payload=payload, resource_ref=diagram_id)
        if revision != current_revision:
            _set_job_failed(job, "Stale LikeC4 render revision.", dead_lettered=True)
            return
        schedule = _normalize_backoff(payload.get("backoff_seconds"), _likec4_backoff_schedule())

        for attempt_index, delay in enumerate(schedule, start=1):
            AsyncJob.objects.filter(id=job.id).update(
                status=AsyncJob.Status.RUNNING,
                started_at=timezone.now(),
                attempt_count=attempt_index,
                last_error="",
            )
            from diagrams.likec4_exports import enqueue_likec4_export

            ok = enqueue_likec4_export(
                storage_path, source=source, diagram_id=diagram_id, revision=revision,
            )
            if ok:
                AsyncJob.objects.filter(id=job.id).update(
                    status=AsyncJob.Status.SUCCEEDED,
                    finished_at=timezone.now(),
                    result_payload={"storage_path": storage_path, "source": source or ""},
                    last_error="",
                )
                emit_baseline_metric(
                    ASYNC_JOB_EXECUTE_METRIC,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=True,
                    dimensions={"job_type": LIKEC4_EXPORT_JOB_TYPE, "status": AsyncJob.Status.SUCCEEDED},
                )
                return
            last_error = "Exporter request failed."
            is_last_attempt = attempt_index >= len(schedule)
            if is_last_attempt:
                _set_job_failed(job, last_error, dead_lettered=True)
                emit_baseline_metric(
                    ASYNC_JOB_EXECUTE_METRIC,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=False,
                    dimensions={"job_type": LIKEC4_EXPORT_JOB_TYPE, "status": AsyncJob.Status.DEAD_LETTERED},
                )
                return
            time.sleep(max(0.0, float(delay)))
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("Async LikeC4 job execution failed (job_id=%s): %s", job_id, exc)
        job = AsyncJob.objects.filter(id=job_id).first()
        if job:
            _set_job_failed(job, f"{type(exc).__name__}: {exc}", dead_lettered=True)
            emit_baseline_metric(
                ASYNC_JOB_EXECUTE_METRIC,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                success=False,
                dimensions={"job_type": LIKEC4_EXPORT_JOB_TYPE, "status": AsyncJob.Status.DEAD_LETTERED},
            )
    finally:
        if current_thread() is not main_thread():
            close_old_connections()


def _run_drawio_job(job_id) -> None:
    if current_thread() is not main_thread():
        close_old_connections()
    started = time.perf_counter()
    try:
        job = AsyncJob.objects.filter(id=job_id).first()
        if not job or job.status not in _ACTIVE_STATUSES:
            return
        payload = dict(job.payload or {})
        diagram_id = payload.get("diagram_id")
        xml_payload = str(payload.get("xml_payload", ""))
        if not diagram_id:
            _set_job_failed(job, "Missing diagram_id in payload.", dead_lettered=True)
            return
        schedule = _normalize_backoff(payload.get("backoff_seconds"), _drawio_backoff_schedule())
        for attempt_index, delay in enumerate(schedule, start=1):
            AsyncJob.objects.filter(id=job.id).update(
                status=AsyncJob.Status.RUNNING,
                started_at=timezone.now(),
                attempt_count=attempt_index,
                last_error="",
            )
            from diagrams.access import get_diagram_for_user
            from diagrams.models import DrawIODiagram
            from diagrams.views import _export_drawio_views

            diagram = DrawIODiagram.objects.filter(pk=diagram_id).first()
            if not diagram:
                _set_job_failed(job, "Diagram not found.", dead_lettered=True)
                return
            requester_id = getattr(job, "requested_by_id", None)
            requester = getattr(job, "requested_by", None)
            if requester_id and requester is not None:
                try:
                    diagram = get_diagram_for_user(
                        DrawIODiagram, requester, pk=diagram.pk,
                    )
                except (Http404, ValidationError, ValueError, TypeError):
                    _set_job_failed(job, "Requester no longer has diagram access.", dead_lettered=True)
                    return
            current_xml = diagram.read_xml() or "<mxGraphModel/>"
            revision = sha256(current_xml.encode("utf-8")).hexdigest()
            expected_revision = str(payload.get("revision", "") or "")
            if expected_revision:
                if revision != expected_revision:
                    _set_job_failed(job, "Stale Draw.io render revision.", dead_lettered=True)
                    return
            elif xml_payload and xml_payload != current_xml:
                # Legacy jobs have no revision hash; only exact current source
                # is safe to render after an upgrade.
                _set_job_failed(job, "Stale legacy Draw.io render job.", dead_lettered=True)
                return
            ok = _export_drawio_views(diagram, current_xml)
            if ok:
                AsyncJob.objects.filter(id=job.id).update(
                    status=AsyncJob.Status.SUCCEEDED,
                    finished_at=timezone.now(),
                    result_payload={
                        "diagram_id": str(diagram_id),
                        "png_paths": getattr(diagram, "png_paths", []) or [],
                    },
                    last_error="",
                )
                emit_baseline_metric(
                    ASYNC_JOB_EXECUTE_METRIC,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=True,
                    dimensions={"job_type": DRAWIO_EXPORT_JOB_TYPE, "status": AsyncJob.Status.SUCCEEDED},
                )
                return
            if attempt_index >= len(schedule):
                _set_job_failed(job, "Draw.io export failed.", dead_lettered=True)
                emit_baseline_metric(
                    ASYNC_JOB_EXECUTE_METRIC,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=False,
                    dimensions={"job_type": DRAWIO_EXPORT_JOB_TYPE, "status": AsyncJob.Status.DEAD_LETTERED},
                )
                return
            time.sleep(max(0.0, float(delay)))
    except Exception as exc:  # pragma: no cover
        logger.exception("Async draw.io job execution failed (job_id=%s): %s", job_id, exc)
        job = AsyncJob.objects.filter(id=job_id).first()
        if job:
            _set_job_failed(job, f"{type(exc).__name__}: {exc}", dead_lettered=True)
    finally:
        if current_thread() is not main_thread():
            close_old_connections()


def _run_pdf_job(job_id) -> None:
    if current_thread() is not main_thread():
        close_old_connections()
    started = time.perf_counter()
    try:
        job = AsyncJob.objects.filter(id=job_id).first()
        if not job or job.status not in _ACTIVE_STATUSES:
            return
        payload = dict(job.payload or {})
        dat_id = payload.get("dat_id")
        base_url = str(payload.get("base_url", "") or "") or None
        if not dat_id:
            _set_job_failed(job, "Missing dat_id in payload.", dead_lettered=True)
            return
        schedule = _normalize_backoff(payload.get("backoff_seconds"), _pdf_backoff_schedule())
        for attempt_index, delay in enumerate(schedule, start=1):
            AsyncJob.objects.filter(id=job.id).update(
                status=AsyncJob.Status.RUNNING,
                started_at=timezone.now(),
                attempt_count=attempt_index,
                last_error="",
            )
            from dat.tasks import _run_pdf_generation

            success = bool(_run_pdf_generation(int(dat_id), base_url=base_url))
            if success:
                AsyncJob.objects.filter(id=job.id).update(
                    status=AsyncJob.Status.SUCCEEDED,
                    finished_at=timezone.now(),
                    result_payload={"dat_id": int(dat_id)},
                    last_error="",
                )
                emit_baseline_metric(
                    ASYNC_JOB_EXECUTE_METRIC,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=True,
                    dimensions={"job_type": PDF_EXPORT_JOB_TYPE, "status": AsyncJob.Status.SUCCEEDED},
                )
                return
            if attempt_index >= len(schedule):
                _set_job_failed(job, "PDF export failed.", dead_lettered=True)
                emit_baseline_metric(
                    ASYNC_JOB_EXECUTE_METRIC,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=False,
                    dimensions={"job_type": PDF_EXPORT_JOB_TYPE, "status": AsyncJob.Status.DEAD_LETTERED},
                )
                return
            time.sleep(max(0.0, float(delay)))
    except Exception as exc:  # pragma: no cover
        logger.exception("Async PDF job execution failed (job_id=%s): %s", job_id, exc)
        job = AsyncJob.objects.filter(id=job_id).first()
        if job:
            _set_job_failed(job, f"{type(exc).__name__}: {exc}", dead_lettered=True)
    finally:
        if current_thread() is not main_thread():
            close_old_connections()


def _normalize_backoff(value: Any, fallback: list[float]) -> list[float]:
    if isinstance(value, (list, tuple)):
        out: list[float] = []
        for item in value:
            try:
                out.append(float(item))
            except (TypeError, ValueError):
                continue
        if out:
            return out
    return fallback


def dispatch_async_job(job_id) -> None:
    job = AsyncJob.objects.filter(id=job_id).only("job_type", "payload").first()
    if not job:
        return
    trace_id = str((job.payload or {}).get("trace_id", "") or "")
    bind_request_context(request_id=trace_id or f"job-{job_id}", job_id=str(job_id))
    try:
        if job.job_type == LIKEC4_EXPORT_JOB_TYPE:
            _run_likec4_job(job_id)
            return
        if job.job_type == DRAWIO_EXPORT_JOB_TYPE:
            _run_drawio_job(job_id)
            return
        if job.job_type == PDF_EXPORT_JOB_TYPE:
            _run_pdf_job(job_id)
            return
        logger.warning("Async job %s has unsupported job_type=%s", job_id, job.job_type)
        AsyncJob.objects.filter(id=job_id).update(
            status=AsyncJob.Status.DEAD_LETTERED,
            finished_at=timezone.now(),
            last_error=f"Unsupported job_type: {job.job_type}",
        )
    finally:
        clear_request_context()


def _set_job_failed(job: AsyncJob, message: str, *, dead_lettered: bool) -> None:
    status = AsyncJob.Status.DEAD_LETTERED if dead_lettered else AsyncJob.Status.FAILED
    AsyncJob.objects.filter(id=job.id).update(
        status=status,
        finished_at=timezone.now(),
        last_error=(message or "")[:4000],
    )


def serialize_async_job(job: AsyncJob) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "resource_ref": job.resource_ref,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "last_error": job.last_error or "",
        "result_payload": job.result_payload or {},
    }
