# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.apps import AppConfig
from material.frontend.apps import ModuleMixin

class DatConfig(ModuleMixin, AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dat"  # or "cintafactory.dat" if inside project package
    verbose_name = "Gestion des DAT"

    icon = "<i class='material-icons'>topic</i>"
    order = 30
    base_template = "material/frontend/base_module.html"

    def ready(self):
        super().ready()
        from .config import ensure_section_blueprints_file_exists

        ensure_section_blueprints_file_exists()
        # Import signals to register DAT lifecycle logging hooks.
        from . import signals  # noqa: F401
        # Register DAT boundary with generic workflow subsystem.
        from . import workflow  # noqa: F401
