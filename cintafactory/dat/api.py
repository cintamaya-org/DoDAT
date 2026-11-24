# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import serializers, viewsets
from rest_framework.permissions import BasePermission

from cintafactory.api.permissions import CanUseAPI

from .permissions import (
    filter_dat_queryset_for_user,
    user_can_change_dat,
    user_can_create_dat_entities,
    user_can_manage_dat,
)
from .models import Application, DAT
from .versioning import DATReferenceError, validate_reference


class ApplicationApiPermission(BasePermission):
    """Match the application CRUD view's authenticated-read and managed-write rules."""

    def has_permission(self, request, view) -> bool:
        if not request.user.is_authenticated:
            return False
        action = getattr(view, "action", None)
        if action in {"list", "retrieve", "metadata"}:
            return True
        if action == "create":
            return user_can_create_dat_entities(request.user)
        return action in {"update", "partial_update", "destroy"}

    def has_object_permission(self, request, view, obj) -> bool:
        action = getattr(view, "action", None)
        if action in {"list", "retrieve", "metadata"}:
            return True
        return action in {"update", "partial_update", "destroy"} and user_can_manage_dat(request.user)


class DATApiPermission(BasePermission):
    """Match DAT UI visibility and mutation rules for every API action."""

    def has_permission(self, request, view) -> bool:
        if not request.user.is_authenticated:
            return False
        action = getattr(view, "action", None)
        if action in {"list", "retrieve", "metadata"}:
            return True
        if action == "create":
            return user_can_create_dat_entities(request.user)
        return action in {"update", "partial_update", "destroy"}

    def has_object_permission(self, request, view, obj) -> bool:
        action = getattr(view, "action", None)
        if action in {"list", "retrieve", "metadata"}:
            return True
        if action in {"update", "partial_update"}:
            return user_can_change_dat(obj, request.user)
        if action == "destroy":
            return user_can_manage_dat(request.user)
        return False


class ApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = [
            "id",
            "code",
            "name",
            "description",
            "business_direction",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class DATSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        application = attrs.get("application")
        application_id = application.pk if application else getattr(self.instance, "application_id", None)
        if application_id is None:
            raise serializers.ValidationError({"application": "L’application du DAT est obligatoire."})
        reference = attrs.get("reference", self.instance.reference if self.instance else "")
        try:
            validate_reference(application_id, reference, original=self.instance)
        except DATReferenceError as exc:
            raise serializers.ValidationError({"reference": exc.messages}) from exc
        return attrs

    def save(self, **kwargs):
        try:
            return super().save(**kwargs)
        except DATReferenceError as exc:
            raise serializers.ValidationError({"reference": exc.messages}) from exc

    class Meta:
        model = DAT
        # The domain validator also handles allocation and legacy numeric spellings.
        validators = []
        extra_kwargs = {"reference": {"required": False, "allow_blank": True, "trim_whitespace": False}}
        fields = [
            "id",
            "reference",
            "title",
            "description",
            "application",
            "status",
            "owner",
            "business_direction",
            "created_at",
            "updated_at",
            "pdf_export_in_progress",
            "pdf_export_requested_at",
            "pdf_export_requested_by",
            "pdf_export_requested_by_display",
            "pdf_export_path",
            "pdf_export_content_type",
            "pdf_export_size",
            "secure_export_requires_dual_admin_approval",
        ]
        read_only_fields = [
            "id",
            "status",
            "owner",
            "business_direction",
            "created_at",
            "updated_at",
            "pdf_export_in_progress",
            "pdf_export_requested_at",
            "pdf_export_requested_by",
            "pdf_export_requested_by_display",
            "pdf_export_path",
            "pdf_export_content_type",
            "pdf_export_size",
            "secure_export_requires_dual_admin_approval",
        ]


class ApplicationViewSet(viewsets.ModelViewSet):
    serializer_class = ApplicationSerializer
    permission_classes = [CanUseAPI, ApplicationApiPermission]

    def get_queryset(self) -> QuerySet:
        return Application.objects.select_related("business_direction").all()


class DATViewSet(viewsets.ModelViewSet):
    serializer_class = DATSerializer
    permission_classes = [CanUseAPI, DATApiPermission]

    def get_queryset(self) -> QuerySet:
        queryset = DAT.objects.select_related(
            "application",
            "owner",
            "business_direction",
            "pdf_export_requested_by",
        )
        return filter_dat_queryset_for_user(queryset, self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
