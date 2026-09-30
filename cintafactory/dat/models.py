# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import models
from django.utils import timezone


class DATStatus(models.TextChoices):
    NOUVELLE_DEMANDE = "nouvelle_demande", "Nouvelle demande"
    EN_COURS = "en_cours", "En cours"
    EN_ATTENTE_DE_REVUE = "en_attente_de_revue", "En Attente de revue"
    VALIDER = "valider", "Valider"
    REFUSE = "refuse", "Refusé"
    RESERVE = "reserve", "Reserve"


class DATParticipantType(models.TextChoices):
    RESPONSABLE = "responsable", "Responsable"
    EXECUTANT = "executant", "Normal"


class Application(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=64, unique=True, verbose_name="Code")
    name = models.CharField(max_length=200, verbose_name="Nom")
    description = models.TextField(blank=True, verbose_name="Description")
    business_direction = models.ForeignKey(
        "users.BusinessDirection",
        on_delete=models.PROTECT,
        related_name="applications",
        null=True,
        verbose_name="Direction métier",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        db_table = "dat_application"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def formatted_created_at(self) -> str:
        if not self.created_at:
            return ""
        reference = timezone.localtime(self.created_at)
        return reference.strftime("%d/%m/%Y à %Hh%M")

    formatted_created_at.short_description = "Créé le"
    formatted_created_at.admin_order_field = "created_at"

    def formatted_updated_at(self) -> str:
        if not self.updated_at:
            return ""
        reference = timezone.localtime(self.updated_at)
        return reference.strftime("%d/%m/%Y à %Hh%M")

    formatted_updated_at.short_description = "Mis à jour le"
    formatted_updated_at.admin_order_field = "updated_at"


class DAT(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    application = models.ForeignKey(
        Application,
        on_delete=models.PROTECT,
        related_name="dats",
    )
    status = models.CharField(
        max_length=64,
        choices=DATStatus.choices,
        default=DATStatus.NOUVELLE_DEMANDE,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dats",
    )
    business_direction = models.ForeignKey(
        "users.BusinessDirection",
        on_delete=models.PROTECT,
        related_name="dats",
        verbose_name="Direction métier",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pdf_export_in_progress = models.BooleanField(default=False)
    pdf_export_requested_at = models.DateTimeField(blank=True, null=True)
    pdf_export_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    pdf_export_requested_by_display = models.CharField(max_length=255, blank=True)
    pdf_export_path = models.CharField(max_length=500, blank=True, default="")
    pdf_export_content_type = models.CharField(max_length=120, blank=True, default="application/pdf")
    pdf_export_size = models.PositiveBigIntegerField(default=0)
    secure_export_requires_dual_admin_approval = models.BooleanField(default=True)

    class Meta:
        db_table = "dat_dat"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["-updated_at", "-id"],
                name="dat_updated_pk_desc_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} - {self.title}"

    def sync_business_direction(self):
        if not self.application_id:
            self.business_direction_id = None
            return
        application = getattr(self, "application", None)
        if application is None or application.pk != self.application_id:
            application = Application.objects.filter(pk=self.application_id).only("business_direction_id").first()
        if application:
            self.business_direction_id = application.business_direction_id

    def save(self, *args, **kwargs):
        self.sync_business_direction()
        super().save(*args, **kwargs)


class DATParticipant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="participants",
        verbose_name="DAT",
    )
    role = models.ForeignKey(
        "users.Role",
        on_delete=models.PROTECT,
        related_name="dat_participants",
        verbose_name="Rôle",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dat_participations",
        verbose_name="Utilisateur",
    )
    participant_type = models.CharField(
        max_length=20,
        choices=DATParticipantType.choices,
        default=DATParticipantType.RESPONSABLE,
        verbose_name="Type d'affectation",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dat_participant"
        constraints = [
            models.UniqueConstraint(
                fields=["dat", "role"],
                name="dat_participant_unique_role_per_dat",
            ),
            models.UniqueConstraint(
                fields=["dat", "user"],
                name="dat_participant_unique_user_per_dat",
            ),
        ]
        ordering = ["dat_id", "role__name", "user__username"]
        verbose_name = "Participant du DAT"
        verbose_name_plural = "Participants du DAT"

    def __str__(self) -> str:
        return f"{self.dat.reference} - {self.role.name} - {self.user.get_username()}"


class DATHistoryAction(models.TextChoices):
    CREATED = "created", "Création"
    UPDATED = "updated", "Mise à jour"
    STATUS_CHANGED = "status_changed", "Changement de statut"
    OWNER_CHANGED = "owner_changed", "Changement de responsable"
    SECTION_UPDATED = "section_updated", "Section mise à jour"
    RESPONSIBLE_VALIDATION = "responsible_validation", "Validation référent"
    DELETED = "deleted", "Suppression"


class DATHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="history_entries",
    )
    action = models.CharField(
        max_length=32,
        choices=DATHistoryAction.choices,
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="dat_history_entries",
    )
    performed_by_display = models.CharField(max_length=255, blank=True)
    details = models.JSONField(blank=True, null=True)
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dat_history"
        ordering = ["-performed_at", "-id"]

    def actor_name(self) -> str:
        if self.performed_by_display:
            return self.performed_by_display
        if self.performed_by:
            full_name = self.performed_by.get_full_name()
            if full_name:
                return full_name
            return self.performed_by.get_username()
        return "Système"

    def formatted_performed_at(self) -> str:
        reference = timezone.localtime(self.performed_at)
        return reference.strftime("%d/%m/%Y à %Hh%M")

    @property
    def status_change(self):
        if self.action != DATHistoryAction.STATUS_CHANGED:
            return None
        details = self.details or {}
        if not isinstance(details, dict):
            return None
        status_from = details.get("from")
        status_to = details.get("to")
        if status_from in ("", None) and status_to in ("", None):
            return None
        return {"from": status_from, "to": status_to}

    @property
    def status_change_from(self) -> str:
        details = self.status_change
        if details is None:
            return ""
        return details.get("from") or ""

    @property
    def status_change_to(self) -> str:
        details = self.status_change
        if details is None:
            return ""
        return details.get("to") or ""

    def __str__(self) -> str:
        username = self.actor_name()
        timestamp = timezone.localtime(self.performed_at)
        return f"{self.get_action_display()} par {username} le {timestamp.strftime('%d/%m/%Y %H:%M:%S')}"


class DATReserveHistoryAction(models.TextChoices):
    SET = "set", "Mise en réserve"
    CLEARED = "cleared", "Réserve levée"


class DATReserveHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="reserve_history_entries",
    )
    action = models.CharField(
        max_length=20,
        choices=DATReserveHistoryAction.choices,
        default=DATReserveHistoryAction.SET,
    )
    section_slug = models.SlugField(max_length=100, blank=True)
    section_title = models.CharField(max_length=200, blank=True)
    reserve_message = models.TextField(blank=True)
    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="dat_reserve_history_entries",
    )
    reserved_by_display = models.CharField(max_length=255, blank=True)
    reserved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dat_reserve_history"
        ordering = ["-reserved_at", "-id"]

    def actor_name(self) -> str:
        if self.reserved_by_display:
            return self.reserved_by_display
        if self.reserved_by:
            full_name = self.reserved_by.get_full_name()
            if full_name:
                return full_name
            return self.reserved_by.get_username()
        return "Système"

    def formatted_reserved_at(self) -> str:
        reference = timezone.localtime(self.reserved_at)
        return reference.strftime("%d/%m/%Y à %Hh%M")

    def __str__(self) -> str:
        username = self.actor_name()
        timestamp = timezone.localtime(self.reserved_at)
        return f"{self.get_action_display()} par {username} le {timestamp.strftime('%d/%m/%Y %H:%M:%S')}"


class DATSection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="sections",
        verbose_name="DAT",
    )
    metadata = models.OneToOneField(
        "DATSectionMetadata",
        on_delete=models.PROTECT,
        related_name="section",
        verbose_name="Metadata",
    )
    order = models.PositiveIntegerField(default=0, verbose_name="Ordre")
    allowed_roles = models.ManyToManyField(
        "users.Role",
        related_name="editable_dat_sections",
        blank=True,
        verbose_name="Rôles autorisés",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        db_table = "dat_section"
        ordering = ["order", "id"]
        verbose_name = "Section de DAT"
        verbose_name_plural = "Sections de DAT"

    def __str__(self) -> str:
        title = self.title or self.slug or "Section"
        return f"{self.dat.reference} - {title}"

    @property
    def title(self) -> str:
        metadata = getattr(self, "metadata", None)
        return getattr(metadata, "title", "") or ""

    @property
    def slug(self) -> str:
        metadata = getattr(self, "metadata", None)
        return getattr(metadata, "slug", "") or ""

    @property
    def description(self) -> str:
        metadata = getattr(self, "metadata", None)
        return getattr(metadata, "description", "") or ""

    def can_user_edit(self, user) -> bool:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        from .permissions import user_is_assigned_to_section

        return user_is_assigned_to_section(self, user)


class DATSectionResponsible(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="section_responsibles",
        verbose_name="DAT",
    )
    section = models.OneToOneField(
        DATSection,
        on_delete=models.CASCADE,
        related_name="responsible_assignment",
        verbose_name="Section",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dat_section_responsibilities",
        verbose_name="Responsable",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        db_table = "dat_section_responsible"
        ordering = ["section__order", "section__id"]
        verbose_name = "Responsable de section DAT"
        verbose_name_plural = "Responsables de section DAT"

    def __str__(self) -> str:
        section_title = getattr(self.section, "title", None) or getattr(self.section, "slug", None) or "Section"
        return f"{self.dat.reference} - {section_title} - {self.user.get_username()}"


class DATSectionParticipant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="section_participants",
        verbose_name="DAT",
    )
    section = models.OneToOneField(
        DATSection,
        on_delete=models.CASCADE,
        related_name="participant_assignment",
        verbose_name="Section",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="dat_section_participations",
        verbose_name="Participant",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        db_table = "dat_section_participant"
        ordering = ["section__order", "section__id"]
        verbose_name = "Participant de section DAT"
        verbose_name_plural = "Participants de section DAT"

    def __str__(self) -> str:
        section_title = getattr(self.section, "title", None) or getattr(self.section, "slug", None) or "Section"
        return f"{self.dat.reference} - {section_title} - {self.user.get_username()}"


class DATAdmin(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="dat_admins",
        verbose_name="DAT",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="managed_dats",
        verbose_name="Administrateur DAT",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")

    class Meta:
        db_table = "dat_admin"
        ordering = ["dat_id", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["dat", "user"],
                name="dat_admin_unique_user_per_dat",
            ),
        ]
        verbose_name = "Administrateur DAT"
        verbose_name_plural = "Administrateurs DAT"

    def __str__(self) -> str:
        return f"{self.dat.reference} - {self.user.get_username()}"


class DATExportAccessRequestStatus(models.TextChoices):
    PENDING = "pending", "En attente"
    APPROVED = "approved", "Approuvée"
    EXPIRED = "expired", "Expirée"


class DATExportAccessEventType(models.TextChoices):
    REQUEST_CREATED = "request_created", "Demande créée"
    APPROVED = "approved", "Demande approuvée"
    ACCESS_GRANTED = "access_granted", "Accès autorisé"
    DOWNLOAD_PDF = "download_pdf", "Téléchargement PDF"
    DOWNLOAD_JSON = "download_json", "Téléchargement JSON"
    REQUEST_EXPIRED = "request_expired", "Demande expirée"
    ACCESS_EXPIRED = "access_expired", "Accès expiré"


class DATExportAccessRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="export_access_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="dat_export_access_requests",
    )
    status = models.CharField(
        max_length=20,
        choices=DATExportAccessRequestStatus.choices,
        default=DATExportAccessRequestStatus.PENDING,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    approve_deadline_at = models.DateTimeField()
    approved_at = models.DateTimeField(blank=True, null=True)
    access_valid_until = models.DateTimeField(blank=True, null=True)
    required_approvals = models.PositiveSmallIntegerField(default=2)

    class Meta:
        db_table = "dat_export_access_request"
        ordering = ["-requested_at", "-id"]

    def __str__(self) -> str:
        return f"{self.dat.reference} - {self.get_status_display()}"


class DATExportAccessApproval(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        DATExportAccessRequest,
        on_delete=models.CASCADE,
        related_name="approvals",
    )
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="export_access_approvals",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="dat_export_access_approvals",
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dat_export_access_approval"
        ordering = ["approved_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "approved_by"],
                name="dat_export_access_approval_unique_request_user",
            ),
        ]


class DATExportAccessHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dat = models.ForeignKey(
        DAT,
        on_delete=models.CASCADE,
        related_name="export_access_history_entries",
    )
    request = models.ForeignKey(
        DATExportAccessRequest,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="history_entries",
    )
    event_type = models.CharField(
        max_length=32,
        choices=DATExportAccessEventType.choices,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="dat_export_access_history_entries",
    )
    details = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dat_export_access_history"
        ordering = ["-created_at", "-id"]


class DATSectionMetadata(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200, verbose_name="Titre")
    slug = models.SlugField(max_length=100, verbose_name="Identifiant")
    description = models.TextField(blank=True, verbose_name="Description")

    class Meta:
        db_table = "dat_section_metada"
        verbose_name = "Metadata de section"
        verbose_name_plural = "Metadata de sections"

    def __str__(self) -> str:
        return self.title or self.slug or f"Metadata #{self.pk}"


class DATSubSection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    section = models.ForeignKey(
        DATSection,
        on_delete=models.CASCADE,
        related_name="sub_sections",
        verbose_name="Section",
    )
    title = models.CharField(max_length=200, verbose_name="Titre")
    slug = models.SlugField(max_length=100, verbose_name="Identifiant")
    order = models.PositiveIntegerField(default=0, verbose_name="Ordre")
    description = models.TextField(blank=True, verbose_name="Description")
    allowed_roles = models.ManyToManyField(
        "users.Role",
        related_name="editable_dat_sub_sections",
        blank=True,
        verbose_name="Rôles autorisés",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        db_table = "dat_sub_section"
        ordering = ["order", "id"]
        unique_together = (("section", "slug"),)
        verbose_name = "Sous-section de DAT"
        verbose_name_plural = "Sous-sections de DAT"

    def __str__(self) -> str:
        return f"{self.section.dat.reference} - {self.section.title} - {self.title}"

    def can_user_edit(self, user) -> bool:
        if getattr(self, "section", None) and getattr(self.section, "slug", None) == "validation":
            return False
        if self.section is None:
            return False
        if not getattr(user, "is_authenticated", False):
            return False
        from .permissions import user_is_assigned_to_section

        return user_is_assigned_to_section(self.section, user)


class DATSectionAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    section = models.ForeignKey(
        DATSection,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="Section",
    )
    storage_path = models.CharField(max_length=512, verbose_name="Chemin de stockage")
    original_name = models.CharField(max_length=255, verbose_name="Nom d'origine")
    display_name = models.CharField(max_length=255, verbose_name="Nom affiché")
    extension = models.CharField(max_length=16, verbose_name="Extension")
    size = models.PositiveIntegerField(verbose_name="Taille")
    content_type = models.CharField(max_length=100, blank=True, verbose_name="Type de contenu")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dat_section_attachments",
        verbose_name="Déposé par",
    )
    uploaded_by_display = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")

    class Meta:
        db_table = "dat_section_attachment"
        ordering = ["-created_at", "-id"]
        verbose_name = "Pièce jointe de section"
        verbose_name_plural = "Pièces jointes de section"

    def __str__(self) -> str:
        return f"{self.section.dat.reference} - {self.display_name}"

    def uploader_name(self) -> str:
        if self.uploaded_by_display:
            return self.uploaded_by_display
        if self.uploaded_by:
            full_name = self.uploaded_by.get_full_name()
            if full_name:
                return full_name
            return self.uploaded_by.get_username()
        return "Système"


class DATPartEntryType(models.TextChoices):
    TEXT = "text", "Texte"
    LONG_TEXT = "long_text", "Texte long"
    INTEGER = "integer", "Nombre entier"
    DECIMAL = "decimal", "Nombre décimal"
    DATE = "date", "Date"
    BOOLEAN = "boolean", "Booléen"
    JSON = "json", "JSON"
    URL = "url", "Lien"
    REPEATER = "repeater", "Tableau dynamique"


class DATPart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sub_section = models.ForeignKey(
        DATSubSection,
        on_delete=models.CASCADE,
        related_name="parts",
        verbose_name="Sous-section",
    )
    key = models.SlugField(max_length=100, verbose_name="Clé")
    label = models.CharField(max_length=200, verbose_name="Libellé")
    data_type = models.CharField(
        max_length=20,
        choices=DATPartEntryType.choices,
        default=DATPartEntryType.TEXT,
        verbose_name="Type de donnée",
    )
    required = models.BooleanField(default=False, verbose_name="Obligatoire")
    order = models.PositiveIntegerField(default=0, verbose_name="Ordre")
    config = models.JSONField(blank=True, null=True, verbose_name="Configuration")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        db_table = "dat_part"
        ordering = ["order", "id"]
        unique_together = (("sub_section", "key"),)
        verbose_name = "Partie de DAT"
        verbose_name_plural = "Parties de DAT"

    def __str__(self) -> str:
        return (
            f"{self.sub_section.section.dat.reference} - {self.sub_section.section.title} - "
            f"{self.sub_section.title} - {self.label}"
        )

    def _get_current_entry(self):
        entry = getattr(self, "_current_entry_cache", None)
        if entry is not None:
            return entry
        prefetched = getattr(self, "_prefetched_objects_cache", None)
        if prefetched and "entries" in prefetched:
            entries = prefetched["entries"] or []
            entries = sorted(
                entries,
                key=lambda item: (
                    item.updated_at or datetime.min.replace(tzinfo=timezone.utc),
                    item.pk or 0,
                ),
                reverse=True,
            )
            entry = entries[0] if entries else None
        else:
            entry = self.entries.order_by("-updated_at", "-id").first()
        self._current_entry_cache = entry
        return entry

    def _set_entry_cache(self, entry):
        self._current_entry_cache = entry

    @property
    def value(self):
        entry = self._get_current_entry()
        if entry is None:
            return None
        return entry.resolved_value

    @value.setter
    def value(self, new_value):
        self.update_value(new_value)

    def form_field_name(self) -> str:
        return f"entry_{self.pk or self.key}"

    def initial_value(self):
        value = self.value
        if value in (None, ""):
            return None
        if self.data_type == DATPartEntryType.BOOLEAN:
            return bool(value)
        if self.data_type == DATPartEntryType.INTEGER:
            try:
                return int(value)
            except (TypeError, ValueError):
                return value
        if self.data_type == DATPartEntryType.DECIMAL:
            try:
                return Decimal(str(value))
            except (TypeError, InvalidOperation):
                return value
        if self.data_type == DATPartEntryType.DATE:
            if isinstance(value, date):
                return value
            if isinstance(value, str):
                try:
                    return date.fromisoformat(value)
                except ValueError:
                    return value
        return value

    def prepare_value(self, value):
        if value in (None, ""):
            return None
        config = self.config or {}
        if config.get("multiple") and isinstance(value, (list, tuple)):
            return list(value)
        if self.data_type == DATPartEntryType.BOOLEAN:
            return bool(value)
        if self.data_type == DATPartEntryType.INTEGER:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if self.data_type == DATPartEntryType.DECIMAL:
            if isinstance(value, Decimal):
                return str(value)
            try:
                return str(Decimal(str(value)))
            except (TypeError, InvalidOperation):
                return None
        if self.data_type == DATPartEntryType.DATE:
            if isinstance(value, date):
                return value.isoformat()
            return str(value)
        if self.data_type == DATPartEntryType.REPEATER:
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    return []
            else:
                parsed = value
            if isinstance(parsed, list):
                return parsed
            return []
        return value

    def update_value(self, prepared):
        payload = DATPartPayload.get_or_create_for_value(prepared)
        entry = self._get_current_entry()
        if entry is None:
            entry = DATPartEntry.objects.create(
                part=self,
                payload=payload,
            )
        else:
            entry.payload = payload
            entry.save(update_fields=["payload", "updated_at"])
        self._set_entry_cache(entry)
        return entry

    def render_value(self, value):
        if value in (None, "", []):
            return ""
        config = self.config or {}
        choices = config.get("choices") if isinstance(config, dict) else None
        if choices:
            choice_map = {item.get("value"): item.get("label", item.get("value")) for item in choices}
            if config.get("multiple") and isinstance(value, (list, tuple)):
                return ", ".join(str(choice_map.get(item, item)) for item in value)
            return str(choice_map.get(value, value))
        if self.data_type == DATPartEntryType.BOOLEAN:
            return "Oui" if bool(value) else "Non"
        if self.data_type == DATPartEntryType.DATE:
            if hasattr(value, "isoformat"):
                return value.isoformat()
            return str(value)
        if self.data_type == DATPartEntryType.INTEGER:
            return str(value)
        if self.data_type == DATPartEntryType.DECIMAL:
            return str(value)
        if self.data_type == DATPartEntryType.JSON:
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, indent=2)
            return str(value)
        if self.data_type == DATPartEntryType.REPEATER:
            if isinstance(value, list):
                return value
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    return []
                if isinstance(parsed, list):
                    return parsed
            return []
        return str(value)

    def formatted_value(self) -> str:
        return self.render_value(self.value)


class DATPartEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payload = models.ForeignKey(
        "DATPartPayload",
        on_delete=models.PROTECT,
        related_name="entries",
        null=True,
        blank=True,
        verbose_name="Payload dédupliqué",
    )
    part = models.ForeignKey(
        DATPart,
        on_delete=models.CASCADE,
        related_name="entries",
        verbose_name="Partie",
        db_index=False,
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        db_table = "dat_part_entry"
        ordering = ["-updated_at", "-id"]
        verbose_name = "Valeur de partie de DAT"
        verbose_name_plural = "Valeurs de parties de DAT"
        indexes = [
            models.Index(fields=["part"], name="dat_part_entry_part_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.part}"

    @property
    def resolved_value(self):
        if self.payload_id:
            return getattr(self.payload, "data", None)
        return None

    @property
    def value(self):  # backwards compatibility
        return self.resolved_value

    @value.setter
    def value(self, new_value):
        payload = DATPartPayload.get_or_create_for_value(new_value)
        self.payload = payload


class DATPartPayload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hash = models.CharField(max_length=64, unique=True, db_index=True)
    data = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "dat_part_payload"
        ordering = ["hash"]

    def __str__(self) -> str:
        return self.hash

    def save(self, *args, **kwargs):
        # Payloads are immutable once created to avoid breaking entries that share them.
        if self.pk:
            existing = type(self).objects.filter(pk=self.pk).values_list("data", flat=True).first()
            if existing is not None:
                self.data = existing
        super().save(*args, **kwargs)

    @staticmethod
    def _normalize_for_hash(value) -> str:
        if value is None:
            return "null"
        try:
            return json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            )
        except (TypeError, ValueError):
            return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _coerce_json_value(value):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def get_or_create_for_value(cls, value):
        if value in (None, "", [], {}, ()):
            return None
        safe_value = cls._coerce_json_value(value)
        normalized = cls._normalize_for_hash(safe_value)
        payload_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        obj, _ = cls.objects.get_or_create(hash=payload_hash, defaults={"data": safe_value})
        return obj
