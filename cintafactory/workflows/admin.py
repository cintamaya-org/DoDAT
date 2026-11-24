# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib import admin

from .models import (
    Workflow,
    WorkflowDefinitionVersion,
    WorkflowInstance,
    WorkflowStep,
    WorkflowStepPermission,
    WorkflowTransitionEvent,
    WorkflowNodeAttempt,
    WorkflowApprovalDecision,
    WorkflowContentSnapshot,
)


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "content_type", "active_version", "is_active", "updated_at")
    list_filter = ("is_active", "content_type")
    search_fields = ("code", "name")
    readonly_fields = ("code", "content_type", "active_version", "created_at", "updated_at")


@admin.register(WorkflowDefinitionVersion)
class WorkflowDefinitionVersionAdmin(admin.ModelAdmin):
    list_display = ("workflow", "version", "checksum", "published_at")
    list_filter = ("workflow",)
    search_fields = ("workflow__code", "checksum")
    readonly_fields = ("workflow", "version", "checksum", "specification", "published_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkflowInstance)
class WorkflowInstanceAdmin(admin.ModelAdmin):
    list_display = ("workflow", "object_id", "current_state", "definition_version", "updated_at")
    list_filter = ("workflow", "current_state", "definition_version")
    search_fields = ("object_id",)
    readonly_fields = (
        "workflow",
        "definition_version",
        "content_type",
        "object_id",
        "current_state",
        "data",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkflowTransitionEvent)
class WorkflowTransitionEventAdmin(admin.ModelAdmin):
    list_display = ("instance", "event", "from_state", "to_state", "actor_display", "occurred_at")
    list_filter = ("instance__workflow", "event", "from_state", "to_state")
    search_fields = ("instance__object_id", "actor_display")
    readonly_fields = (
        "instance",
        "event",
        "from_state",
        "to_state",
        "actor",
        "actor_display",
        "metadata",
        "occurred_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(WorkflowStep)
admin.site.register(WorkflowStepPermission)


class GraphEvidenceAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(WorkflowNodeAttempt, GraphEvidenceAdmin)
admin.site.register(WorkflowApprovalDecision, GraphEvidenceAdmin)
admin.site.register(WorkflowContentSnapshot, GraphEvidenceAdmin)
