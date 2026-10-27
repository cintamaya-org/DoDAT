# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied

from cintafactory.api.permissions import CanUseAPI, IsSuperuser

from .models import BusinessGroup, _get_default_role_for_group

User = get_user_model()


def _privileged_role(role) -> bool:
    return bool(role and (role.slug == "admin" or role.is_admin_role))


def _privileged_user(user) -> bool:
    return bool(user.is_staff or user.is_superuser or _privileged_role(user.role))


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "is_superuser",
            "can_use_api",
            "role",
            "business_group",
            "profile_picture",
            "last_login",
            "date_joined",
            "password",
        ]
        read_only_fields = ["id", "last_login", "date_joined"]

    def validate(self, attrs):
        actor = getattr(self.context.get("request"), "user", None)
        if "can_use_api" in attrs and not (
            actor is not None
            and actor.is_authenticated
            and actor.is_superuser
        ):
            raise PermissionDenied("Seul un superadministrateur peut modifier l'accès API.")
        if actor is not None and actor.is_authenticated and actor.is_superuser:
            return attrs
        if self.instance is not None and _privileged_user(self.instance):
            raise PermissionDenied("La modification des comptes privilégiés est réservée aux superutilisateurs.")
        errors = {}
        for field in ("is_staff", "is_superuser"):
            if attrs.get(field):
                errors[field] = "Seul un superutilisateur peut attribuer ce droit."
        role = attrs.get("role", getattr(self.instance, "role", None))
        if role is None:
            group = attrs.get("business_group", getattr(self.instance, "business_group", None))
            role = _get_default_role_for_group(group)
            # Pin the same effective default that User.save would otherwise choose.
            if role is not None:
                attrs["role"] = role
        if _privileged_role(role):
            errors["role"] = "Seul un superutilisateur peut attribuer un rôle administrateur."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = super().create(validated_data)
        if password:
            user.set_password(password)
            user.save(update_fields=["password"])
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save(update_fields=["password"])
        return user


class BusinessGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessGroup
        fields = [
            "id",
            "name",
            "direction",
            "responsible",
            "is_default",
            "business_direction",
        ]


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [CanUseAPI, IsSuperuser]

    def get_queryset(self) -> QuerySet:
        return User.objects.select_related("role", "business_group").all()

    def perform_destroy(self, instance):
        if not self.request.user.is_superuser and _privileged_user(instance):
            raise PermissionDenied("La suppression des comptes privilégiés est réservée aux superutilisateurs.")
        super().perform_destroy(instance)


class BusinessGroupViewSet(viewsets.ModelViewSet):
    serializer_class = BusinessGroupSerializer
    permission_classes = [CanUseAPI, IsSuperuser]

    def get_queryset(self) -> QuerySet:
        return BusinessGroup.objects.select_related(
            "direction",
            "responsible",
            "business_direction",
        ).all()
