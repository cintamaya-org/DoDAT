# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.db.models import QuerySet
from rest_framework import serializers, viewsets

from .models import Application, DAT


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
    class Meta:
        model = DAT
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
    permission_required_by_action = {
        "list": ["dat.view_application"],
        "retrieve": ["dat.view_application"],
        "create": ["dat.add_application"],
        "update": ["dat.change_application"],
        "partial_update": ["dat.change_application"],
        "destroy": ["dat.delete_application"],
    }

    def get_queryset(self) -> QuerySet:
        return Application.objects.select_related("business_direction").all()


class DATViewSet(viewsets.ModelViewSet):
    serializer_class = DATSerializer
    permission_required_by_action = {
        "list": ["dat.view_dat"],
        "retrieve": ["dat.view_dat"],
        "create": ["dat.add_dat"],
        "update": ["dat.change_dat"],
        "partial_update": ["dat.change_dat"],
        "destroy": ["dat.delete_dat"],
    }

    def get_queryset(self) -> QuerySet:
        return DAT.objects.select_related(
            "application",
            "owner",
            "business_direction",
            "pdf_export_requested_by",
        ).all()

    def perform_create(self, serializer):
        owner = serializer.validated_data.get("owner")
        if owner is None and self.request and self.request.user.is_authenticated:
            serializer.save(owner=self.request.user)
            return
        serializer.save()
