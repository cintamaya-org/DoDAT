# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from workflows.exceptions import WorkflowError
from workflows.services import migrate_workflow_instances


class Command(BaseCommand):
    help = "Migrate pinned workflow instances to active definition version. Dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("--restart-active", action="store_true", help="Restart active DATs with fresh graph approvals. Completed DATs remain archived.")
        parser.add_argument("workflow_code")
        parser.add_argument(
            "--map",
            action="append",
            default=[],
            metavar="OLD=NEW",
            help="Explicit state mapping; repeat for multiple states.",
        )
        parser.add_argument(
            "--object-id",
            action="append",
            default=[],
            help="Limit migration to one object ID; repeat for multiple objects.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist migration. Without this flag all changes are rolled back.",
        )

    def handle(self, *args, **options):
        mapping = {}
        for raw_mapping in options["map"]:
            source, separator, target = raw_mapping.partition("=")
            if not separator or not source.strip() or not target.strip():
                raise CommandError(f"Invalid state mapping '{raw_mapping}'; expected OLD=NEW")
            mapping[source.strip()] = target.strip()

        try:
            with transaction.atomic():
                result = migrate_workflow_instances(
                    workflow_code=options["workflow_code"],
                    state_mapping=mapping,
                    object_ids=options["object_id"],
                    **({"restart_active": True, "preview": not options["apply"]} if options["restart_active"] else {}),
                )
                if not options["apply"]:
                    transaction.set_rollback(True)
        except WorkflowError as exc:
            raise CommandError(str(exc)) from exc

        mode = "applied" if options["apply"] else "dry-run"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: examined={result.examined}, migrated={result.migrated}, "
                f"target=v{result.target_version}"
            )
        )
