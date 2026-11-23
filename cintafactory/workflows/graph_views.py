# SPDX-License-Identifier: AGPL-3.0-only
"""Review endpoints share one authorization path for HTML and JSON clients."""

import json

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import CharField, Q, Subquery
from django.db.models.functions import Cast
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from dat.models import DAT
from dat.permissions import filter_dat_queryset_for_user

from .exceptions import WorkflowPermissionDenied, WorkflowTransitionUnavailable
from .graph_engine import WorkflowConflict, act_on_node, graph_context
from .review_diagram import review_diagram
from .models import WorkflowApprovalDecision, WorkflowContentSnapshot


def _dat(user, dat_pk):
    return get_object_or_404(filter_dat_queryset_for_user(DAT.objects.select_related("application"), user), pk=dat_pk)


def _payload(graph):
    return {
        "state": graph["instance"].current_state,
        "version": graph["instance"].definition_version.version,
        "refusal_links": graph["refusal_links"],
        "require_section_referents": graph["require_section_referents"],
        "nodes": [
            {"id": node["id"], "name": node["name"], "type": node["type"], "target": node["target"],
             "after": node.get("after", []), "roles": node["roles"], "remaining_roles": node["remaining_roles"], "status": node["attempt"].status,
             "attempt_id": str(node["attempt"].pk), "number": node["attempt"].number, "snapshot_id": str(node["attempt"].snapshot_id) if node["attempt"].snapshot_id else None,
             "blockers": node["attempt"].blockers, "can_submit": node["can_submit"], "can_approve": node["can_approve"],
             "section_referent": node["section_referent"], "on_reject": node["refusal_policy"], "can_retry": node["can_retry"],
             "decisions": [{"actor": decision.actor_display, "role": decision.role_slug, "outcome": decision.outcome,
                            "comment": decision.comment, "at": decision.created_at.isoformat()} for decision in node["decisions"]]}
            for node in graph["nodes"]
        ],
    }


@login_required
@require_GET
def graph_detail(request, dat_pk):
    dat = _dat(request.user, dat_pk)
    graph = graph_context(dat, request.user)
    if graph is None:
        return render(request, "workflows/graph_detail.html", {"dat": dat, "approval_graph": None, "current_module": apps.get_app_config("workflows")})
    if request.GET.get("format") == "json":
        return JsonResponse(_payload(graph))
    history = Paginator(graph["instance"].transition_events.select_related("actor").order_by("-occurred_at", "-pk"), 50).get_page(request.GET.get("page"))
    decisions = Paginator(WorkflowApprovalDecision.objects.filter(attempt__instance=graph["instance"]).select_related("attempt", "snapshot").order_by("-created_at", "-pk"), 50).get_page(request.GET.get("decisions_page"))
    return render(request, "workflows/graph_detail.html", {"dat": dat, "approval_graph": graph, "review_diagram": review_diagram(dat, graph), "history": history, "decision_history": decisions, "current_module": apps.get_app_config("workflows")})


@login_required
@require_POST
def node_action(request, dat_pk, node_id):
    dat = _dat(request.user, dat_pk)
    wants_json = request.content_type == "application/json"
    if wants_json:
        if len(request.body) > 65536:
            return JsonResponse({"error": "payload_too_large"}, status=413)
        try:
            payload = json.loads(request.body)
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError, RecursionError):
            return JsonResponse({"error": "invalid_json"}, status=400)
    else:
        payload = request.POST
    try:
        decision = act_on_node(dat, node_id, request.user, outcome=payload.get("outcome"),
                               attempt_id=payload.get("attempt_id"), request_id=payload.get("request_id"), comment=payload.get("comment", ""))
    except WorkflowPermissionDenied as exc:
        raise PermissionDenied(str(exc)) from exc
    except WorkflowTransitionUnavailable as exc:
        if wants_json:
            return JsonResponse({"error": "stale_review" if isinstance(exc, WorkflowConflict) else "unavailable", "message": str(exc)}, status=409 if isinstance(exc, WorkflowConflict) else 400)
        messages.error(request, str(exc))
    else:
        if wants_json:
            return JsonResponse({"decision_id": str(decision.pk), "workflow": _payload(graph_context(dat, request.user))})
        messages.success(request, "Décision enregistrée.")
    return redirect("workflows:graph_detail", dat_pk=dat.pk)


@login_required
@require_GET
def review_snapshot(request, dat_pk, snapshot_pk):
    dat = _dat(request.user, dat_pk)
    snapshot = get_object_or_404(WorkflowContentSnapshot, pk=snapshot_pk, instance__object_id=str(dat.pk), instance__content_type__app_label="dat", instance__content_type__model="dat")
    if request.GET.get("format") == "json":
        return JsonResponse({"id": str(snapshot.pk), "target": snapshot.target, "digest": snapshot.digest, "content": snapshot.content, "at": snapshot.created_at.isoformat()})
    parts = []
    for part in snapshot.content.get("parts", []):
        value = part["value"]
        display = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else "Non renseigné" if value is None else str(value)
        parts.append({**part, "display": display})
    return render(request, "workflows/review_snapshot.html", {"dat": dat, "snapshot": snapshot, "parts": parts, "current_module": apps.get_app_config("workflows")})


@login_required
@require_GET
def review_asset(request, dat_pk, snapshot_pk, checksum):
    import re
    from cintafactory.storage.seaweedfs_storage import SeaweedFSStorage

    dat = _dat(request.user, dat_pk)
    snapshot = get_object_or_404(WorkflowContentSnapshot, pk=snapshot_pk, instance__object_id=str(dat.pk))
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise Http404
    artifact = next((item for item in snapshot.content.get("artifacts", []) if item["sha256"] == checksum), None)
    if artifact is None or artifact["path"] != f"workflow-snapshots/{dat.pk}/{checksum}":
        raise Http404
    try:
        handle = SeaweedFSStorage().open(artifact["path"], "rb")
    except OSError:
        raise Http404 from None
    response = FileResponse(handle, as_attachment=True, filename=artifact["name"], content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def my_approvals(request):
    visible_dats = filter_dat_queryset_for_user(DAT.objects.all(), request.user)
    queryset = visible_dats.exclude(status__in=["valider", "refuse"]).filter(
        Q(participants__user=request.user) | Q(section_responsibles__user=request.user),
    ).distinct().order_by("-updated_at", "-pk")
    page = Paginator(queryset, 20).get_page(request.GET.get("page"))
    tasks = []
    pending_tasks = []
    for dat in page:
        graph = graph_context(dat, request.user)
        if graph is None or graph["instance"].current_state in {"valider", "refuse"}:
            continue
        roles = set(dat.participants.filter(user=request.user).values_list("role__slug", flat=True))
        for node in graph["nodes"]:
            if node["type"] != "approval" or node["attempt"].status not in {"waiting", "ready", "refusal_blocked"}:
                continue
            already_decided = any(decision.actor_id == request.user.pk for decision in node["decisions"])
            if already_decided:
                continue
            if roles.intersection(node["roles"]) or node["is_section_referent"] or node["can_retry"]:
                task = {"dat": dat, "node": node}
                if node["can_approve"] or node["can_retry"]:
                    pending_tasks.append(task)
                else:
                    tasks.append(task)

    dat_object_ids = visible_dats.annotate(workflow_object_id=Cast("pk", output_field=CharField())).values("workflow_object_id")
    history_queryset = WorkflowApprovalDecision.objects.filter(
        actor=request.user,
        outcome__in=["approve", "request_changes", "reject"],
        attempt__instance__content_type=ContentType.objects.get_for_model(DAT),
        attempt__instance__object_id__in=Subquery(dat_object_ids),
    ).select_related("attempt", "attempt__instance", "attempt__instance__definition_version").order_by("-created_at", "-pk")
    history_page = Paginator(history_queryset, 20).get_page(request.GET.get("history_page"))
    history_dat_ids = [decision.attempt.instance.object_id for decision in history_page]
    history_dats = {
        str(dat.pk): dat
        for dat in visible_dats.filter(pk__in=history_dat_ids)
    }
    outcome_labels = {"approve": "Approuvée", "request_changes": "Corrections demandées", "reject": "Refusée"}
    approval_history = []
    for decision in history_page:
        specification = decision.attempt.instance.definition_version.specification
        node = next((node for node in specification["nodes"] if node["id"] == decision.attempt.node_id), None)
        dat = history_dats.get(decision.attempt.instance.object_id)
        if dat is not None:
            approval_history.append({
                "decision": decision,
                "dat": dat,
                "node_name": node.get("name", decision.attempt.node_id) if node else decision.attempt.node_id,
                "outcome_label": outcome_labels[decision.outcome],
            })
    return render(request, "workflows/my_approvals.html", {
        "tasks": tasks,
        "pending_tasks": pending_tasks,
        "page_obj": page,
        "approval_history": approval_history,
        "history_page": history_page,
        "current_module": apps.get_app_config("workflows"),
    })
