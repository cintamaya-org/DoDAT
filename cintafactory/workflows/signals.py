# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging

from django.db.models.signals import m2m_changed, post_migrate
from django.dispatch import receiver
from diagrams.models import DrawIODiagram, LikeC4Diagram

from .sync import sync_workflow_definitions


def track_diagram_sections(sender, instance, action, **kwargs):
    """M2M managers run pre/post signals inside the same atomic transaction."""
    from .graph_engine import content_mutation, is_graph, _executing
    from .models import WorkflowInstance
    from .exceptions import WorkflowTransitionUnavailable

    dat_id = getattr(instance, "dat_id", None)
    if dat_id is None or _executing.get():
        return
    if action.startswith("pre_"):
        workflow = WorkflowInstance.objects.select_for_update().select_related("definition_version").filter(object_id=str(dat_id), workflow__code="dat-validation").first()
        if workflow and is_graph(workflow) and workflow.current_state in {"valider", "refuse"}:
            raise WorkflowTransitionUnavailable("Ce DAT est clôturé.")
    elif action.startswith("post_"):
        with content_mutation(dat_id):
            pass


m2m_changed.connect(track_diagram_sections, sender=DrawIODiagram.sections.through, dispatch_uid="workflow_drawio_sections")
m2m_changed.connect(track_diagram_sections, sender=LikeC4Diagram.sections.through, dispatch_uid="workflow_likec4_sections")

logger = logging.getLogger(__name__)


@receiver(post_migrate)
def sync_workflows_on_migrate(sender, **kwargs):
    """
    Keep declarative workflow definitions and DB state aligned.

    post_migrate is used to avoid hitting the database before migrations are
    applied. Errors are logged instead of raised in order to keep migrations
    resilient: an operator can fix the issue and re-run sync later.
    """

    # Avoid executing for unrelated apps.
    if getattr(sender, "label", None) != "workflows":
        return

    try:
        sync_workflow_definitions()
    except Exception:
        logger.exception("Unable to synchronise workflow definitions after migrations.")
