# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.utils import OperationalError, ProgrammingError
from django.core.exceptions import ValidationError

from .profile_pictures import build_profile_picture_storage_name, get_profile_picture_storage


def _get_default_group_responsible():
    User = get_user_model()
    try:
        user = User.objects.filter(is_superuser=True).order_by("id").first()
        if not user:
            user = User.objects.order_by("id").first()
    except (ProgrammingError, OperationalError):
        user = None
    return user


def _get_default_role_for_group(group=None):
    try:
        if group and group.direction_id:
            role = Role.objects.filter(technical_direction=group.direction).order_by("id").first()
            if role:
                return role
            return None
        return Role.objects.order_by("id").first()
    except (ProgrammingError, OperationalError):
        return None


class TechnicalDirection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        db_table = "TECHNICAL_DIRECTION"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def _generate_default_group_name(self):
        base_name = f"{self.name} - Groupe par défaut"
        try:
            existing = BusinessGroup.objects.filter(name__startswith=base_name)
        except (ProgrammingError, OperationalError):
            return base_name
        if not existing.filter(name=base_name).exists():
            return base_name
        counter = 2
        candidate = f"{base_name} {counter}"
        while existing.filter(name=candidate).exists():
            counter += 1
            candidate = f"{base_name} {counter}"
        return candidate

    def ensure_default_group(self):
        try:
            has_default = self.groups.filter(is_default=True).exists()
        except (ProgrammingError, OperationalError):
            return
        if has_default:
            return
        responsible = _get_default_group_responsible()
        if not responsible:
            return
        BusinessGroup.objects.create(
            name=self._generate_default_group_name(),
            direction=self,
            responsible=responsible,
            is_default=True,
        )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.ensure_default_group()


class BusinessDirection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        db_table = "BUSINESS_METIER_DIRECTION"
        ordering = ["name"]

    def __str__(self):
        return self.name


class BusinessGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    direction = models.ForeignKey(
        TechnicalDirection,
        on_delete=models.PROTECT,
        related_name="groups",
    )
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_groups",
    )
    is_default = models.BooleanField(default=False)
    business_direction = models.ForeignKey(
        BusinessDirection,
        on_delete=models.PROTECT,
        related_name="project_groups",
        null=True,
        blank=True,
        help_text="Direction métier associée à ce groupe.",
    )

    class Meta:
        db_table = "BUSINESS_GROUP"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["direction"],
                condition=Q(is_default=True),
                name="unique_default_group_per_direction",
            ),
            models.UniqueConstraint(
                fields=["business_direction", "direction"],
                condition=Q(business_direction__isnull=False),
                name="unique_business_direction_group_per_technical_direction",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def member_count(self):
        cached = getattr(self, "user_total", None)
        if cached is not None:
            return cached
        return self.users.count()


class Role(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    technical_direction = models.ForeignKey(
        TechnicalDirection,
        on_delete=models.PROTECT,
        related_name="roles",
        null=True,
        blank=True,
        help_text="Direction technique associée à ce rôle.",
    )
    is_admin_role = models.BooleanField(
        default=False,
        help_text="Les rôles administrateurs ne sont pas rattachés à une direction technique.",
    )

    class Meta:
        db_table = "USER_ROLE"
        ordering = ["name"]

    def __str__(self):
        return self.name

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    can_use_api = models.BooleanField(
        default=False,
        help_text="Autorise cet utilisateur à appeler l'API REST avec OAuth.",
    )
    profile_picture = models.ImageField(
        upload_to=build_profile_picture_storage_name,
        storage=get_profile_picture_storage(),
        blank=True,
        null=True,
    )
    role = models.ForeignKey(
        Role, on_delete=models.PROTECT, null=True, blank=True, related_name="users"
    )
    business_group = models.ForeignKey(
        BusinessGroup,
        on_delete=models.PROTECT,
        related_name="users",
        help_text="Groupe métier auquel appartient l'utilisateur.",
        null=True,
        blank=True,
    )

    @property
    def business_direction(self):
        group = getattr(self, "business_group", None)
        if group:
            return group.business_direction
        return None

    def is_role(self, slug: str) -> bool:
        return bool(self.role and self.role.slug == slug)

    def clean(self):
        super().clean()

        try:
            roles_exist = Role.objects.exists()
        except (ProgrammingError, OperationalError):
            roles_exist = False

        if not self.role_id and roles_exist and self._state.adding:
            raise ValidationError({"role": "Chaque utilisateur doit avoir un rôle."})

        role = None
        if self.role_id:
            role = getattr(self, "role", None)
            if role and role.pk != self.role_id:
                role = None
            if role is None:
                role = Role.objects.filter(pk=self.role_id).only("technical_direction_id").first()

        group = None
        if self.business_group_id:
            group = getattr(self, "business_group", None)
            if group and group.pk != self.business_group_id:
                group = None
            if group is None:
                group = BusinessGroup.objects.filter(pk=self.business_group_id).only("direction_id").first()

        role_direction_id = getattr(role, "technical_direction_id", None) if role else None
        group_direction_id = getattr(group, "direction_id", None) if group else None

        if role_direction_id:
            if not group_direction_id:
                raise ValidationError(
                    {"business_group": "Ce rôle requiert un groupe rattaché à sa direction technique."}
                )
            if group_direction_id != role_direction_id:
                raise ValidationError(
                    {"role": "Le rôle sélectionné doit appartenir à la même direction technique que le groupe métier."}
                )
        elif self.business_group_id:
            raise ValidationError(
                {"business_group": "Les rôles sans direction technique ne peuvent pas être rattachés à un groupe."}
            )

    def save(self, *args, **kwargs):
        old_profile_picture = None
        if self.pk:
            try:
                old_profile_picture = (
                    type(self).objects.filter(pk=self.pk)
                    .values_list("profile_picture", flat=True)
                    .first()
                )
            except (ProgrammingError, OperationalError):
                old_profile_picture = None
        if not self.password:
            # Ensure new users created without an explicit password get an unusable one
            self.set_unusable_password()
        if not self.role_id:
            group = getattr(self, "business_group", None)
            if not group and self.business_group_id:
                try:
                    group = BusinessGroup.objects.select_related("direction").filter(pk=self.business_group_id).first()
                except (ProgrammingError, OperationalError):
                    group = None
            default_role = _get_default_role_for_group(group)
            if default_role and not self.role_id:
                self.role = default_role
        if not self.business_group_id and self.role_id:
            role = getattr(self, "role", None)
            if role and role.pk != self.role_id:
                role = None
            if role is None:
                try:
                    role = Role.objects.select_related("technical_direction").filter(pk=self.role_id).first()
                except (ProgrammingError, OperationalError):
                    role = None
            direction = getattr(role, "technical_direction", None)
            if direction:
                try:
                    direction.ensure_default_group()
                except Exception:
                    pass
                try:
                    default_group = (
                        BusinessGroup.objects.filter(direction=direction, is_default=True).order_by("id").first()
                        or BusinessGroup.objects.filter(direction=direction).order_by("id").first()
                    )
                except (ProgrammingError, OperationalError):
                    default_group = None
                if default_group:
                    self.business_group = default_group
        self.full_clean()
        super().save(*args, **kwargs)
        if old_profile_picture and old_profile_picture != self.profile_picture.name:
            try:
                self.profile_picture.storage.delete(old_profile_picture)
            except Exception:
                pass


class OAuthAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_accounts",
    )
    provider = models.CharField(max_length=50)
    provider_user_id = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    token_type = models.CharField(max_length=40, blank=True)
    scope = models.TextField(blank=True)
    raw_profile = models.JSONField(default=dict, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_oauth_account"
        ordering = ["provider", "id"]
        unique_together = (("provider", "provider_user_id"),)
        indexes = [
            models.Index(fields=["provider", "email"]),
            models.Index(fields=["user", "provider"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.provider_user_id}"
