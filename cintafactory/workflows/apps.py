# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.apps import AppConfig

from material.frontend.apps import ModuleMixin


class WorkflowsConfig(ModuleMixin, AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workflows"
    verbose_name = "Flux de travail"

    icon = "<i class='material-icons'>fact_check</i>"
    order = 40
    base_template = "material/frontend/base_module.html"

    def ready(self):
        super().ready()
        # Import signal handlers so post_migrate is connected.
        from . import signals  # noqa: F401
