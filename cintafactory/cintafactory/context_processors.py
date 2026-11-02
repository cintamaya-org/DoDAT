# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.conf import settings


def application_branding(_request):
    return {
        "application_name": settings.APPLICATION_NAME,
    }


def frontend_dev_logger(_request):
    return {
        "frontend_dev_logger_enabled": bool(settings.DEBUG),
    }
