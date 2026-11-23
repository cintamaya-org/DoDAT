# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit publication of validated JSON. File edits never mutate pinned instances."""

import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Max

from .exceptions import WorkflowConfigurationError
from .graph_schema import SUMMARY_STATES, referenced_roles, validate_graph
from .graph_targets import digest
from .models import Workflow, WorkflowDefinitionVersion, WorkflowStep


LIBRARY_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json\Z")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path, description):
    try:
        with Path(path).open("rb") as stream:
            text = stream.read(1024 * 1024 + 1)
        if len(text) > 1024 * 1024:
            raise ValueError("JSON exceeds 1 MiB.")
        return json.loads(text.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError, RecursionError) as exc:
        raise WorkflowConfigurationError(f"Cannot load {description}: {exc}") from exc


def _selected_graph_path():
    configured = getattr(settings, "DAT_WORKFLOW_CONFIG", "")
    if configured:
        config_path = Path(configured)
    else:
        config_path = Path(settings.BASE_DIR) / "conf" / "workflows.json"
        if not config_path.exists() and not config_path.is_symlink():
            config_path = Path(__file__).with_name("config") / "workflows.json"
    config = _load_json(config_path, "workflow library configuration")
    if not isinstance(config, dict) or set(config) != {"active_workflow"}:
        raise WorkflowConfigurationError("Workflow library configuration requires only active_workflow.")
    filename = config["active_workflow"]
    if not isinstance(filename, str) or not LIBRARY_FILENAME.fullmatch(filename):
        raise WorkflowConfigurationError("active_workflow must be a JSON filename in the library directory.")
    try:
        directory = config_path.resolve().parent
        selected = (directory / filename).resolve()
        if selected.parent != directory:
            raise ValueError("Selected workflow must remain inside the library directory.")
    except (OSError, ValueError, RuntimeError) as exc:
        raise WorkflowConfigurationError(f"Cannot select workflow JSON: {exc}") from exc
    return selected


def load_graph(path=None):
    """Load the selected library entry, unless an explicit/legacy file overrides it."""
    if path is None:
        path = getattr(settings, "DAT_WORKFLOW_JSON", "") or _selected_graph_path()
    spec = _load_json(path, "workflow JSON")
    validate_graph(spec)
    return spec


def blueprint_targets():
    from dat.config.section_blueprints import SECTION_BLUEPRINTS

    result = set()
    for section in SECTION_BLUEPRINTS:
        if section["slug"] == "validation":
            continue
        result.add((section["slug"], None, None))
        for sub in section.get("parts", ()):
            result.add((section["slug"], sub["slug"], None))
            for part in sub.get("entries", ()):
                result.add((section["slug"], sub["slug"], part["key"]))
    return result


def participant_role_slugs(dat=None):
    """Roles for assignment forms come from the pinned contract, not approval state."""
    from .models import WorkflowInstance
    version = None
    if dat is not None and dat.pk:
        instance = WorkflowInstance.objects.filter(workflow__code="dat-validation", object_id=str(dat.pk)).select_related("definition_version").first()
        if instance:
            version = instance.definition_version
    if version is None:
        workflow = Workflow.objects.filter(code="dat-validation", is_active=True).select_related("active_version").first()
        version = workflow.active_version if workflow else None
    if version is None or version.specification.get("schema_version") != 2:
        return None
    roles = referenced_roles(version.specification)
    if dat is not None and dat.pk:
        roles = sorted(set(roles) | set(dat.participants.values_list("role__slug", flat=True)))
    return roles


@transaction.atomic
def publish_graph(spec):
    from dat.config.section_blueprints import SECTION_BLUEPRINTS
    from dat.models import DAT
    from users.models import Role

    validate_graph(spec)
    if spec["code"] != "dat-validation":
        raise WorkflowConfigurationError("The DAT application's workflow code is dat-validation. Customize its nodes and name under this code.")
    validate_graph(
        spec,
        role_slugs=Role.objects.filter(slug__in=referenced_roles(spec)).values_list("slug", flat=True),
        targets=blueprint_targets(),
        referent_sections={item["slug"] for item in SECTION_BLUEPRINTS if item.get("allowed_roles")},
    )
    content_type = ContentType.objects.get_for_model(DAT)
    workflow, _ = Workflow.objects.get_or_create(code=spec["code"], defaults={"name": spec.get("name", spec["code"]), "content_type": content_type})
    workflow = Workflow.objects.select_for_update().get(pk=workflow.pk)
    if workflow.content_type_id != content_type.pk:
        raise WorkflowConfigurationError("A workflow code cannot change its target model.")
    version = WorkflowDefinitionVersion.objects.filter(workflow=workflow, checksum=digest(spec)).first()
    if version is None:
        number = (workflow.versions.aggregate(number=Max("version"))["number"] or 0) + 1
        version = WorkflowDefinitionVersion.objects.create(workflow=workflow, version=number, checksum=digest(spec), specification=spec)
    workflow.name = spec.get("name", spec["code"])
    workflow.active_version = version
    workflow.is_active = True
    workflow.save(update_fields=["name", "active_version", "is_active", "updated_at"])
    # Board columns are summaries, never approval authority or edit permissions.
    WorkflowStep.objects.filter(workflow=workflow).delete()
    for index, (state, name, _lane, _capabilities) in enumerate(SUMMARY_STATES):
        WorkflowStep.objects.update_or_create(workflow=workflow, key=state, defaults={"state": state, "name": name, "order": index, "is_initial": index == 0})
    return version
