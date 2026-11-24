# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from workflows.sync import sync_selected_workflow
from workflows.exceptions import WorkflowError


class Command(BaseCommand):
    help = "Publish the selected workflow and restart open DATs using another version."

    def add_arguments(self, parser):
        parser.add_argument("--json", dest="json_path", help="Publish a workflow graph from a JSON file.")
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate and preview synchronization, then roll back all database writes.",
        )

    def handle(self, *args, **options):
        try:
            result = sync_selected_workflow(options["json_path"], preview=options["check"])
        except WorkflowError as exc:
            raise CommandError(str(exc)) from exc
        if options["check"]:
            message = f"Workflow v{result.target_version} is valid. Open DATs to restart: {result.migrated}. No changes saved."
        else:
            message = f"Published workflow v{result.target_version}. Open DATs restarted: {result.migrated}."
        self.stdout.write(self.style.SUCCESS(message))
