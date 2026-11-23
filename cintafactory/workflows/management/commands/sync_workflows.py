# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from workflows.sync import sync_workflow_definitions
from workflows.graph_config import load_graph, publish_graph
from workflows.exceptions import WorkflowConfigurationError


class Command(BaseCommand):
    help = "Publish the workflow selected by the JSON library configuration."

    def add_arguments(self, parser):
        parser.add_argument("--json", dest="json_path", help="Publish a workflow graph from a JSON file.")
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate and preview synchronization, then roll back all database writes.",
        )

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                if options.get("json_path"):
                    publish_graph(load_graph(options["json_path"]))
                else:
                    sync_workflow_definitions()
                if options["check"]:
                    transaction.set_rollback(True)
        except WorkflowConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        if options["check"]:
            self.stdout.write(self.style.SUCCESS("Workflow definitions are valid; no changes saved."))
            return
        self.stdout.write(self.style.SUCCESS("Workflow definitions synchronised and published."))
