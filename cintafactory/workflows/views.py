# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import re
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


_MISSING_NOTIFICATION_VALUE = object()
_MAX_NOTIFICATION_DETAIL_DEPTH = 10


def _parse_notification_json(value):
    """Parse a serialized JSON value while preserving ordinary text."""
    if not isinstance(value, str) or not value.strip():
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, RecursionError):
        return value


def _normalise_notification_value(value, depth: int = 0):
    if depth > _MAX_NOTIFICATION_DETAIL_DEPTH:
        return value
    if isinstance(value, str):
        parsed = _parse_notification_json(value)
        if isinstance(parsed, str):
            return parsed
        return _normalise_notification_value(parsed, depth + 1)
    if isinstance(value, dict):
        return {
            key: _normalise_notification_value(child, depth + 1)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_normalise_notification_value(child, depth + 1) for child in value]
    return value


def _notification_field_label(value) -> str:
    label = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
    words = re.split(r"[_\-\s]+", label.strip())
    acronyms = {"api", "dat", "id", "url"}
    return " ".join(
        word.upper() if word.casefold() in acronyms else word.capitalize()
        for word in words
        if word
    )


def _notification_scalar(value) -> str:
    if value is _MISSING_NOTIFICATION_VALUE or value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    return str(value)


def _notification_value_summary(value) -> str:
    if value is _MISSING_NOTIFICATION_VALUE or value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    if isinstance(value, dict):
        count = len(value)
        return f"{count} champ" if count == 1 else f"{count} champs"
    if isinstance(value, list):
        count = len(value)
        return f"{count} élément" if count == 1 else f"{count} éléments"
    return str(value)


def _notification_value_lines(value) -> list[dict[str, object]]:
    """Flatten nested JSON into safe, labeled lines for the notification template."""
    lines: list[dict[str, object]] = []

    def add(label, scalar, depth, *, group=False):
        lines.append({
            "label": label,
            "value": scalar,
            "depth": min(depth, _MAX_NOTIFICATION_DETAIL_DEPTH),
            "is_group": group,
        })

    def visit(current, label, depth):
        if depth > _MAX_NOTIFICATION_DETAIL_DEPTH:
            add(label or "Détail", "Contenu imbriqué : consulter le JSON brut.", depth)
            return
        if isinstance(current, dict):
            if not current:
                add(label or "Détail", "Aucun champ.", depth)
                return
            if label:
                add(label, "", depth, group=True)
            for key, child in current.items():
                child_label = _notification_field_label(key)
                if isinstance(child, (dict, list)):
                    visit(child, child_label, depth + 1)
                else:
                    add(child_label, _notification_scalar(child), depth + 1)
            return
        if isinstance(current, list):
            if not current:
                add(label or "Détail", "Aucun élément.", depth)
                return
            if label:
                add(label, "", depth, group=True)
            for index, child in enumerate(current, start=1):
                visit(child, f"Élément {index}", depth + 1)
            return
        add(label or "Valeur", _notification_scalar(current), depth)

    visit(value, "", 0)
    return lines


def _notification_identity_token(value):
    if value is None or value == "":
        return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return None


def _notification_array_identity_key(before: list, after: list) -> str | None:
    if (
        not before
        or not after
        or not all(isinstance(row, dict) for row in before)
        or not all(isinstance(row, dict) for row in after)
    ):
        return None
    shared_keys = set(before[0]).intersection(after[0])
    for row in before:
        shared_keys.intersection_update(row)
    for row in after:
        shared_keys.intersection_update(row)
    candidates = [key for key in ("id", "key") if key in shared_keys]
    candidates.extend(
        sorted(
            key
            for key in shared_keys
            if key.endswith("_id") and key not in {"id", "key"}
        )
    )
    for key in candidates:
        before_tokens = [_notification_identity_token(row.get(key)) for row in before]
        after_tokens = [_notification_identity_token(row.get(key)) for row in after]
        if (
            all(token is not None for token in before_tokens)
            and all(token is not None for token in after_tokens)
            and len(before_tokens) == len(set(before_tokens))
            and len(after_tokens) == len(set(after_tokens))
        ):
            return key
    return None


def _notification_diff_path(path: str, label: str) -> str:
    return f"{path} · {label}" if path else label


def _notification_diff_leaf(path: str, kind: str, before, after) -> dict[str, str]:
    return {
        "path": path or "Valeur",
        "kind": kind,
        "before": _notification_value_summary(before),
        "after": _notification_value_summary(after),
    }


def _notification_added_or_removed_leaf(value, path: str, kind: str) -> dict[str, str]:
    before = value if kind == "removed" else _MISSING_NOTIFICATION_VALUE
    after = _MISSING_NOTIFICATION_VALUE if kind == "removed" else value
    return _notification_diff_leaf(path, kind, before, after)


def _notification_added_or_removed(
    value,
    path: str,
    kind: str,
    depth: int = 0,
) -> list[dict[str, str]]:
    if depth > _MAX_NOTIFICATION_DETAIL_DEPTH:
        return [_notification_added_or_removed_leaf(value, path, kind)]
    if isinstance(value, dict):
        if not value:
            return [_notification_added_or_removed_leaf(value, path, kind)]
        result = []
        for key, child in value.items():
            result.extend(_notification_added_or_removed(
                child,
                _notification_diff_path(path, _notification_field_label(key)),
                kind,
                depth + 1,
            ))
        return result
    if isinstance(value, list):
        if not value:
            return [_notification_added_or_removed_leaf(value, path, kind)]
        result = []
        for index, child in enumerate(value, start=1):
            result.extend(_notification_added_or_removed(
                child,
                _notification_diff_path(path, f"Élément {index}"),
                kind,
                depth + 1,
            ))
        return result
    before = value if kind == "removed" else _MISSING_NOTIFICATION_VALUE
    after = _MISSING_NOTIFICATION_VALUE if kind == "removed" else value
    return [_notification_diff_leaf(path, kind, before, after)]


def _notification_value_differences(
    before,
    after,
    path: str = "",
    depth: int = 0,
) -> list[dict[str, str]]:
    if type(before) is type(after) and before == after:
        return []
    if depth > _MAX_NOTIFICATION_DETAIL_DEPTH:
        return [_notification_diff_leaf(path, "changed", before, after)]
    if before is _MISSING_NOTIFICATION_VALUE:
        return _notification_added_or_removed(after, path, "added", depth)
    if after is _MISSING_NOTIFICATION_VALUE:
        return _notification_added_or_removed(before, path, "removed", depth)
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in dict.fromkeys((*before.keys(), *after.keys())):
            child_path = _notification_diff_path(path, _notification_field_label(key))
            result.extend(_notification_value_differences(
                before.get(key, _MISSING_NOTIFICATION_VALUE),
                after.get(key, _MISSING_NOTIFICATION_VALUE),
                child_path,
                depth + 1,
            ))
        return result
    if isinstance(before, list) and isinstance(after, list):
        identity_key = _notification_array_identity_key(before, after)
        if identity_key:
            before_by_id = {
                _notification_identity_token(row[identity_key]): row
                for row in before
            }
            after_by_id = {
                _notification_identity_token(row[identity_key]): row
                for row in after
            }
            result = []
            for row in after:
                token = _notification_identity_token(row[identity_key])
                identity_label = (
                    f"{_notification_field_label(identity_key)} {row[identity_key]}"
                )
                row_path = _notification_diff_path(path, identity_label)
                old_row = before_by_id.get(token, _MISSING_NOTIFICATION_VALUE)
                if old_row is _MISSING_NOTIFICATION_VALUE:
                    result.extend(
                        _notification_added_or_removed(row, row_path, "added", depth + 1)
                    )
                else:
                    result.extend(
                        _notification_value_differences(old_row, row, row_path, depth + 1)
                    )
            for row in before:
                token = _notification_identity_token(row[identity_key])
                if token not in after_by_id:
                    identity_label = (
                        f"{_notification_field_label(identity_key)} {row[identity_key]}"
                    )
                    result.extend(_notification_added_or_removed(
                        row,
                        _notification_diff_path(path, identity_label),
                        "removed",
                        depth + 1,
                    ))
            return result
        result = []
        for index in range(max(len(before), len(after))):
            row_path = _notification_diff_path(path, f"Élément {index + 1}")
            old_value = before[index] if index < len(before) else _MISSING_NOTIFICATION_VALUE
            new_value = after[index] if index < len(after) else _MISSING_NOTIFICATION_VALUE
            result.extend(
                _notification_value_differences(old_value, new_value, row_path, depth + 1)
            )
        return result
    if isinstance(before, (dict, list)) or isinstance(after, (dict, list)):
        return [_notification_diff_leaf(path, "changed", before, after)]
    return [_notification_diff_leaf(path, "changed", before, after)]


def _notification_change_views(details) -> list[dict[str, object]]:
    details = _normalise_notification_value(_parse_notification_json(details))
    changes = details.get("changes") if isinstance(details, dict) else None
    if not isinstance(changes, dict):
        return []
    result = []
    for key, change in changes.items():
        if not isinstance(change, dict):
            change = {"to": change}
        raw_before = change.get("from", _MISSING_NOTIFICATION_VALUE)
        raw_after = change.get("to", _MISSING_NOTIFICATION_VALUE)
        before = _normalise_notification_value(_parse_notification_json(raw_before))
        after = _normalise_notification_value(_parse_notification_json(raw_after))
        result.append({
            "label": str(change.get("label") or _notification_field_label(key)),
            "part": str(change.get("part") or ""),
            "before_lines": _notification_value_lines(before),
            "after_lines": _notification_value_lines(after),
            "differences": _notification_value_differences(before, after),
        })
    return result


def _notification_detail_lines(details) -> list[dict[str, object]]:
    return _notification_value_lines(
        _normalise_notification_value(_parse_notification_json(details))
    )


def _notification_has_details(details) -> bool:
    return not (details is None or details == "" or details == {} or details == [])


def _notification_raw_json(details) -> str:
    if not _notification_has_details(details):
        return ""
    try:
        return json.dumps(details, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError, RecursionError):
        return str(details)


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
        if active_spec.get("schema_version") == 2:
            from .graph_schema import SUMMARY_STATES
            active_spec = {"steps": [{"status": code, "lane": lane} for code, _name, lane, _caps in SUMMARY_STATES]}
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
                details = entry.history.details
                change_views = (
                    _notification_change_views(details)
                    if entry.history.action in {
                        DATHistoryAction.UPDATED,
                        DATHistoryAction.SECTION_UPDATED,
                    }
                    else []
                )
                if entry.history.action in {
                    DATHistoryAction.STATUS_CHANGED,
                    DATHistoryAction.OWNER_CHANGED,
                    DATHistoryAction.SECTION_UPDATED,
                } or (entry.history.action == DATHistoryAction.UPDATED and change_views):
                    detail_lines = []
                else:
                    detail_lines = (
                        _notification_detail_lines(details)
                        if _notification_has_details(details)
                        else []
                    )
                payload.update(
                    {
                        "history": entry.history,
                        "title": entry.history.get_action_display(),
                        "actor_name": entry.history.actor_name(),
                        "created_at": entry.history.performed_at,
                        "details": details or {},
                        "detail_lines": detail_lines,
                        "change_views": change_views,
                        "raw_details_json": _notification_raw_json(details),
                        "action": entry.history.action,
                        "status_from": entry.history.status_change_from,
                        "status_to": entry.history.status_change_to,
                        "is_unread": entry.history.id not in seen_ids,
                    }
                )
            elif entry.user_notification is not None:
                details = entry.user_notification.extra_data
                payload.update(
                    {
                        "user_notification": entry.user_notification,
                        "title": entry.user_notification.title,
                        "actor_name": entry.user_notification.actor_name,
                        "created_at": entry.user_notification.created_at,
                        "message": entry.user_notification.message,
                        "details": details or {},
                        "detail_lines": (
                            _notification_detail_lines(details)
                            if _notification_has_details(details)
                            else []
                        ),
                        "raw_details_json": _notification_raw_json(details),
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
            from .graph_engine import graph_context, is_graph
            from .services import ensure_workflow_instance
            instance = ensure_workflow_instance(dat, workflow_code=self.workflow_code)
            if is_graph(instance):
                graph = graph_context(dat, self.request.user)
                if graph is None or instance.current_state in {"valider", "refuse"}:
                    continue
                role = dat.participants.filter(user=self.request.user).values_list("role__slug", flat=True).first()
                relevant = [node for node in graph["nodes"] if role in node["roles"]]
                if graph["has_actions"]:
                    columns["in_progress"].append(dat)
                elif relevant:
                    key = "blocked" if any(node["attempt"].status != "completed" for node in relevant) else "validation"
                    columns[key].append(dat)
                continue
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
