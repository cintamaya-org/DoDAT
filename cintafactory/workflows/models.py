# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class Workflow(models.Model):
    """Declarative workflow definition bound to a concrete Django model."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="workflows",
    )
    is_active = models.BooleanField(default=True)
    active_version = models.ForeignKey(
        "WorkflowDefinitionVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="active_for_workflows",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_definition"
        ordering = ["name"]
        verbose_name = _("Workflow")
        verbose_name_plural = _("Workflows")

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return self.name


class WorkflowDefinitionVersion(models.Model):
    """Immutable, validated snapshot used by running workflow instances."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    specification = models.JSONField()
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workflow_definition_version"
        ordering = ["workflow", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "version"],
                name="workflow_definition_version_unique_number",
            ),
            models.UniqueConstraint(
                fields=["workflow", "checksum"],
                name="workflow_definition_version_unique_checksum",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return f"{self.workflow.code} v{self.version}"

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                "workflow_id",
                "version",
                "checksum",
                "specification",
            ).first()
            if original is not None and any(
                (
                    original["workflow_id"] != self.workflow_id,
                    original["version"] != self.version,
                    original["checksum"] != self.checksum,
                    original["specification"] != self.specification,
                )
            ):
                raise ValidationError("Published workflow versions are immutable.")
        return super().save(*args, **kwargs)


class WorkflowInstance(models.Model):
    """Pinned workflow state for any model object."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.PROTECT,
        related_name="instances",
    )
    definition_version = models.ForeignKey(
        WorkflowDefinitionVersion,
        on_delete=models.PROTECT,
        related_name="instances",
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        related_name="workflow_instances",
    )
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")
    current_state = models.CharField(max_length=64)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_instance"
        ordering = ["-updated_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "content_type", "object_id"],
                name="workflow_instance_unique_object",
            ),
        ]
        indexes = [
            models.Index(
                fields=["workflow", "current_state"],
                name="workflow_instance_state_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return f"{self.workflow.code}:{self.object_id}@{self.current_state}"


class WorkflowTransitionEvent(models.Model):
    """Append-only audit record emitted for each successful transition."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    instance = models.ForeignKey(
        WorkflowInstance,
        on_delete=models.CASCADE,
        related_name="transition_events",
    )
    event = models.SlugField(max_length=64)
    from_state = models.CharField(max_length=64)
    to_state = models.CharField(max_length=64)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflow_transition_events",
    )
    actor_display = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workflow_transition_event"
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(
                fields=["instance", "occurred_at"],
                name="workflow_transition_time_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return f"{self.from_state} → {self.to_state} ({self.event})"


class WorkflowStep(models.Model):
    """Single workflow column/state displayed on the Kanban board."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    workflow = models.ForeignKey(
        Workflow,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    key = models.SlugField(max_length=64)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    state = models.CharField(
        max_length=64,
        help_text=_("Code stored on the target object to represent this step"),
    )
    is_initial = models.BooleanField(
        default=False,
        help_text=_("Whether this step is the default/start of the workflow."),
    )

    class Meta:
        db_table = "workflow_step"
        ordering = ["order", "pk"]
        unique_together = (
            ("workflow", "key"),
            ("workflow", "state"),
        )
        verbose_name = _("Workflow step")
        verbose_name_plural = _("Workflow steps")

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return f"{self.workflow}: {self.name}"

    def permissions_by_type(self, permission_type: str) -> models.QuerySet["WorkflowStepPermission"]:
        return self.permissions.filter(permission_type=permission_type).select_related("role", "user")

    @property
    def read_permissions(self) -> models.QuerySet["WorkflowStepPermission"]:
        return self.permissions_by_type(WorkflowStepPermission.PERMISSION_READ)

    @property
    def write_permissions(self) -> models.QuerySet["WorkflowStepPermission"]:
        return self.permissions_by_type(WorkflowStepPermission.PERMISSION_WRITE)


class WorkflowStepPermission(models.Model):
    """Role/user assignments for workflow steps."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    PERMISSION_READ = "read"
    PERMISSION_WRITE = "write"
    PERMISSION_CHOICES = [
        (PERMISSION_READ, _("Read")),
        (PERMISSION_WRITE, _("Write")),
    ]

    step = models.ForeignKey(
        WorkflowStep,
        on_delete=models.CASCADE,
        related_name="permissions",
    )
    permission_type = models.CharField(max_length=5, choices=PERMISSION_CHOICES)
    role = models.ForeignKey(
        "users.Role",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="workflow_step_permissions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="workflow_step_permissions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workflow_step_permission"
        constraints = [
            models.CheckConstraint(
                check=models.Q(role__isnull=False) | models.Q(user__isnull=False),
                name="workflow_step_permission_role_or_user",
            ),
            models.UniqueConstraint(
                fields=["step", "permission_type", "role"],
                condition=models.Q(role__isnull=False),
                name="workflow_step_permission_unique_role",
            ),
            models.UniqueConstraint(
                fields=["step", "permission_type", "user"],
                condition=models.Q(user__isnull=False),
                name="workflow_step_permission_unique_user",
            ),
        ]
        ordering = ["step", "permission_type", "role__name", "user__username"]
        verbose_name = _("Workflow step permission")
        verbose_name_plural = _("Workflow step permissions")

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        if self.role:
            subject = self.role.name
        elif self.user:
            subject = self.user.get_username()
        else:
            subject = _("Unknown")
        return f"{subject} → {self.step} ({self.get_permission_type_display()})"

    @property
    def subject(self) -> str:
        if self.role:
            return self.role.name
        if self.user:
            return self.user.get_username()
        return _("Unknown")


class NotificationLevel(models.TextChoices):
    INFO = "info", _("Information")
    SUCCESS = "success", _("Succès")
    WARNING = "warning", _("Avertissement")
    ERROR = "error", _("Erreur")


class NotificationType(models.Model):
    """Reusable notification payload shared across user notifications."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    LEVEL_INFO = NotificationLevel.INFO
    LEVEL_SUCCESS = NotificationLevel.SUCCESS
    LEVEL_WARNING = NotificationLevel.WARNING
    LEVEL_ERROR = NotificationLevel.ERROR
    LEVEL_CHOICES = NotificationLevel.choices

    title = models.CharField(max_length=255)
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workflow_notification_type"
        unique_together = ("title", "level")
        ordering = ["title", "level", "pk"]
        verbose_name = _("Type de notification")
        verbose_name_plural = _("Types de notification")

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return f"{self.title} ({self.get_level_display()})"


class NotificationMessage(models.Model):
    """Stores deduplicated notification message payloads."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    content = models.TextField(blank=True, default="", unique=True,null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workflow_notification_message"
        ordering = ["pk"]
        verbose_name = _("Message de notification")
        verbose_name_plural = _("Messages de notification")

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        preview = (self.content or "").strip()
        if len(preview) > 60:
            preview = f"{preview[:57]}..."
        return preview or _("Message vide")


class UserNotification(models.Model):
    """Notification explicitly targeted to a single user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    LEVEL_INFO = NotificationLevel.INFO
    LEVEL_SUCCESS = NotificationLevel.SUCCESS
    LEVEL_WARNING = NotificationLevel.WARNING
    LEVEL_ERROR = NotificationLevel.ERROR
    LEVEL_CHOICES = NotificationLevel.choices

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflow_notifications",
    )
    notification_type = models.ForeignKey(
        NotificationType,
        on_delete=models.PROTECT,
        related_name="user_notifications",
    )
    notification_message = models.ForeignKey(
        NotificationMessage,
        on_delete=models.PROTECT,
        related_name="user_notifications",
    )
    dat = models.ForeignKey(
        "dat.DAT",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="user_notifications",
    )
    target_url = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_by_display = models.CharField(max_length=255, blank=True)
    extra_data = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    viewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "workflow_user_notification"
        ordering = ["-created_at", "-pk"]
        verbose_name = _("Notification utilisateur")
        verbose_name_plural = _("Notifications utilisateur")

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return f"{self.title} → {self.user}"

    @property
    def is_viewed(self) -> bool:
        return self.viewed_at is not None

    def mark_as_viewed(self) -> None:
        if self.is_viewed:
            return
        from django.utils import timezone

        self.viewed_at = timezone.now()
        self.save(update_fields=["viewed_at"])

    @property
    def actor_name(self) -> str:
        if self.created_by_display:
            return self.created_by_display
        if self.created_by:
            full_name = self.created_by.get_full_name()
            if full_name:
                return full_name
            return self.created_by.get_username()
        return "Système"

    @property
    def title(self) -> str:
        if self.notification_type_id is None:
            return ""
        return self.notification_type.title

    @property
    def message(self) -> str:
        if self.notification_message_id is None:
            return ""
        return self.notification_message.content

    @property
    def level(self) -> str:
        if self.notification_type_id is None:
            return NotificationLevel.INFO
        return self.notification_type.level

    def get_level_display(self) -> str:  # pragma: no cover - compatibility helper
        if self.notification_type_id is None:
            return dict(NotificationLevel.choices).get(NotificationLevel.INFO, NotificationLevel.INFO)
        return self.notification_type.get_level_display()


class HistoryNotificationSeen(models.Model):
    """Track which workflow history entries have been seen by a user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflow_history_notifications_seen",
    )
    history = models.ForeignKey(
        "dat.DATHistory",
        on_delete=models.CASCADE,
        related_name="workflow_seen_by",
    )
    seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workflow_history_notification_seen"
        ordering = ["-seen_at", "-pk"]
        unique_together = ("user", "history")
        verbose_name = _("Consultation d’historique de workflow")
        verbose_name_plural = _("Consultations d’historique de workflow")

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return f"{self.history} → {self.user}"
