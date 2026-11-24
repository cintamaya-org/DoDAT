# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib import admin

from .forms import DATReferenceForm
from .versioning import DATReferenceError
from .models import (
    Application,
    DAT,
    DATExportAccessApproval,
    DATExportAccessHistory,
    DATExportAccessRequest,
    DATParticipant,
    DATSectionAttachment,
)


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "business_direction", "formatted_created_at", "formatted_updated_at")
    search_fields = ("code", "name", "business_direction__name")
    ordering = ("name",)


class DATParticipantInline(admin.TabularInline):
    model = DATParticipant
    extra = 0
    autocomplete_fields = ("user", "role")
    readonly_fields = ("created_at",)


@admin.register(DAT)
class DATAdmin(admin.ModelAdmin):
    form = DATReferenceForm
    list_display = ("display_reference", "title", "application", "business_direction", "status", "owner", "created_at")
    list_select_related = ("application", "business_direction", "owner")
    list_filter = ("status", "created_at", "application", "business_direction")
    search_fields = ("reference", "title", "description", "application__name", "business_direction__name")
    ordering = ("-created_at",)
    inlines = (DATParticipantInline,)

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        return ["application", *(field for field in fields if field != "application")]

    def changeform_view(self, request, *args, **kwargs):
        try:
            return super().changeform_view(request, *args, **kwargs)
        except DATReferenceError as exc:
            # A concurrent save can claim a reference after ModelForm validation.
            # Redisplay the original POST and inlines with the field error.
            request.dat_reference_error = exc
            return super().changeform_view(request, *args, **kwargs)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if error := getattr(request, "dat_reference_error", None):
            class ConflictingReferenceForm(form):
                def clean_reference(self):
                    raise error

            return ConflictingReferenceForm
        return form


class AttachmentDATFilter(admin.RelatedOnlyFieldListFilter):
    def field_choices(self, field, request, model_admin):
        dat_ids = model_admin.get_queryset(request).values("section__dat_id")
        dats = DAT.objects.filter(pk__in=dat_ids).select_related("application").order_by("application__name", "reference")
        return [(dat.pk, dat.display_reference()) for dat in dats]


@admin.register(DATSectionAttachment)
class DATSectionAttachmentAdmin(admin.ModelAdmin):
    list_display = ("display_name", "section", "uploaded_by", "size", "created_at")
    list_select_related = ("section__dat__application", "section__metadata", "uploaded_by")
    list_filter = ("created_at", ("section__dat", AttachmentDATFilter))
    search_fields = ("display_name", "original_name", "section__title", "section__dat__reference")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)


@admin.register(DATExportAccessRequest)
class DATExportAccessRequestAdmin(admin.ModelAdmin):
    list_display = ("dat", "status", "requested_by", "requested_at", "approve_deadline_at", "access_valid_until")
    list_select_related = ("dat__application", "requested_by")
    list_filter = ("status", "requested_at")
    search_fields = ("dat__reference", "dat__title", "requested_by__username")
    ordering = ("-requested_at",)


@admin.register(DATExportAccessApproval)
class DATExportAccessApprovalAdmin(admin.ModelAdmin):
    list_display = ("dat", "request", "approved_by", "approved_at")
    list_select_related = ("dat__application", "request__dat__application", "approved_by")
    list_filter = ("approved_at",)
    search_fields = ("dat__reference", "approved_by__username")
    ordering = ("-approved_at",)


@admin.register(DATExportAccessHistory)
class DATExportAccessHistoryAdmin(admin.ModelAdmin):
    list_display = ("dat", "event_type", "actor", "created_at")
    list_select_related = ("dat__application", "actor")
    list_filter = ("event_type", "created_at")
    search_fields = ("dat__reference", "actor__username")
    ordering = ("-created_at",)
