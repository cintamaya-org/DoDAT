# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .events import workflow_transitioned
from .exceptions import (
    WorkflowConfigurationError,
    WorkflowPermissionDenied,
    WorkflowTransitionUnavailable,
)
from .models import Workflow, WorkflowInstance, WorkflowTransitionEvent
from .registry import get_workflow_adapter


@dataclass(frozen=True)
class TransitionResult:
    instance: WorkflowInstance
    changed: bool
    event: str
    from_state: str
    to_state: str
    audit_event: WorkflowTransitionEvent | None = None


@dataclass(frozen=True)
class WorkflowMigrationResult:
    workflow_code: str
    target_version: int
    examined: int
    migrated: int


def ensure_workflow_instance(obj: Any, *, workflow_code: str = "dat-validation") -> WorkflowInstance:
    """Return pinned instance, lazily importing legacy object's current state once."""

    cache = getattr(obj, "_workflow_instance_cache", None)
    if isinstance(cache, dict) and workflow_code in cache:
        return cache[workflow_code]

    content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    try:
        workflow = Workflow.objects.select_related("active_version").get(
            code=workflow_code,
            content_type=content_type,
            is_active=True,
        )
    except Workflow.DoesNotExist as exc:
        raise WorkflowConfigurationError(
            f"Active workflow '{workflow_code}' is not configured for '{obj._meta.label}'"
        ) from exc
    if workflow.active_version_id is None:
        raise WorkflowConfigurationError(f"Workflow '{workflow_code}' has no published version")

    adapter = get_workflow_adapter(obj)
    specification = workflow.active_version.specification
    if specification.get("schema_version") == 2:
        initial_state = obj.status if getattr(obj, "status", "") in {"valider", "refuse"} else "nouvelle_demande"
    else:
        initial_state = adapter.initial_state(obj, specification)
    state_codes = {state["status"] for state in specification.get("steps", [])}
    if specification.get("schema_version") != 2 and initial_state not in state_codes:
        raise WorkflowConfigurationError(
            f"Adapter returned unknown initial state '{initial_state}' for '{workflow_code}'"
        )

    instance, _created = WorkflowInstance.objects.get_or_create(
        workflow=workflow,
        content_type=content_type,
        object_id=str(obj.pk),
        defaults={
            "definition_version": workflow.active_version,
            "current_state": initial_state,
            "data": {"archived": True} if specification.get("schema_version") == 2 and initial_state in {"valider", "refuse"} else {},
        },
    )
    _cache_instance(obj, workflow_code, instance)
    return instance


def workflow_state(obj: Any, *, workflow_code: str = "dat-validation") -> str:
    return ensure_workflow_instance(obj, workflow_code=workflow_code).current_state


def bind_workflow_instances(
    objects: list[Any] | tuple[Any, ...],
    *,
    workflow_code: str = "dat-validation",
) -> dict[str, WorkflowInstance]:
    """Batch-load instances and pin missing objects without N+1 reads."""

    if not objects:
        return {}
    first = objects[0]
    content_type = ContentType.objects.get_for_model(first, for_concrete_model=False)
    try:
        workflow = Workflow.objects.select_related("active_version").get(
            code=workflow_code,
            content_type=content_type,
            is_active=True,
        )
    except Workflow.DoesNotExist as exc:
        raise WorkflowConfigurationError(
            f"Active workflow '{workflow_code}' is not configured for '{first._meta.label}'"
        ) from exc
    if workflow.active_version_id is None:
        raise WorkflowConfigurationError(f"Workflow '{workflow_code}' has no published version")

    object_by_id = {str(obj.pk): obj for obj in objects}
    instances = list(
        WorkflowInstance.objects.filter(
            workflow=workflow,
            content_type=content_type,
            object_id__in=object_by_id,
        ).select_related("definition_version")
    )
    found = {instance.object_id: instance for instance in instances}
    missing_ids = set(object_by_id) - set(found)
    if missing_ids:
        adapter = get_workflow_adapter(first)
        specification = workflow.active_version.specification
        WorkflowInstance.objects.bulk_create(
            [
                WorkflowInstance(
                    workflow=workflow,
                    definition_version=workflow.active_version,
                    content_type=content_type,
                    object_id=object_id,
                    current_state=("nouvelle_demande" if specification.get("schema_version") == 2 else adapter.initial_state(object_by_id[object_id], specification)),
                )
                for object_id in missing_ids
            ],
            ignore_conflicts=True,
        )
        instances = list(
            WorkflowInstance.objects.filter(
                workflow=workflow,
                content_type=content_type,
                object_id__in=object_by_id,
            ).select_related("definition_version")
        )
        found = {instance.object_id: instance for instance in instances}

    for object_id, instance in found.items():
        _cache_instance(object_by_id[object_id], workflow_code, instance)
    return found


def workflow_state_metadata(obj: Any, *, workflow_code: str = "dat-validation") -> dict:
    instance = ensure_workflow_instance(obj, workflow_code=workflow_code)
    if instance.definition_version.specification.get("schema_version") == 2:
        from .graph_schema import summary_metadata
        return summary_metadata(instance.current_state)
    metadata = _state_metadata(instance.definition_version.specification, instance.current_state)
    return {**metadata, "capabilities": tuple({"terminal"} | (set(metadata.get("capabilities", ())) & {"approved", "rejected"}))}


def workflow_state_label(obj: Any, *, workflow_code: str = "dat-validation") -> str:
    state = workflow_state_metadata(obj, workflow_code=workflow_code)
    return str(state.get("name") or state.get("status") or "")


def workflow_state_permission_roles(
    obj: Any,
    permission: str = "write",
    *,
    workflow_code: str = "dat-validation",
) -> tuple[str, ...]:
    instance = ensure_workflow_instance(obj, workflow_code=workflow_code)
    if instance.definition_version.specification.get("schema_version") == 2:
        from .graph_schema import approval_roles
        ready = set(instance.node_attempts.filter(is_current=True, status="ready").values_list("node_id", flat=True))
        return tuple(dict.fromkeys(role for node in instance.definition_version.specification["nodes"] if node["id"] in ready for role in approval_roles(node))) if permission == "write" else ()
    state = workflow_state_metadata(obj, workflow_code=workflow_code)
    return _permission_roles(state, permission)


def workflow_actor_has_any_state_permission(
    obj: Any,
    actor: Any,
    permission: str = "write",
    *,
    workflow_code: str = "dat-validation",
) -> bool:
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    instance = ensure_workflow_instance(obj, workflow_code=workflow_code)
    if instance.definition_version.specification.get("schema_version") == 2:
        from .graph_schema import referenced_roles
        return obj.participants.filter(user=actor, role__slug__in=referenced_roles(instance.definition_version.specification)).exists()
    role_slug = getattr(getattr(actor, "role", None), "slug", None)
    username = getattr(actor, "get_username", lambda: "")()
    for state in instance.definition_version.specification.get("steps", []):
        for binding in state.get("permissions", []):
            if binding.get("permission") != permission:
                continue
            if role_slug and role_slug in (binding.get("roles") or ()):
                return True
            if username and username in (binding.get("users") or ()):
                return True
    return False


def workflow_permission_roles_for_state(
    state: str,
    permission: str = "write",
    *,
    workflow_code: str = "dat-validation",
) -> tuple[str, ...]:
    workflow = Workflow.objects.select_related("active_version").get(
        code=workflow_code,
        is_active=True,
    )
    if workflow.active_version_id is None:
        raise WorkflowConfigurationError(f"Workflow '{workflow_code}' has no published version")
    if workflow.active_version.specification.get("schema_version") == 2:
        from .graph_schema import referenced_roles
        return tuple(referenced_roles(workflow.active_version.specification)) if permission == "write" else ()
    metadata = _state_metadata(workflow.active_version.specification, state)
    return _permission_roles(metadata, permission)


def workflow_has_capability(
    obj: Any,
    capability: str,
    *,
    workflow_code: str = "dat-validation",
) -> bool:
    state = workflow_state_metadata(obj, workflow_code=workflow_code)
    return capability in set(state.get("capabilities") or ())


def workflow_can(
    obj: Any,
    event: str,
    actor: Any = None,
    *,
    context: dict | None = None,
    workflow_code: str = "dat-validation",
) -> bool:
    ensure_workflow_instance(obj, workflow_code=workflow_code)
    # Event-only authorization was retired. Graph authorization requires a node
    # attempt and is enforced by act_on_node(). Legacy instances are read-only.
    return False


def available_workflow_actions(
    obj: Any,
    actor: Any = None,
    *,
    workflow_code: str = "dat-validation",
) -> tuple[str, ...]:
    instance = ensure_workflow_instance(obj, workflow_code=workflow_code)
    specification = instance.definition_version.specification
    events = {
        str(item.get("event"))
        for item in specification.get("transitions", [])
        if not item.get("automatic") and instance.current_state in (item.get("sources") or ())
    }
    return tuple(
        event
        for event in sorted(events)
        if workflow_can(obj, event, actor, workflow_code=workflow_code)
    )


def transition_workflow(
    obj: Any,
    event: str,
    actor: Any = None,
    *,
    context: dict | None = None,
    metadata: dict | None = None,
    workflow_code: str = "dat-validation",
    strict: bool = True,
) -> TransitionResult:
    """Apply one authorized, guarded transition atomically."""

    ensure_workflow_instance(obj, workflow_code=workflow_code)
    raise WorkflowTransitionUnavailable(
        "Les transitions historiques sont archivées. Utilisez une étape d'approbation."
    )


def workflow_states(*, workflow_code: str = "dat-validation") -> tuple[dict, ...]:
    workflow = Workflow.objects.select_related("active_version").get(
        code=workflow_code,
        is_active=True,
    )
    if workflow.active_version_id is None:
        raise WorkflowConfigurationError(f"Workflow '{workflow_code}' has no published version")
    if workflow.active_version.specification.get("schema_version") == 2:
        from .graph_schema import SUMMARY_STATES, summary_metadata
        return tuple({**summary_metadata(code), "key": code, "order": index, "is_initial": index == 0} for index, (code, *_rest) in enumerate(SUMMARY_STATES))
    steps = workflow.active_version.specification.get("steps", [])
    return tuple(sorted(steps, key=lambda step: (step.get("order", 0), step.get("key", ""))))


def workflow_initial_state(*, workflow_code: str = "dat-validation") -> str:
    for state in workflow_states(workflow_code=workflow_code):
        if state.get("is_initial"):
            return str(state["status"])
    raise WorkflowConfigurationError(f"Workflow '{workflow_code}' has no initial state")


def migrate_workflow_instances(
    *,
    workflow_code: str,
    state_mapping: dict[str, str] | None = None,
    object_ids: tuple[str, ...] | list[str] | None = None,
    actor: Any = None,
    restart_active: bool = False,
    preview: bool = False,
) -> WorkflowMigrationResult:
    """Explicitly move selected instances to active version with validated state mapping."""

    mapping = state_mapping or {}
    with transaction.atomic():
        workflow = (
            Workflow.objects.select_for_update()
            .get(code=workflow_code, is_active=True)
        )
        if workflow.active_version_id is None:
            raise WorkflowConfigurationError(f"Workflow '{workflow_code}' has no published version")
        target_specification = workflow.active_version.specification
        if target_specification.get("schema_version") == 2:
            if not restart_active or mapping:
                raise WorkflowConfigurationError("Approval graphs require --restart-active and do not accept state mappings. Old approvals are never carried forward.")
            if object_ids:
                from uuid import UUID
                try:
                    object_ids = [UUID(str(value)) for value in object_ids]
                except (ValueError, TypeError, AttributeError) as exc:
                    raise WorkflowConfigurationError("DAT object IDs must be UUIDs.") from exc
            return _restart_graph_instances(workflow, object_ids=object_ids, actor=actor, preview=preview)
        valid_states = {
            str(state.get("status")) for state in target_specification.get("steps", [])
        }
        unknown_targets = set(mapping.values()) - valid_states
        if unknown_targets:
            raise WorkflowConfigurationError(
                f"State mapping references unknown targets: {', '.join(sorted(unknown_targets))}"
            )

        queryset = WorkflowInstance.objects.select_for_update().filter(workflow=workflow)
        if object_ids:
            queryset = queryset.filter(object_id__in=[str(value) for value in object_ids])
        instances = list(queryset.select_related("definition_version"))
        model = workflow.content_type.model_class()
        if model is None:
            raise WorkflowConfigurationError(
                f"Workflow '{workflow_code}' content type has no model class"
            )
        objects = model._default_manager.in_bulk(
            [instance.object_id for instance in instances]
        )

        migrated = 0
        for instance in instances:
            if instance.definition_version_id == workflow.active_version_id:
                continue
            target_state = mapping.get(instance.current_state, instance.current_state)
            if target_state not in valid_states:
                raise WorkflowConfigurationError(
                    f"Instance '{instance.pk}' state '{instance.current_state}' needs explicit mapping"
                )
            content_object = objects.get(instance.object_id)
            if content_object is None:
                # UUID-keyed managers may normalize input keys.
                content_object = next(
                    (obj for key, obj in objects.items() if str(key) == instance.object_id),
                    None,
                )
            if content_object is None:
                raise WorkflowConfigurationError(
                    f"Instance '{instance.pk}' references missing object '{instance.object_id}'"
                )

            source_state = instance.current_state
            source_version = instance.definition_version.version
            instance.definition_version = workflow.active_version
            instance.current_state = target_state
            instance.save(update_fields=["definition_version", "current_state", "updated_at"])
            get_workflow_adapter(content_object).project_state(
                content_object,
                target_state,
                actor=actor,
            )
            audit_event = WorkflowTransitionEvent.objects.create(
                instance=instance,
                event="workflow-migrated",
                from_state=source_state,
                to_state=target_state,
                actor=actor if getattr(actor, "is_authenticated", False) else None,
                actor_display=_actor_display(actor),
                metadata={
                    "from_version": source_version,
                    "to_version": workflow.active_version.version,
                },
            )
            _cache_instance(content_object, workflow_code, instance)
            transaction.on_commit(
                lambda current_instance=instance, current_event=audit_event, current_object=content_object: (
                    workflow_transitioned.send(
                        sender=type(current_object),
                        instance=current_instance,
                        transition_event=current_event,
                        content_object=current_object,
                    )
                )
            )
            migrated += 1

        return WorkflowMigrationResult(
            workflow_code=workflow_code,
            target_version=workflow.active_version.version,
            examined=len(instances),
            migrated=migrated,
        )


def _restart_graph_instances(workflow, *, object_ids=None, actor=None, preview=False):
    """Explicit cutover, including previously unpinned active DATs. Finals stay archived."""
    from dat.models import DAT
    from .graph_engine import _execution, _reconcile, actor_name

    queryset = DAT.objects.exclude(status__in=["valider", "refuse"]).order_by("pk")
    if object_ids:
        queryset = queryset.filter(pk__in=object_ids)
    examined = migrated = 0
    content_type = ContentType.objects.get_for_model(DAT)
    with _execution():
        for dat in queryset.iterator(chunk_size=100):
            instance = WorkflowInstance.objects.select_for_update().filter(workflow=workflow, object_id=str(dat.pk)).first()
            if instance is not None and instance.current_state in {"valider", "refuse"}:
                continue
            examined += 1
            if instance is not None and instance.definition_version_id == workflow.active_version_id:
                continue
            if preview:
                migrated += 1
                continue
            previous = {"state": dat.status, "version": instance.definition_version.version if instance else None}
            if instance is None:
                instance, created = WorkflowInstance.objects.get_or_create(workflow=workflow, content_type=content_type, object_id=str(dat.pk), defaults={"definition_version": workflow.active_version, "current_state": "nouvelle_demande"})
                instance = WorkflowInstance.objects.select_for_update().get(pk=instance.pk)
                if not created and (instance.current_state in {"valider", "refuse"} or instance.definition_version_id == workflow.active_version_id):
                    continue
                if not created:
                    previous = {"state": instance.current_state, "version": instance.definition_version.version}
            instance.node_attempts.filter(is_current=True).update(is_current=False, status="superseded")
            instance.definition_version = workflow.active_version
            instance.current_state = "nouvelle_demande"
            instance.data = {}
            instance.save(update_fields=["definition_version", "current_state", "data", "updated_at"])
            WorkflowTransitionEvent.objects.create(instance=instance, event="workflow-restarted", from_state=previous["state"], to_state="nouvelle_demande", actor=actor, actor_display=actor_name(actor), metadata={"from_version": previous["version"], "to_version": workflow.active_version.version, "legacy_approvals_carried": False})
            _reconcile(instance, dat, actor=actor)
            migrated += 1
    return WorkflowMigrationResult(workflow.code, workflow.active_version.version, examined, migrated)


def _transition_candidates(specification: dict, state: str, event: str) -> list[dict]:
    candidates = [
        item
        for item in specification.get("transitions", [])
        if item.get("event") == event and state in (item.get("sources") or ())
    ]
    return sorted(candidates, key=lambda item: (item.get("order", 0), item.get("target", "")))


def _state_metadata(specification: dict, state: str) -> dict:
    for item in specification.get("steps", []):
        if item.get("status") == state:
            return item
    raise WorkflowConfigurationError(f"Workflow instance references unknown state '{state}'")


def _permission_roles(state_metadata: dict, permission: str) -> tuple[str, ...]:
    roles: list[str] = []
    for binding in state_metadata.get("permissions", []):
        if binding.get("permission") != permission:
            continue
        for role in binding.get("roles") or ():
            if role not in roles:
                roles.append(str(role))
    return tuple(roles)


def _cache_instance(obj: Any, workflow_code: str, instance: WorkflowInstance) -> None:
    cache = getattr(obj, "_workflow_instance_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(obj, "_workflow_instance_cache", cache)
    cache[workflow_code] = instance


def _actor_display(actor: Any) -> str:
    if actor is None:
        return ""
    full_name = getattr(actor, "get_full_name", lambda: "")()
    if full_name:
        return full_name
    return str(getattr(actor, "get_username", lambda: "")() or "")


def _json_payload(value: dict | None) -> dict:
    if not value:
        return {}
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
