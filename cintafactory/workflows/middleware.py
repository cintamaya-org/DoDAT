# SPDX-License-Identifier: AGPL-3.0-only
"""Translate a review race at a model write boundary into a reloadable conflict."""

from django.http import HttpResponse, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from .exceptions import WorkflowPermissionDenied, WorkflowTransitionUnavailable


class WorkflowConflictMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        if isinstance(exception, (WorkflowTransitionUnavailable, WorkflowPermissionDenied)):
            status = 403 if isinstance(exception, WorkflowPermissionDenied) else 409
            message = str(exception)
            if request.content_type == "application/json" or request.path.startswith("/api/"):
                return JsonResponse({"error": "workflow_unavailable", "message": message}, status=status)
            return HttpResponse(message + " Actualisez la page avant de continuer.", status=status, content_type="text/plain; charset=utf-8")
        return None
