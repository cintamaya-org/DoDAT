# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase


class BackfillDiagramAccessCommandTests(SimpleTestCase):
    @mock.patch(
        "diagrams.management.commands.backfill_diagram_access.backfill_legacy_diagrams",
        return_value={"bound": 2, "ambiguous": 1},
    )
    def test_dry_run_reports_result_and_does_not_apply_backfill(self, backfill):
        output = StringIO()
        call_command("backfill_diagram_access", "--dry-run", stdout=output)

        backfill.assert_called_once_with(dry_run=True)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"dry_run": True, "bound": 2, "ambiguous": 1},
        )

    @mock.patch(
        "diagrams.management.commands.backfill_diagram_access.backfill_legacy_diagrams",
        return_value={"bound": 1},
    )
    def test_default_run_applies_backfill(self, backfill):
        output = StringIO()
        call_command("backfill_diagram_access", stdout=output)

        backfill.assert_called_once_with(dry_run=False)
        self.assertEqual(json.loads(output.getvalue()), {"dry_run": False, "bound": 1})
