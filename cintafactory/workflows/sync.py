# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Sequence, Tuple

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from django.db.models import Max

from .definitions import (
    WORKFLOW_DEFINITIONS,
    StepPermissionDefinition,
    WorkflowDefinition,
    WorkflowStepDefinition,
    definition_to_spec,
)
from .exceptions import WorkflowConfigurationError
from .registry import get_workflow_adapter
from .validation import validate_workflow_definition

def _iter_expected_permissions(
    permission_def: StepPermissionDefinition,
    role_ids: Sequence[int],
    user_ids: Sequence[int],
) -> Iterable[Tuple[str, int | None, int | None]]:
    """Build normalized tuples representing future permissions."""
    for role_id in role_ids:
        yield (permission_def.permission, role_id, None)
    for user_id in user_ids:
        yield (permission_def.permission, None, user_id)


@transaction.atomic
def sync_selected_workflow(path=None, *, preview=False):
    """Publish one graph and move its open dossiers in the same transaction."""
    from .graph_config import load_graph, publish_graph
    from .services import migrate_workflow_instances

    version = publish_graph(load_graph(path))
    result = migrate_workflow_instances(
        workflow_code=version.workflow.code, restart_active=True, preview=preview,
    )
    if preview:
        transaction.set_rollback(True)
    return result


def sync_workflow_definitions(definitions: Sequence[WorkflowDefinition] | None = None) -> None:
    """Synchronise declarative workflow definitions with the database."""

    if definitions is None:
        sync_selected_workflow()
        return

    Workflow = apps.get_model("workflows", "Workflow")
    WorkflowStep = apps.get_model("workflows", "WorkflowStep")
    WorkflowStepPermission = apps.get_model("workflows", "WorkflowStepPermission")
    WorkflowDefinitionVersion = apps.get_model("workflows", "WorkflowDefinitionVersion")
    WorkflowInstance = apps.get_model("workflows", "WorkflowInstance")
    Role = apps.get_model("users", "Role")
    User = apps.get_model("users", "User")

    with transaction.atomic():
        for workflow_def in definitions:
            validate_workflow_definition(workflow_def)
            model = apps.get_model(workflow_def.model)
            content_type = ContentType.objects.get_for_model(model)
            existing_workflow = (
                Workflow.objects.select_related("content_type")
                .filter(code=workflow_def.code)
                .first()
            )
            if (
                existing_workflow is not None
                and existing_workflow.content_type_id != content_type.pk
            ):
                raise WorkflowConfigurationError(
                    f"Workflow '{workflow_def.code}' is already bound to "
                    f"'{existing_workflow.content_type.app_label}."
                    f"{existing_workflow.content_type.model}' and cannot be rebound to "
                    f"'{workflow_def.model}'"
                )
            adapter = get_workflow_adapter(workflow_def.model)
            supported_guards = set(getattr(adapter, "guards", ())) | {"", "always"}
            supported_actions = set(getattr(adapter, "actions", ())) | {""}
            unknown_guards = {
                transition.guard
                for transition in workflow_def.transitions
                if transition.guard not in supported_guards
            }
            unknown_actions = {
                transition.action
                for transition in workflow_def.transitions
                if transition.action not in supported_actions
            }
            if unknown_guards or unknown_actions:
                details = []
                if unknown_guards:
                    details.append(f"guards={', '.join(sorted(unknown_guards))}")
                if unknown_actions:
                    details.append(f"actions={', '.join(sorted(unknown_actions))}")
                raise WorkflowConfigurationError(
                    f"Workflow '{workflow_def.code}' references unsupported adapter handlers: "
                    + "; ".join(details)
                )
            referenced_roles = {
                role
                for step in workflow_def.steps
                for permission in step.permissions
                for role in permission.roles
            } | {
                role
                for transition_def in workflow_def.transitions
                for role in transition_def.roles
            }
            available_roles = set(
                Role.objects.filter(slug__in=referenced_roles).values_list("slug", flat=True)
            )
            missing_referenced_roles = referenced_roles - available_roles
            if missing_referenced_roles:
                missing = ", ".join(sorted(missing_referenced_roles))
                raise WorkflowConfigurationError(
                    f"Workflow '{workflow_def.code}' references missing roles: {missing}"
                )

            workflow, _ = Workflow.objects.update_or_create(
                code=workflow_def.code,
                defaults={
                    "name": workflow_def.name,
                    "description": workflow_def.description,
                    "content_type": content_type,
                    "is_active": True,
                },
            )

            specification = definition_to_spec(workflow_def)
            serialized = json.dumps(
                specification,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            version = WorkflowDefinitionVersion.objects.filter(
                workflow=workflow,
                checksum=checksum,
            ).first()
            if version is None:
                latest = (
                    WorkflowDefinitionVersion.objects.filter(workflow=workflow)
                    .aggregate(value=Max("version"))
                    .get("value")
                    or 0
                )
                version = WorkflowDefinitionVersion.objects.create(
                    workflow=workflow,
                    version=latest + 1,
                    checksum=checksum,
                    specification=specification,
                )
            if workflow.active_version_id != version.pk:
                workflow.active_version = version
                workflow.save(update_fields=["active_version", "updated_at"])

            # Pin every existing object once. Later publishes never rewrite running instances.
            state_codes = {step.status for step in workflow_def.steps}
            initial_state = next(step.status for step in workflow_def.steps if step.is_initial)
            existing_object_ids = set(
                WorkflowInstance.objects.filter(
                    workflow=workflow,
                    content_type=content_type,
                ).values_list("object_id", flat=True)
            )
            model_fields = {field.name for field in model._meta.fields}
            value_fields = ["pk"] + (["status"] if "status" in model_fields else [])
            pending_instances = []
            for row in model._default_manager.values(*value_fields).iterator(chunk_size=500):
                object_id = str(row["pk"])
                if object_id in existing_object_ids:
                    continue
                projected_state = row.get("status") or initial_state
                current_state = projected_state if projected_state in state_codes else initial_state
                pending_instances.append(
                    WorkflowInstance(
                        workflow=workflow,
                        definition_version=version,
                        content_type=content_type,
                        object_id=object_id,
                        current_state=current_state,
                    )
                )
                if len(pending_instances) >= 500:
                    WorkflowInstance.objects.bulk_create(pending_instances, ignore_conflicts=True)
                    pending_instances = []
            if pending_instances:
                WorkflowInstance.objects.bulk_create(pending_instances, ignore_conflicts=True)

            existing_steps = {
                step.key: step
                for step in workflow.steps.all().prefetch_related("permissions")
            }
            desired_states = {step.key: step.status for step in workflow_def.steps}
            for key in set(existing_steps) - set(desired_states):
                existing_steps.pop(key).delete()

            # Free unique state values before key renames or state swaps are applied.
            for key, step in existing_steps.items():
                if step.state == desired_states[key]:
                    continue
                step.state = f"__sync__{step.pk.hex}"
                step.save(update_fields=["state"])
            ordered_step_defs = sorted(
                workflow_def.steps,
                key=lambda step: (step.order, step.key),
            )

            for step_def in ordered_step_defs:
                step, _ = WorkflowStep.objects.update_or_create(
                    workflow=workflow,
                    key=step_def.key,
                    defaults={
                        "name": step_def.name,
                        "description": step_def.description,
                        "order": step_def.order,
                        "state": step_def.status,
                        "is_initial": step_def.is_initial,
                    },
                )
                expected_permissions: set[Tuple[str, int | None, int | None]] = set()
                for permission_def in step_def.permissions:
                    role_slugs = set(permission_def.roles or ())
                    user_names = set(permission_def.users or ())

                    role_map = {
                        role.slug: role.id
                        for role in Role.objects.filter(slug__in=role_slugs)
                    }
                    user_map = {
                        user.username: user.id
                        for user in User.objects.filter(username__in=user_names)
                    }
                    missing_users = user_names - set(user_map.keys())
                    if missing_users:
                        missing = ", ".join(sorted(missing_users))
                        raise WorkflowConfigurationError(
                            f"Workflow '{workflow_def.code}' references missing users: {missing}"
                        )

                    expected_permissions.update(
                        _iter_expected_permissions(
                            permission_def,
                            role_ids=role_map.values(),
                            user_ids=user_map.values(),
                        )
                    )

                existing_permissions = {
                    (permission.permission_type, permission.role_id, permission.user_id): permission
                    for permission in step.permissions.all()
                }

                # Remove stale permissions
                for key, permission in existing_permissions.items():
                    if key not in expected_permissions:
                        permission.delete()

                # Add missing permissions
                for permission in expected_permissions:
                    if permission not in existing_permissions:
                        WorkflowStepPermission.objects.create(
                            step=step,
                            permission_type=permission[0],
                            role_id=permission[1],
                            user_id=permission[2],
                        )
