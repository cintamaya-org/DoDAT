# SPDX-License-Identifier: AGPL-3.0-only

import json

from django.core.management.base import BaseCommand

from diagrams.references import backfill_legacy_diagrams


class Command(BaseCommand):
    help = "Bind legacy diagrams only when current references identify one DAT."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report changes without writing them.")

    def handle(self, *args, **options):
        report = backfill_legacy_diagrams(dry_run=options["dry_run"])
        self.stdout.write(json.dumps({"dry_run": options["dry_run"], **report}, sort_keys=True))
