# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import BusinessDirection, TechnicalDirection, BusinessGroup, Role, User, OAuthAccount


@admin.register(BusinessDirection)
class BusinessDirectionAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    ordering = ("name",)


@admin.register(TechnicalDirection)
class TechnicalDirectionAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "group_count")
    search_fields = ("name", "slug")
    ordering = ("name",)

    def group_count(self, obj):
        return obj.groups.count()

    group_count.short_description = "Groupes"


@admin.register(BusinessGroup)
class BusinessGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "direction", "business_direction", "responsible", "is_default", "user_count")
    list_filter = ("direction", "business_direction", "is_default")
    search_fields = ("name", "direction__name", "business_direction__name", "responsible__username")
    ordering = ("name",)
    readonly_fields = ()

    def user_count(self, obj):
        return obj.member_count

    user_count.short_description = "Utilisateurs"

@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "technical_direction", "is_admin_role", "user_count")
    search_fields = ("name", "slug", "technical_direction__name")
    list_filter = ("technical_direction", "is_admin_role")
    ordering = ("name",)

    def user_count(self, obj):
        return obj.users.count()

    user_count.short_description = "Utilisateurs"

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Structure", {"fields": ("business_group", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("API", {"fields": ("can_use_api",)}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "password1",
                    "password2",
                    "business_group",
                    "role",
                ),
            },
        ),
    )
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "role",
        "role_direction",
        "business_group",
        "business_direction",
    )
    list_filter = (
        "is_staff",
        "is_superuser",
        "is_active",
        "groups",
        "role",
        "role__technical_direction",
        "business_group",
        "business_group__business_direction",
    )
    search_fields = ("username", "first_name", "last_name", "email")
    ordering = ("username",)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly_fields.append("can_use_api")
        return tuple(readonly_fields)

    def business_direction(self, obj):
        group = getattr(obj, "business_group", None)
        if group and group.business_direction:
            return group.business_direction
        return None

    business_direction.short_description = "Direction métier"

    def role_direction(self, obj):
        role = getattr(obj, "role", None)
        if role and role.technical_direction:
            return role.technical_direction
        if role and role.is_admin_role:
            return "Transverse"
        return None

    role_direction.short_description = "Direction technique (rôle)"


@admin.register(OAuthAccount)
class OAuthAccountAdmin(admin.ModelAdmin):
    list_display = ("provider", "provider_user_id", "email", "user", "updated_at")
    list_filter = ("provider",)
    search_fields = ("provider", "provider_user_id", "email", "user__username")
    ordering = ("provider", "id")
