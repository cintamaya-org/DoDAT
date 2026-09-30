# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from uuid import UUID

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.functional import cached_property
from django.views.generic import TemplateView

from dat.models import DATHistoryAction
from dat.permissions import filter_dat_queryset_for_user

from .models import Workflow
from .services import (
    bind_workflow_instances,
    workflow_actor_has_any_state_permission,
    workflow_has_capability,
    workflow_state_metadata,
)
from .notifications import (
    fetch_notifications_for_user,
    get_unread_notification_count,
    get_seen_notification_ids,
    mark_all_notifications_as_seen,
    mark_notifications_as_seen,
    mark_user_notifications_as_viewed,
    notification_count_for_user,
    notification_queryset_for_user,
    user_notification_queryset,
)

from cintafactory.pagination import DEFAULT_PAGE_SIZE


class WorkflowOverviewView(LoginRequiredMixin, TemplateView):
    """Static overview describing the workflow journey."""

    # template_name = "workflows/overview.html"


class WorkflowBoardView(LoginRequiredMixin, TemplateView):
    """Display the DAT validation workflow as a Kanban board."""

    template_name = "workflows/board.html"
    workflow_code = "dat-validation"
    paginate_by = DEFAULT_PAGE_SIZE
    cursor_salt = "workflows.workflow-board.cursor"
    column_titles = {
        "initial": "Nouveau besoin",
        "in_progress": "Projets en cours",
        "completed": "Projets terminés",
    }

    @cached_property
    def workflow(self) -> Workflow:
        queryset = (
            Workflow.objects.filter(code=self.workflow_code, is_active=True)
            .select_related("content_type", "active_version")
            .prefetch_related(
                "steps__permissions__role",
                "steps__permissions__user",
            )
        )
        return get_object_or_404(queryset)

    @cached_property
    def workflow_model(self):
        return self.workflow.content_type.model_class()

    @staticmethod
    def _aggregate_permissions(steps, accessor: str):
        aggregated = []
        seen = set()
        for step in steps:
            for permission in getattr(step, accessor).all():
                pk = getattr(permission, "pk", None)
                if pk is not None:
                    if pk in seen:
                        continue
                    seen.add(pk)
                aggregated.append(permission)
        return aggregated

    def _build_column(self, *, key, states, step_by_state, dat_items):
        steps = [step_by_state[state] for state in states if state in step_by_state]
        status_codes = [step.state for step in steps]
        items = [dat for dat in dat_items if dat.workflow_lane == key]
        description = steps[0].description if len(steps) == 1 else ""
        return {
            "key": key,
            "title": self.column_titles[key],
            "description": description,
            "status_codes": status_codes,
            "status_labels": [step.name for step in steps],
            "items": items,
            "read_permissions": self._aggregate_permissions(steps, "read_permissions"),
            "write_permissions": self._aggregate_permissions(steps, "write_permissions"),
        }

    def _decode_cursor(self, model):
        token = self.request.GET.get("cursor")
        if not token:
            return None
        try:
            payload = signing.loads(token, salt=self.cursor_salt)
            if not isinstance(payload, dict):
                return None
            updated_at = parse_datetime(payload["updated_at"])
            object_id = model._meta.pk.to_python(payload["pk"])
        except (
            signing.BadSignature,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            return None
        if updated_at is None:
            return None
        return updated_at, object_id

    def _encode_cursor(self, dat):
        return signing.dumps(
            {"updated_at": dat.updated_at.isoformat(), "pk": str(dat.pk)},
            salt=self.cursor_salt,
            compress=True,
        )

    def _cursor_url(self, dat, *, direction):
        query = self.request.GET.copy()
        for key in ("cursor", "direction", "page"):
            query.pop(key, None)
        query["cursor"] = self._encode_cursor(dat)
        query["direction"] = direction
        return f"{self.request.path}?{query.urlencode()}"

    def get_dat_items(self):
        model = self.workflow_model
        if model is None:
            return []

        relation_field_names = {
            field.name
            for field in model._meta.fields
            if field.is_relation
            and not field.many_to_many
            and field.related_model is not None
        }
        related_fields = [
            field_name
            for field_name in ("owner", "application")
            if field_name in relation_field_names
        ]
        dat_queryset = filter_dat_queryset_for_user(
            model.objects.all(),
            self.request.user,
        )
        if related_fields:
            dat_queryset = dat_queryset.select_related(*related_fields)
        base_queryset = dat_queryset
        cursor = self._decode_cursor(model)
        direction = self.request.GET.get("direction")
        if direction not in {"next", "previous"}:
            direction = "next"
        if cursor is None:
            direction = "next"

        if cursor is not None:
            updated_at, object_id = cursor
            if direction == "previous":
                dat_queryset = dat_queryset.filter(
                    Q(updated_at__gt=updated_at)
                    | Q(updated_at=updated_at, pk__gt=object_id)
                ).order_by("updated_at", "pk")
            else:
                dat_queryset = dat_queryset.filter(
                    Q(updated_at__lt=updated_at)
                    | Q(updated_at=updated_at, pk__lt=object_id)
                ).order_by("-updated_at", "-pk")
        else:
            dat_queryset = dat_queryset.order_by("-updated_at", "-pk")

        page_rows = list(dat_queryset[: self.paginate_by + 1])
        has_more_in_direction = len(page_rows) > self.paginate_by
        dat_items = page_rows[: self.paginate_by]
        if cursor is not None and direction == "previous":
            dat_items.reverse()
            self.has_previous = has_more_in_direction
            self.has_next = bool(dat_items)
        elif cursor is not None:
            self.has_previous = bool(dat_items)
            self.has_next = has_more_in_direction
        else:
            self.has_previous = False
            self.has_next = has_more_in_direction

        # A stale cursor can point beyond the current result set after DAT changes.
        if cursor is not None and not dat_items:
            page_rows = list(
                base_queryset.order_by("-updated_at", "-pk")[: self.paginate_by + 1]
            )
            dat_items = page_rows[: self.paginate_by]
            self.has_previous = False
            self.has_next = len(page_rows) > self.paginate_by

        self.previous_url = (
            self._cursor_url(dat_items[0], direction="previous")
            if self.has_previous and dat_items
            else ""
        )
        self.next_url = (
            self._cursor_url(dat_items[-1], direction="next")
            if self.has_next and dat_items
            else ""
        )
        self.page_item_count = len(dat_items)
        bind_workflow_instances(dat_items, workflow_code=self.workflow_code)

        for dat in dat_items:
            metadata = workflow_state_metadata(dat, workflow_code=self.workflow_code)
            dat.workflow_lane = metadata.get("lane", "in_progress")

        return dat_items

    def get_columns(self):
        dat_items = self.get_dat_items()
        steps = list(self.workflow.steps.all())
        step_by_state = {step.state: step for step in steps}

        active_spec = self.workflow.active_version.specification if self.workflow.active_version else {}
        state_lanes = {
            item.get("status"): item.get("lane", "in_progress")
            for item in active_spec.get("steps", [])
        }
        initial_states = [step.state for step in steps if state_lanes.get(step.state) == "initial"]
        in_progress_states = [
            step.state for step in steps if state_lanes.get(step.state) == "in_progress"
        ]
        completed_states = [step.state for step in steps if state_lanes.get(step.state) == "completed"]

        return [
            self._build_column(
                key="initial",
                states=initial_states,
                step_by_state=step_by_state,
                dat_items=dat_items,
            ),
            self._build_column(
                key="in_progress",
                states=in_progress_states,
                step_by_state=step_by_state,
                dat_items=dat_items,
            ),
            self._build_column(
                key="completed",
                states=completed_states,
                step_by_state=step_by_state,
                dat_items=dat_items,
            ),
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status_choices = []
        model = self.workflow_model
        if model is not None:
            try:
                status_field = model._meta.get_field("status")
                status_choices = list(status_field.flatchoices)
            except Exception:
                status_choices = []
        context.update(
            {
                "workflow": self.workflow,
                "columns": self.get_columns(),
                "all_statuses": status_choices,
                "has_previous": getattr(self, "has_previous", False),
                "previous_url": getattr(self, "previous_url", ""),
                "has_next": getattr(self, "has_next", False),
                "next_url": getattr(self, "next_url", ""),
                "page_item_count": getattr(self, "page_item_count", 0),
                "is_paginated": bool(
                    getattr(self, "has_previous", False)
                    or getattr(self, "has_next", False)
                ),
            }
        )
        return context


class WorkflowNotificationsView(LoginRequiredMixin, TemplateView):
    """Timeline-like view listing history events relevant to the connected user."""

    template_name = "workflows/notifications.html"
    notification_limit = DEFAULT_PAGE_SIZE

    def post(self, request, *args, **kwargs):
        if request.POST.get("mark_all") == "1":
            mark_all_notifications_as_seen(request.user)
            messages.success(request, "Toutes les notifications ont été marquées comme lues.")
        elif request.POST.get("mark_notification") == "1":
            notification_source = request.POST.get("notification_source")
            notification_id = request.POST.get("notification_id")
            marked = False
            if notification_source == "history":
                try:
                    history_id = int(notification_id)
                except (TypeError, ValueError):
                    history_id = None
                if history_id is not None and notification_queryset_for_user(request.user).filter(
                    pk=history_id
                ).exists():
                    mark_notifications_as_seen(request, [history_id])
                    marked = True
            elif notification_source == "user":
                try:
                    user_notification_id = UUID(notification_id)
                except (TypeError, ValueError, AttributeError):
                    user_notification_id = None
                if user_notification_id is not None and user_notification_queryset(
                    request.user
                ).filter(pk=user_notification_id, viewed_at__isnull=True).exists():
                    mark_user_notifications_as_viewed(request.user, [user_notification_id])
                    marked = True
            if marked:
                messages.success(request, "Notification marquée comme lue.")

        redirect_url = reverse("workflows:notifications")
        try:
            page_number = int(request.POST.get("page", "1"))
        except (TypeError, ValueError):
            page_number = 1
        if page_number > 1:
            redirect_url = f"{redirect_url}?page={page_number}"
        return redirect(redirect_url)

    def get_notifications(self, *, offset=0):
        entries = fetch_notifications_for_user(
            self.request.user,
            limit=self.notification_limit,
            offset=offset,
            with_related=True,
        )
        history_ids = [entry.history.id for entry in entries if entry.history is not None]
        seen_ids = get_seen_notification_ids(self.request, history_ids=history_ids)
        notifications = []
        for entry in entries:
            dat = entry.dat
            application = getattr(dat, "application", None) if dat else None
            payload = {
                "source": entry.source,
                "dat_link": reverse("dat:my_detail", args=[dat.pk]) if dat else "",
                "dat_reference": getattr(dat, "reference", "") if dat else "",
                "dat_title": getattr(dat, "title", "") if dat else "",
                "dat_application_name": getattr(application, "name", "") if application else "",
            }
            if entry.history is not None:
                payload.update(
                    {
                        "history": entry.history,
                        "title": entry.history.get_action_display(),
                        "actor_name": entry.history.actor_name(),
                        "created_at": entry.history.performed_at,
                        "details": entry.history.details or {},
                        "action": entry.history.action,
                        "status_from": entry.history.status_change_from,
                        "status_to": entry.history.status_change_to,
                        "is_unread": entry.history.id not in seen_ids,
                    }
                )
            elif entry.user_notification is not None:
                payload.update(
                    {
                        "user_notification": entry.user_notification,
                        "title": entry.user_notification.title,
                        "actor_name": entry.user_notification.actor_name,
                        "created_at": entry.user_notification.created_at,
                        "message": entry.user_notification.message,
                        "details": entry.user_notification.extra_data or {},
                        "action": entry.user_notification.level,
                        "level": entry.user_notification.level,
                        "level_display": entry.user_notification.get_level_display(),
                        "target_url": entry.user_notification.target_url,
                        "is_unread": not entry.user_notification.is_viewed,
                    }
                )
            notifications.append(payload)
        return notifications

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        total = notification_count_for_user(self.request.user)
        paginator = Paginator(range(total), self.notification_limit)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        notifications = self.get_notifications(offset=page_obj.start_index() - 1)
        context.update(
            {
                "notifications": notifications,
                "history_actions": DATHistoryAction,
                "notifications_unread_count": get_unread_notification_count(self.request),
                "paginator": paginator,
                "page_obj": page_obj,
                "is_paginated": paginator.num_pages > 1,
                "current_module": None,
            }
        )
        return context


class MyTasksBoardView(WorkflowBoardView):
    """Personal Kanban board focusing on the connected user's duties."""

    column_titles = {
        "blocked": "Bloquer",
        "in_progress": "En cours",
        "validation": "Validation",
    }

    column_descriptions = {
        "blocked": (
            "Au moins une autre personne doit agir avant que vous puissiez continuer sur ce DAT."
        ),
        "in_progress": (
            "Ces DAT attendent une action de votre part selon vos autorisations actuelles."
        ),
        "validation": (
            "Vous avez realise vos actions et attendez la finalisation par les autres intervenants."
        ),
    }

    def get_columns(self):
        if self.workflow.active_version_id is None:
            return []

        columns = {
            "blocked": [],
            "in_progress": [],
            "validation": [],
        }

        dat_items = self.get_dat_items()
        for dat in dat_items:
            if not workflow_actor_has_any_state_permission(
                dat,
                self.request.user,
                workflow_code=self.workflow_code,
            ):
                continue
            if workflow_has_capability(dat, "terminal", workflow_code=self.workflow_code):
                # Nothing to do once workflow reaches terminal capability.
                continue
            if workflow_has_capability(dat, "reviewable", workflow_code=self.workflow_code):
                columns["validation"].append(dat)
                continue

            columns["in_progress"].append(dat)

        ordered_keys = ["blocked", "in_progress", "validation"]
        return [
            {
                "key": key,
                "title": self.column_titles[key],
                "description": self.column_descriptions.get(key, ""),
                "status_codes": [],
                "status_labels": [],
                "items": columns.get(key, []),
                "read_permissions": [],
                "write_permissions": [],
            }
            for key in ordered_keys
        ]
