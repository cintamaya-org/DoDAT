# SPDX-License-Identifier: AGPL-3.0-only
"""Transactional approval graph. All writes serialize on the workflow instance."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID, uuid4
import logging
from time import monotonic

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse

from .exceptions import WorkflowPermissionDenied, WorkflowTransitionUnavailable
from .graph_schema import (
    SECTION_REFERENT, TERMINAL_STATES, approval_roles, descendants, is_referent_gate, refusal_policy, topological_nodes,
)
from .graph_targets import TargetFacts, digest
from .models import (
    Workflow, WorkflowApprovalDecision, WorkflowContentSnapshot, WorkflowInstance,
    WorkflowNodeAttempt, WorkflowTransitionEvent,
)

_executing = ContextVar("workflow_graph_executing", default=False)
logger = logging.getLogger(__name__)
SATISFIED_STATUSES = {"completed", "skipped"}
REFUSAL_BLOCKED = "refusal_blocked"


class WorkflowConflict(WorkflowTransitionUnavailable):
    """The browser's attempt/content version no longer matches authoritative state."""


def is_graph(instance):
    return instance.definition_version.specification.get("schema_version") == 2


def actor_name(actor):
    return (actor.get_full_name() or actor.get_username()) if actor is not None else "Système"


def _event(instance, event, actor=None, *, source=None, target=None, **metadata):
    return WorkflowTransitionEvent.objects.create(
        instance=instance, event=event, from_state=source or instance.current_state,
        to_state=target or instance.current_state, actor=actor,
        actor_display=actor_name(actor), metadata=metadata,
    )


def _assignment(row):
    return {"assignment_id": str(row.pk), "user_id": str(row.user_id)}


def _active_participants(dat):
    return {
        row.role.slug: _assignment(row)
        for row in dat.participants.filter(user__is_active=True).select_related("role")
    }


def _active_referents(dat):
    return {
        row.section.slug: _assignment(row)
        for row in dat.section_responsibles.filter(
            section__dat=dat, user__is_active=True,
        ).select_related("section__metadata")
    }


def _assignments(node, participants, referents):
    if is_referent_gate(node):
        referent = referents.get(node["target"]["section"])
        return {SECTION_REFERENT: referent} if referent else {}
    return {role: participants[role] for role in approval_roles(node) if role in participants}


def _retry_role(node, participants, actor):
    return next((
        role for role in refusal_policy(node).get("retry_roles", ())
        if role in participants and participants[role]["user_id"] == str(getattr(actor, "pk", None))
    ), "")


def refusal_links(instance):
    return [
        {"from": source, "to": link["to"], "decision_id": link["decision_id"], "pending": link["pending"]}
        for source, link in instance.data.get("refusal_links", {}).items()
    ]


def _new_attempt(instance, node_id):
    previous = instance.node_attempts.filter(node_id=node_id).order_by("-number").first()
    return WorkflowNodeAttempt.objects.create(instance=instance, node_id=node_id, number=previous.number + 1 if previous else 1)


def _invalidate(instance, spec, attempts, seeds, *, reason, actor=None, retry_node=None):
    affected = {
        node_id for node_id in descendants(spec, seeds)
        if attempts[node_id].status != REFUSAL_BLOCKED or node_id == retry_node
    }
    for node_id in affected:
        current = attempts[node_id]
        current.is_current = False
        current.status = "superseded"
        current.save(update_fields=["is_current", "status", "updated_at"])
        attempts[node_id] = _new_attempt(instance, node_id)
    if affected:
        _event(instance, "nodes-invalidated", actor, nodes=sorted(affected), reason=reason)


def _notify_ready(instance, dat, node, attempt):
    if node["type"] != "approval":
        return
    user_ids = {assignment["user_id"] for assignment in attempt.assignments.values()}
    title = node.get("name", node["id"])

    def notify():
        from django.contrib.auth import get_user_model
        from .notifications import create_user_notification

        for user in get_user_model().objects.filter(pk__in=user_ids, is_active=True):
            create_user_notification(
                user, title="Approbation attendue", message=f"{dat.reference} : {title}", dat=dat,
                target_url=reverse("workflows:graph_detail", args=[dat.pk]),
                extra_data={"node_id": node["id"], "attempt_id": str(attempt.pk)},
            )

    transaction.on_commit(notify)


def _reconcile(instance, dat, *, actor=None):
    snapshot_deadline = monotonic() + 15
    spec = instance.definition_version.specification
    ordered = topological_nodes(spec)
    attempts = {attempt.node_id: attempt for attempt in instance.node_attempts.filter(is_current=True).select_related("snapshot")}
    for node in ordered:
        if node["id"] not in attempts:
            attempts[node["id"]] = _new_attempt(instance, node["id"])
    if instance.current_state in TERMINAL_STATES:
        return attempts, None
    facts = TargetFacts(dat)
    participants = _active_participants(facts.dat)
    require_referents = settings.DAT_WORKFLOW_REQUIRE_SECTION_REFERENTS
    referents = _active_referents(facts.dat) if require_referents and any(is_referent_gate(node) for node in ordered) else {}
    assignments = {node["id"]: _assignments(node, participants, referents) for node in ordered}
    bypassed = {node["id"] for node in ordered if is_referent_gate(node) and not require_referents}
    contents = {node["id"]: facts.resolve(node["target"]) for node in ordered if node["id"] not in bypassed}
    changed = set()
    for node in ordered:
        node_id = node["id"]
        attempt = attempts[node_id]
        if attempt.status == REFUSAL_BLOCKED:
            continue
        if node_id in bypassed:
            if attempt.snapshot_id is not None:
                changed.add(node_id)
            continue
        if attempt.status == "skipped":
            changed.add(node_id)
            continue
        if attempt.snapshot_id is None:
            continue
        if node["type"] != "milestone" and attempt.snapshot.digest != facts.fingerprint(node["target"]):
            changed.add(node_id)
        if attempt.assignments != assignments[node_id]:
            changed.add(node_id)
        if node.get("ready_when") and contents[node_id] is not None and not facts.condition(node["ready_when"], contents[node_id]):
            changed.add(node_id)
    if changed:
        _invalidate(instance, spec, attempts, changed, reason="content_or_assignment_changed", actor=actor)
    for node in ordered:
        attempt = attempts[node["id"]]
        if attempt.status in SATISFIED_STATUSES or attempt.status == REFUSAL_BLOCKED:
            continue
        blockers = []
        for dependency in node.get("after", ()):
            if attempts[dependency].status not in SATISFIED_STATUSES:
                blockers.append({"code": "dependency_pending", "node_id": dependency})
        if node["id"] in bypassed:
            attempt.blockers = blockers
            attempt.status = "waiting" if blockers else "skipped"
            attempt.save(update_fields=["blockers", "status", "updated_at"])
            continue
        content = contents[node["id"]]
        if content is None:
            blockers.append({"code": "target_missing"})
        roles = approval_roles(node)
        missing = set(roles) - set(assignments[node["id"]])
        if is_referent_gate(node) and not assignments[node["id"]]:
            blockers.append({"code": "missing_section_referent", "section": node["target"]["section"]})
        if ("any_role" in node.get("approvers", {}) and not assignments[node["id"]]) or ("all_roles" in node.get("approvers", {}) and missing):
            blockers.extend({"code": "missing_assignee", "role": role} for role in sorted(missing))
        if content is not None and node.get("ready_when") and not facts.condition(node["ready_when"], content):
            blockers.append({"code": "readiness_failed", "condition": node["ready_when"]})
        previous_status = attempt.status
        attempt.blockers = blockers
        attempt.status = "waiting" if blockers else "ready"
        if not blockers and attempt.snapshot_id is None:
            from .graph_artifacts import snapshot_files
            fingerprint = facts.fingerprint(node["target"])
            existing = WorkflowContentSnapshot.objects.filter(instance=instance, target=node["target"], digest=fingerprint).first()
            try:
                artifacts = snapshot_files(instance, content, deadline=snapshot_deadline) if existing is None else None
            except (OSError, ValueError) as exc:
                logger.warning("Workflow snapshot unavailable", extra={"dat_id": str(dat.pk), "node_id": node["id"], "error_type": type(exc).__name__})
                blockers.append({"code": "snapshot_unavailable"})
                attempt.status = "waiting"
            else:
                attempt.snapshot = existing or WorkflowContentSnapshot.objects.create(instance=instance, target=node["target"], digest=fingerprint, content={**content, "artifacts": artifacts})
                attempt.assignments = assignments[node["id"]]
                attempt.dependencies = {key: str(attempts[key].pk) for key in node.get("after", ())}
        if not blockers and node["type"] == "milestone":
            attempt.status = "completed"
            _event(instance, "milestone-completed", actor, node_id=node["id"], attempt_id=str(attempt.pk), snapshot_id=str(attempt.snapshot_id))
        attempt.save(update_fields=["blockers", "status", "snapshot", "assignments", "dependencies", "updated_at"])
        if attempt.status == "ready" and previous_status != "ready":
            _notify_ready(instance, facts.dat, node, attempt)
    _project_summary(instance, facts.dat, attempts, actor=actor)
    return attempts, facts


def _project_summary(instance, dat, attempts, *, actor=None):
    spec = instance.definition_version.specification
    correction = instance.data.get("correction_node")
    if correction and attempts[correction].status in SATISFIED_STATUSES:
        instance.data.pop("correction_node", None)
        correction = None
    links = instance.data.get("refusal_links", {}).values()
    for link in links:
        if attempts[link["to"]].status in SATISFIED_STATUSES:
            link["pending"] = False
    if all(attempts[key].status in SATISFIED_STATUSES for key in spec["complete_when"]["all"]):
        state = "valider"
    elif correction or any(link["pending"] for link in links) or any(attempt.status == REFUSAL_BLOCKED for attempt in attempts.values()):
        state = "reserve"
    elif instance.node_attempts.filter(is_current=True, decisions__isnull=False).exists():
        state = "en_cours"
    else:
        state = "nouvelle_demande"
    previous = instance.current_state
    instance.current_state = state
    instance.save(update_fields=["current_state", "data", "updated_at"])
    if dat.status != state:
        dat._history_actor = actor
        dat.status = state
        dat.save(update_fields=["status", "updated_at"])
    if previous != state:
        _event(instance, "summary-changed", actor, source=previous, target=state, previous=previous, current=state)


@contextmanager
def _execution():
    token = _executing.set(True)
    try:
        yield
    finally:
        _executing.reset(token)


def reconcile_graph(dat, *, instance=None, actor=None):
    if instance is None:
        from .services import ensure_workflow_instance
        instance = ensure_workflow_instance(dat)
    if not is_graph(instance):
        return {}
    with transaction.atomic(), _execution():
        locked = WorkflowInstance.objects.select_for_update(of=("self",)).get(pk=instance.pk)
        attempts, _facts = _reconcile(locked, dat, actor=actor)
        from .services import _cache_instance
        _cache_instance(dat, locked.workflow.code, locked)
        return attempts


@contextmanager
def content_mutation(dat_id, *, allow_terminal_metadata=False, require_referent_gate=False):
    """Serialize normal model writes with approval decisions, including non-UI writes."""
    if _executing.get() or not dat_id or (require_referent_gate and not settings.DAT_WORKFLOW_REQUIRE_SECTION_REFERENTS):
        yield
        return
    with transaction.atomic():
        # Metadata edits can reopen an approved DAT. Serialize the choice of version
        # with publication before locking the instance, in the same order as a switch.
        workflow = None
        if allow_terminal_metadata:
            workflow = Workflow.objects.select_for_update(of=("self",)).filter(
                code="dat-validation", instances__object_id=str(dat_id),
            ).first()
        instance = WorkflowInstance.objects.select_for_update(of=("self",)).filter(
            content_type__app_label="dat", content_type__model="dat", object_id=str(dat_id), workflow__code="dat-validation",
        ).first()
        if instance is None or (require_referent_gate and (
            instance.current_state in TERMINAL_STATES
            or not any(is_referent_gate(node) for node in instance.definition_version.specification.get("nodes", ()))
        )):
            yield
            return
        if not is_graph(instance):
            raise WorkflowTransitionUnavailable("Ce circuit historique est archivé. Redémarrez le DAT avec le nouveau circuit.")
        if instance.data.get("archived"):
            raise WorkflowTransitionUnavailable("Ce dossier historique est archivé.")
        terminal = instance.current_state in TERMINAL_STATES
        if terminal and not allow_terminal_metadata:
            raise WorkflowTransitionUnavailable("Ce DAT est clôturé.")
        with _execution():
            yield
            from dat.models import DAT
            dat = DAT.objects.filter(pk=dat_id).first()
            if dat is not None:
                if instance.current_state == "refuse":
                    return
                if terminal:
                    facts = TargetFacts(dat)
                    nodes = {node["id"]: node for node in instance.definition_version.specification["nodes"]}
                    participants = _active_participants(facts.dat)
                    referents = _active_referents(facts.dat) if any(is_referent_gate(node) for node in nodes.values()) else {}
                    changed = any(attempt.snapshot_id and nodes[attempt.node_id]["type"] != "milestone" and (attempt.snapshot.digest != digest(facts.resolve(nodes[attempt.node_id]["target"])) or attempt.assignments != _assignments(nodes[attempt.node_id], participants, referents)) for attempt in instance.node_attempts.filter(is_current=True).select_related("snapshot"))
                    if not changed:
                        return
                    if workflow and workflow.active_version_id != instance.definition_version_id:
                        from .services import restart_dat

                        restart_dat(workflow, dat, instance)
                        return
                    previous = instance.current_state
                    instance.current_state = "en_cours"
                    _event(instance, "terminal-content-revised", source=previous, target="en_cours", previous=previous)
                _reconcile(instance, dat)


def _reject(instance, dat, node, attempt, attempts, decision, actor):
    policy = refusal_policy(node)
    mode = policy["mode"]
    if mode == "return":
        restart = policy["restart_at"]
        _invalidate(instance, instance.definition_version.specification, attempts, {restart}, reason="refused_return", actor=actor)
        instance.data.setdefault("refusal_links", {})[node["id"]] = {
            "to": restart, "decision_id": str(decision.pk), "pending": True,
        }
        _event(
            instance, "refusal-returned", actor, node_id=node["id"], restart_at=restart,
            decision_id=str(decision.pk), restart_attempt_id=str(attempts[restart].pk),
        )
        return
    if mode == "block":
        attempt.status = REFUSAL_BLOCKED
        attempt.blockers = [{"code": "refusal_blocked"}]
        attempt.save(update_fields=["status", "blockers", "updated_at"])
        return

    attempt.status = "rejected"
    attempt.save(update_fields=["status", "updated_at"])
    previous = instance.current_state
    instance.current_state = "refuse"
    instance.save(update_fields=["current_state", "updated_at"])
    dat._history_actor = actor
    dat.status = "refuse"
    dat.save(update_fields=["status", "updated_at"])
    for other in attempts.values():
        if other.pk != attempt.pk and other.status not in SATISFIED_STATUSES:
            other.status = "cancelled"
            other.blockers = [{"code": "workflow_rejected"}]
            other.save(update_fields=["status", "blockers", "updated_at"])
    _event(instance, "summary-changed", actor, source=previous, target="refuse", previous=previous, current="refuse")


def act_on_node(dat, node_id, actor, *, outcome, attempt_id, request_id, comment=""):
    """A browser must echo attempt ID. Retries are idempotent per request UUID."""
    from .services import ensure_workflow_instance

    if not getattr(actor, "is_authenticated", False):
        raise WorkflowPermissionDenied("Authentification requise.")
    if not isinstance(outcome, str) or outcome not in {"submit", "approve", "request_changes", "reject", "retry"}:
        raise WorkflowTransitionUnavailable("Décision inconnue.")
    try:
        attempt_id, request_id = UUID(str(attempt_id)), UUID(str(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WorkflowConflict("Identifiant de tentative ou de requête invalide.") from exc
    if not isinstance(comment, str) or len(comment) > 10000:
        raise WorkflowTransitionUnavailable("Commentaire invalide (10000 caractères maximum).")
    instance = ensure_workflow_instance(dat)
    conflict = None
    decision = None
    with transaction.atomic(), _execution():
        instance = WorkflowInstance.objects.select_for_update(of=("self",)).get(pk=instance.pk)
        if not is_graph(instance):
            raise WorkflowTransitionUnavailable("Workflow historique archivé.")
        spec = instance.definition_version.specification
        node = next((node for node in spec["nodes"] if node["id"] == node_id), None)
        if node is None:
            raise WorkflowTransitionUnavailable("Étape inconnue.")
        from dat.permissions import filter_dat_queryset_for_user
        from dat.models import DAT
        if not filter_dat_queryset_for_user(DAT.objects.filter(pk=dat.pk), actor).exists():
            raise WorkflowPermissionDenied("DAT inaccessible.")
        existing = WorkflowApprovalDecision.objects.filter(request_id=request_id).select_related("attempt").first()
        if existing:
            if existing.attempt.instance_id == instance.pk and existing.attempt.node_id == node_id and existing.attempt_id == attempt_id and existing.actor_id == actor.pk and existing.outcome == outcome and existing.comment == comment.strip():
                return existing
            raise WorkflowConflict("Identifiant de requête déjà utilisé.")
        if instance.current_state in TERMINAL_STATES:
            raise WorkflowTransitionUnavailable("Ce DAT est clôturé.")
        attempts, facts = _reconcile(instance, dat, actor=actor)
        attempt = attempts[node_id]
        if attempt.pk != attempt_id:
            conflict = WorkflowConflict("Le contenu ou les affectations ont changé. Actualisez la revue.")
        elif attempt.status != (REFUSAL_BLOCKED if outcome == "retry" else "ready"):
            conflict = WorkflowConflict("Cette étape n'est plus disponible.")
        else:
            role = ""
            assignment = {}
            if outcome == "retry":
                participants = _active_participants(facts.dat)
                role = _retry_role(node, participants, actor)
                if not role:
                    raise WorkflowPermissionDenied("Votre rôle sur ce DAT n’autorise pas la reprise de cette étape.")
                assignment = participants[role]
            elif node["type"] == "submission":
                if outcome != "submit" or not facts.can_submit(node["target"], actor):
                    raise WorkflowPermissionDenied("Seul un éditeur de ce contenu peut le soumettre.")
            elif node["type"] == "approval":
                if outcome == "submit":
                    raise WorkflowTransitionUnavailable("Cette étape attend une approbation.")
                role = next((role for role, assigned in attempt.assignments.items() if assigned["user_id"] == str(actor.pk)), "")
                if not role:
                    raise WorkflowPermissionDenied("Votre rôle sur ce DAT n'autorise pas cette approbation.")
                assignment = attempt.assignments[role]
            else:
                raise WorkflowTransitionUnavailable("Un jalon est automatique.")
            if outcome in {"request_changes", "reject"} and not comment.strip():
                raise WorkflowTransitionUnavailable("Un commentaire est obligatoire.")
            # Retry is an administrative action on the refused attempt. The role
            # may already have an approval decision on that same attempt.
            decision_role = "" if outcome == "retry" else role
            if attempt.decisions.filter(role_slug=decision_role).exists():
                raise WorkflowConflict("Votre décision est déjà enregistrée.")
            try:
                with transaction.atomic():
                    decision = WorkflowApprovalDecision.objects.create(
                        attempt=attempt, snapshot=attempt.snapshot, actor=actor, actor_display=actor_name(actor),
                        role_slug=decision_role, assignment_id=assignment.get("assignment_id"), outcome=outcome, comment=comment.strip(), request_id=request_id,
                    )
            except IntegrityError as exc:
                if WorkflowApprovalDecision.objects.filter(request_id=request_id).exists():
                    raise WorkflowConflict("Identifiant de requête déjà utilisé.") from exc
                raise
            _event(instance, "node-decision", actor, node_id=node_id, attempt_id=str(attempt.pk), decision_id=str(decision.pk), outcome=outcome, role=role)
            if outcome == "retry":
                _invalidate(instance, spec, attempts, {node_id}, reason="refusal_retried", actor=actor, retry_node=node_id)
            elif outcome == "request_changes":
                restart = node.get("on_request_changes", {}).get("restart_at", node_id)
                _invalidate(instance, spec, attempts, {restart}, reason="changes_requested", actor=actor)
                instance.data["correction_node"] = restart
            elif outcome == "reject":
                _reject(instance, facts.dat, node, attempt, attempts, decision, actor)
            elif outcome == "submit" or "any_role" in node.get("approvers", {}) or set(attempt.assignments) <= set(attempt.decisions.filter(outcome="approve").values_list("role_slug", flat=True)):
                attempt.status = "completed"
                attempt.save(update_fields=["status", "updated_at"])
            _reconcile(instance, dat, actor=actor)
        from .services import _cache_instance
        _cache_instance(dat, instance.workflow.code, instance)
    if conflict:
        raise conflict
    return decision


def attempt_display_tone(attempt):
    if attempt.status == REFUSAL_BLOCKED or (
        attempt.status == "waiting" and any(blocker["code"] != "dependency_pending" for blocker in attempt.blockers)
    ):
        return "blocked"
    return attempt.status


def graph_context(dat, actor):
    from .services import ensure_workflow_instance

    instance = ensure_workflow_instance(dat)
    if not is_graph(instance) or instance.data.get("archived"):
        return None
    attempts = reconcile_graph(dat, instance=instance)
    instance = ensure_workflow_instance(dat)
    facts = TargetFacts(dat)
    from users.models import Role
    role_names = dict(Role.objects.values_list("slug", "name"))
    node_names = {node["id"]: node.get("name", node["id"]) for node in instance.definition_version.specification["nodes"]}
    links = refusal_links(instance)
    links_by_source = {link["from"]: link for link in links}
    active = instance.current_state not in TERMINAL_STATES
    participants = _active_participants(facts.dat) if active and any(attempt.status == REFUSAL_BLOCKED for attempt in attempts.values()) else {}
    require_referents = settings.DAT_WORKFLOW_REQUIRE_SECTION_REFERENTS
    referents = _active_referents(facts.dat) if require_referents and any(is_referent_gate(node) for node in instance.definition_version.specification["nodes"]) else {}
    decisions = {}
    for decision in WorkflowApprovalDecision.objects.filter(attempt_id__in=[attempt.pk for attempt in attempts.values()]).order_by("created_at"):
        decisions.setdefault(decision.attempt_id, []).append(decision)
    nodes = []
    for node in topological_nodes(instance.definition_version.specification):
        attempt = attempts[node["id"]]
        recorded = decisions.get(attempt.pk, [])
        section_referent = is_referent_gate(node)
        for decision in recorded:
            decision.role_name = (
                "Référent de section" if section_referent and decision.role_slug == SECTION_REFERENT
                else role_names.get(decision.role_slug, decision.role_slug)
            )
        role = next((role for role, assignment in attempt.assignments.items() if assignment["user_id"] == str(getattr(actor, "pk", None))), None)
        can_act = attempt.status == "ready" and active
        can_submit = can_act and node["type"] == "submission" and facts.can_submit(node["target"], actor)
        can_approve = can_act and role is not None and not any(decision.role_slug == role for decision in recorded)
        roles = approval_roles(node)
        expected_roles = (SECTION_REFERENT,) if section_referent and require_referents else roles
        remaining_roles = [
            required for required in expected_roles
            if not any(decision.role_slug == required and decision.outcome == "approve" for decision in recorded)
        ] if attempt.status in {"ready", "waiting"} else []
        reject = refusal_policy(node)
        link = links_by_source.get(node["id"])
        referent = referents.get(node["target"].get("section")) if section_referent else None
        nodes.append({
            "display_tone": attempt_display_tone(attempt),
            **node, "name": node.get("name", node["id"]), "attempt": attempt, "decisions": recorded,
            "roles": roles, "role_names": [role_names.get(role, role) for role in roles],
            "remaining_roles": remaining_roles,
            "remaining_role_names": ["Référent de section"] if section_referent and remaining_roles else [role_names.get(role, role) for role in remaining_roles],
            "dependency_names": [node_names[key] for key in node.get("after", [])],
            "correction_name": node_names[node.get("on_request_changes", {}).get("restart_at", node["id"])],
            "refusal_policy": reject, "refusal_destination": node_names.get(reject.get("restart_at")),
            "refusal_link": link, "retry_role_names": [role_names.get(role, role) for role in reject.get("retry_roles", ())],
            "section_referent": section_referent,
            "is_section_referent": bool(referent and referent["user_id"] == str(getattr(actor, "pk", None))),
            "display_blockers": [{**blocker, "node_name": node_names.get(blocker.get("node_id"), ""), "role_name": role_names.get(blocker.get("role"), "")} for blocker in attempt.blockers],
            "can_submit": can_submit, "can_approve": can_approve,
            "can_retry": active and attempt.status == REFUSAL_BLOCKED and bool(_retry_role(node, participants, actor)),
            "request_id": str(uuid4()), "target_label": facts.describe(node["target"]),
        })
    return {
        "instance": instance, "nodes": nodes, "refusal_links": links,
        "require_section_referents": require_referents,
        "has_actions": any(node["can_submit"] or node["can_approve"] or node["can_retry"] for node in nodes),
    }
