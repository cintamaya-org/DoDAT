# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.apps import AppConfig
from django.conf import settings


class CintaFactoryConfig(AppConfig):
    name = "cintafactory"
    verbose_name = settings.APPLICATION_NAME

    def ready(self) -> None:
        from .admin_config import ensure_admin_config_exists
        from .notifications.config import ensure_external_notifications_config_exists
        from .rate_limit import ensure_limit_config_exists
        from . import security_checks  # noqa: F401
        from .theming import ensure_theme_structure_exists
        from .upload.upload_limit import ensure_upload_config_exists

        ensure_admin_config_exists()
        ensure_external_notifications_config_exists()
        ensure_limit_config_exists()
        ensure_theme_structure_exists()
        ensure_upload_config_exists()
