# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import Http404

from .access import require_diagram_write
from .models import DrawIODiagram, LikeC4Diagram


class DiagramAccessAdmin(admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is not None:
            try:
                require_diagram_write(obj, request.user)
            except (PermissionDenied, Http404):
                return False
        return True

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and self.has_change_permission(request, obj)

    def has_add_permission(self, request):
        # Sources are created through editor flows, which bind ownership and DATs.
        return False


@admin.register(DrawIODiagram)
class DiagramAdmin(DiagramAccessAdmin):
    list_display = ("id", "title", "owner", "dat", "updated_at")
    search_fields = ("title", "owner__username")
    readonly_fields = (
        "owner", "dat", "sections", "xml_file", "xml_content_type", "xml_size",
        "thumbnail", "thumbnail_content_type", "thumbnail_size", "png_paths", "updated_at",
    )


@admin.register(LikeC4Diagram)
class LikeC4DiagramAdmin(DiagramAccessAdmin):
    list_display = ("id", "storage_path", "title", "owner", "dat", "size", "updated_at")
    search_fields = ("storage_path", "title")
    readonly_fields = (
        "owner", "dat", "sections", "storage_path", "revision", "content_type", "size",
        "png_path", "png_paths", "png_content_type", "png_size", "png_updated_at", "updated_at",
    )
