# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib import admin

from .models import DatViewflowProcess


@admin.register(DatViewflowProcess)
class DatViewflowProcessAdmin(admin.ModelAdmin):
    list_display = ("id", "dat", "process_id")
    list_select_related = ("dat__application",)
    search_fields = ("dat__reference", "dat__title", "process_id")
    readonly_fields = ("id",)
