# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from dat_viewflow.flows import DatViewflowFlow


class DatViewflowFlowTests(SimpleTestCase):
    def test_creation_links_existing_dat_to_flow_process(self):
        dat = object()
        activation = SimpleNamespace(prepare=mock.Mock(), done=mock.Mock(), process=SimpleNamespace(pk=31))

        with mock.patch("dat_viewflow.models.DatViewflowProcess.objects") as manager:
            DatViewflowFlow().create_process(activation, dat=dat)

        activation.prepare.assert_called_once_with()
        activation.done.assert_called_once_with()
        manager.get_or_create.assert_called_once_with(dat=dat, defaults={"process_id": 31})

    def test_creation_resolves_dat_id_and_skips_link_when_missing(self):
        dat = object()
        activation = SimpleNamespace(prepare=mock.Mock(), done=mock.Mock(), process=SimpleNamespace(pk=None))

        with (
            mock.patch("dat.models.DAT.objects") as dat_manager,
            mock.patch("dat_viewflow.models.DatViewflowProcess.objects") as process_manager,
        ):
            dat_manager.filter.return_value.first.return_value = dat
            DatViewflowFlow().create_process(activation, dat_id="dat-1")
            dat_manager.filter.assert_called_once_with(pk="dat-1")
            process_manager.get_or_create.assert_called_once_with(dat=dat, defaults={"process_id": None})

            process_manager.get_or_create.reset_mock()
            dat_manager.filter.return_value.first.return_value = None
            DatViewflowFlow().create_process(activation, dat_id="missing")

        process_manager.get_or_create.assert_not_called()
        self.assertEqual(activation.prepare.call_count, 2)
        self.assertEqual(activation.done.call_count, 2)
