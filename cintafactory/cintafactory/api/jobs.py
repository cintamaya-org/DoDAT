# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.utils import timezone
from django.db.models import QuerySet
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated

from cintafactory.async_jobs import dispatch_async_job
from cintafactory.models import AsyncJob
from cintafactory.api.permissions import CanUseAPI

STAFF_PERMISSION_REQUIRED = "Staff permission required."


class AsyncJobSerializer(serializers.ModelSerializer):
    job_id = serializers.UUIDField(source="id", read_only=True)

    class Meta:
        model = AsyncJob
        fields = [
            "job_id",
            "job_type",
            "queue_name",
            "status",
            "resource_ref",
            "requested_by",
            "created_at",
            "started_at",
            "finished_at",
            "attempt_count",
            "max_attempts",
            "last_error",
            "result_payload",
        ]
        read_only_fields = fields


class AsyncJobViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AsyncJobSerializer
    permission_classes = [CanUseAPI, IsAuthenticated]

    def get_queryset(self) -> QuerySet:
        queryset = AsyncJob.objects.select_related("requested_by").all()
        resource_ref = str(self.request.query_params.get("resource_ref", "")).strip()
        if resource_ref:
            queryset = queryset.filter(resource_ref=resource_ref)
        if self.request.user.is_staff or self.request.user.is_superuser:
            return queryset
        return queryset.filter(requested_by=self.request.user)

    def _ensure_staff(self, request):
        return bool(request.user and request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        job = self.get_object()
        if not self._ensure_staff(request):
            return Response({"detail": STAFF_PERMISSION_REQUIRED}, status=status.HTTP_403_FORBIDDEN)
        if job.status not in {AsyncJob.Status.QUEUED, AsyncJob.Status.RUNNING}:
            return Response({"ok": False, "error": "invalid_status", "status": job.status}, status=status.HTTP_400_BAD_REQUEST)
        job.status = AsyncJob.Status.CANCELLED
        job.finished_at = timezone.now()
        job.last_error = "Cancelled by operator."
        job.save(update_fields=["status", "finished_at", "last_error", "updated_at"])
        return Response({"ok": True, "job_id": str(job.id), "status": job.status})

    @action(detail=True, methods=["post"])
    def requeue(self, request, pk=None):
        job = self.get_object()
        if not self._ensure_staff(request):
            return Response({"detail": STAFF_PERMISSION_REQUIRED}, status=status.HTTP_403_FORBIDDEN)
        if job.status not in {AsyncJob.Status.DEAD_LETTERED, AsyncJob.Status.FAILED, AsyncJob.Status.CANCELLED}:
            return Response({"ok": False, "error": "invalid_status", "status": job.status}, status=status.HTTP_400_BAD_REQUEST)
        job.status = AsyncJob.Status.QUEUED
        job.started_at = None
        job.finished_at = None
        job.attempt_count = 0
        job.last_error = ""
        job.save(update_fields=["status", "started_at", "finished_at", "attempt_count", "last_error", "updated_at"])
        dispatch_async_job(job.id)
        return Response({"ok": True, "job_id": str(job.id), "status": job.status})

    @action(detail=True, methods=["post"])
    def ignore(self, request, pk=None):
        job = self.get_object()
        if not self._ensure_staff(request):
            return Response({"detail": STAFF_PERMISSION_REQUIRED}, status=status.HTTP_403_FORBIDDEN)
        reason = str(request.data.get("reason", "")).strip() or "ignored"
        job.status = AsyncJob.Status.CANCELLED
        job.finished_at = timezone.now()
        job.last_error = f"Ignored by operator: {reason}"[:4000]
        job.save(update_fields=["status", "finished_at", "last_error", "updated_at"])
        return Response({"ok": True, "job_id": str(job.id), "status": job.status})
