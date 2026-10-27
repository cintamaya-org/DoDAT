# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import json
import logging
from time import perf_counter
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from cintafactory.operations.slo_baseline import emit_baseline_metric
from cintafactory.url_safety import is_http_url

logger = logging.getLogger(__name__)


def enqueue_likec4_export(
    storage_path: str,
    *,
    source: str | None = None,
    diagram_id: str | None = None,
    revision: str | None = None,
    diagram=None,
) -> bool:
    from django.core.exceptions import ValidationError

    from .likec4_scope import refresh_revision, scope_headers
    from .models import LikeC4Diagram

    started_at = perf_counter()

    def _emit(success: bool, outcome: str) -> None:
        emit_baseline_metric(
            "export.likec4.enqueue",
            duration_ms=(perf_counter() - started_at) * 1000.0,
            success=success,
            dimensions={"outcome": outcome, "source": source or ""},
        )

    if not isinstance(storage_path, str) or not storage_path:
        _emit(False, "invalid_storage_path")
        return False
    if not getattr(settings, "LIKEC4_EXPORT_ENABLED", False):
        _emit(False, "disabled")
        return False
    export_url = getattr(settings, "LIKEC4_EXPORT_URL", "").strip()
    if not export_url:
        logger.warning("LikeC4 export skipped: LIKEC4_EXPORT_URL is not configured.")
        _emit(False, "missing_url")
        return False
    if not is_http_url(export_url):
        logger.warning("LikeC4 export skipped: LIKEC4_EXPORT_URL must be http(s).")
        _emit(False, "invalid_url")
        return False

    if diagram is None:
        query = LikeC4Diagram.objects.filter(storage_path=storage_path)
        if diagram_id:
            query = query.filter(pk=diagram_id)
        diagram = query.first()
    if diagram is None or diagram.storage_path != storage_path:
        _emit(False, "unregistered_diagram")
        return False
    try:
        current_revision = refresh_revision(diagram)
    except (OSError, ValidationError, ValueError):
        _emit(False, "source_unavailable")
        return False
    if diagram_id and str(diagram.pk) != str(diagram_id):
        _emit(False, "diagram_mismatch")
        return False
    if revision and revision != current_revision:
        _emit(False, "stale_revision")
        return False
    revision = current_revision

    payload = {
        "diagram_id": str(diagram.pk),
        "storage_path": diagram.storage_path,
        "revision": revision,
        "source": source or "",
    }
    body = json.dumps(payload).encode("utf-8")
    try:
        headers = {"Content-Type": "application/json"}
        headers.update(scope_headers(diagram, "export", revision=revision))
        request = Request(export_url, data=body, headers=headers, method="POST")
        timeout = int(getattr(settings, "LIKEC4_EXPORT_TIMEOUT", 60))
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                _emit(False, "http_status")
                return False
        _emit(True, "ok")
        return True
    except HTTPError as exc:  # pragma: no cover - best effort export
        logger.warning("LikeC4 export request failed for diagram %s: status=%s", diagram.pk, exc.code)
        _emit(False, "http_error")
        return False
    except Exception as exc:  # pragma: no cover - best effort enqueue
        logger.warning("LikeC4 export request failed for diagram %s: %s", diagram.pk, type(exc).__name__)
        _emit(False, "exception")
        return False
