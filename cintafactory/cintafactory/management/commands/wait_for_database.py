# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Wait until configured PostgreSQL database accepts authenticated connections."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=float,
            default=120.0,
            help="Maximum wait time in seconds.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=1.0,
            help="Delay between connection attempts in seconds.",
        )

    def handle(self, *args, **options):
        timeout = max(0.0, float(options["timeout"]))
        interval = max(0.01, float(options["interval"]))
        deadline = time.monotonic() + timeout

        while True:
            try:
                connection.ensure_connection()
                self.stdout.write(self.style.SUCCESS("Database connection ready."))
                return
            except Exception as exc:
                connection.close()
                if time.monotonic() >= deadline:
                    raise CommandError(
                        f"Database unavailable after {timeout:g} seconds."
                    ) from exc
