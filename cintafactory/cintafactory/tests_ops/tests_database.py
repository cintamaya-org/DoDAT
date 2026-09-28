# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-License-Identifier: AGPL-3.0-only

from unittest import mock

from django.core import management
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class WaitForDatabaseCommandTests(SimpleTestCase):
    @mock.patch("cintafactory.management.commands.wait_for_database.connection")
    @mock.patch("cintafactory.management.commands.wait_for_database.time.sleep")
    @mock.patch("cintafactory.management.commands.wait_for_database.time.monotonic")
    def test_wait_retries_after_transient_failure(
        self, monotonic, sleep, connection
    ):
        monotonic.side_effect = [0.0, 0.0, 0.0]
        connection.ensure_connection.side_effect = [RuntimeError("not ready"), None]

        management.call_command("wait_for_database", timeout=10, interval=1)

        self.assertEqual(connection.ensure_connection.call_count, 2)
        connection.close.assert_called_once_with()
        sleep.assert_called_once()

    @mock.patch("cintafactory.management.commands.wait_for_database.connection")
    @mock.patch(
        "cintafactory.management.commands.wait_for_database.time.monotonic",
        return_value=0.0,
    )
    def test_wait_times_out_without_logging_connection_details(self, _monotonic, connection):
        connection.ensure_connection.side_effect = RuntimeError(
            "password=must-not-appear host=postgres.example.test"
        )

        with self.assertRaises(CommandError) as error:
            management.call_command("wait_for_database", timeout=0)

        self.assertEqual(str(error.exception), "Database unavailable after 0 seconds.")
        self.assertNotIn("password", str(error.exception))
