# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional, Set

from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import RegexValidator
from django.urls import reverse, reverse_lazy
from django.utils.safestring import mark_safe


from users.models import Role

from .constants import (
    DAT_PORTEUR_ROLE_SLUG,
    DAT_REQUIRED_PARTICIPANT_ROLE_LABELS,
    DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS,
)
from .models import DAT, DATParticipant, DATPart, DATPartEntryType, DATSubSection
from workflows.services import workflow_initial_state, workflow_permission_roles_for_state


class RepeatableTableWidget(forms.Widget):
    template_name = "dat/widgets/repeater.html"

    def __init__(
        self,
        *,
        columns: list[dict],
        min_rows: int | None = None,
        max_rows: int | None = None,
        allow_row_addition: bool = True,
        allow_row_removal: bool = True,
        attrs: Optional[dict] = None,
    ):
        super().__init__(attrs)
        self.columns = columns or []
        self.min_rows = min_rows
        self.max_rows = max_rows
        self.allow_row_addition = allow_row_addition
        self.allow_row_removal = allow_row_removal

    def format_value(self, value):
        if value in (None, "", []):
            return []
        return value

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        serialised_value = value
        if isinstance(serialised_value, str):
            try:
                serialised_value = json.loads(serialised_value)
            except json.JSONDecodeError:
                serialised_value = []
        if not serialised_value:
            serialised_value = []
        context["widget"]["columns"] = self.columns
        context["widget"]["columns_json"] = json.dumps(self.columns, ensure_ascii=False)
        context["widget"]["value"] = serialised_value
        context["widget"]["value_json"] = json.dumps(serialised_value, ensure_ascii=False)
        context["widget"]["min_rows"] = self.min_rows
        context["widget"]["max_rows"] = self.max_rows
        context["widget"]["allow_row_addition"] = self.allow_row_addition
        context["widget"]["allow_row_removal"] = self.allow_row_removal
        return context


class MaterialCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    """
    Custom checkbox group aligned with Material Design markup.
    """

    template_name = "dat/widgets/material_checkbox_select.html"
    option_template_name = "dat/widgets/material_checkbox_option.html"


class MaterialRadioSelect(forms.RadioSelect):
    """
    Custom radio group aligned with Material Design markup.
    """

    template_name = "dat/widgets/material_radio_select.html"
    option_template_name = "dat/widgets/material_radio_option.html"


def _attach_drawio_support(entry: DATPart, widget: RepeatableTableWidget) -> None:
    sub_section = getattr(entry, "sub_section", None)
    section = getattr(sub_section, "section", None)
    dat = getattr(section, "dat", None)
    if not (sub_section and section and dat):
        return
    columns = widget.columns or []
    has_drawio_column = any(isinstance(col, dict) and col.get("drawio") for col in columns)
    if not has_drawio_column:
        return
    attrs = widget.attrs or {}
    attrs["data_drawio_create_url"] = reverse("dat:schema_create_diagram", args=[dat.pk])
    attrs["data_drawio_section_slug"] = section.slug
    attrs["data_drawio_sub_section_slug"] = sub_section.slug
    placeholder_uuid = uuid.UUID(int=0)
    placeholder_segment = f"/{placeholder_uuid}/"
    edit_template = reverse("diagrams:edit", kwargs={"pk": placeholder_uuid}).replace(
        placeholder_segment, "/{id}/"
    )
    detail_template = reverse("diagrams:detail", kwargs={"pk": placeholder_uuid}).replace(
        placeholder_segment, "/{id}/"
    )
    attrs["data_drawio_edit_template"] = edit_template
    attrs["data_drawio_detail_template"] = detail_template
    import_template = reverse("diagrams:import_xml", kwargs={"pk": placeholder_uuid}).replace(
        placeholder_segment, "/{id}/"
    )
    export_template = reverse("diagrams:export_xml", kwargs={"pk": placeholder_uuid}).replace(
        placeholder_segment, "/{id}/"
    )
    attrs["data_drawio_import_template"] = import_template
    attrs["data_drawio_export_template"] = export_template
    attrs["data_likec4_export_template"] = reverse("diagrams:likec4_export")
    attrs["data_likec4_views_template"] = reverse("diagrams:likec4_views")
    attrs["data_likec4_import_url"] = reverse("diagrams:likec4_import")
    attrs["data_likec4_png_public_prefix"] = reverse("diagrams:likec4_png")
    # Mark every Draw.io-enabled repeater so the bulk import UI can attach to it.
    attrs["data_schema_repeater"] = "true"
    widget.attrs = attrs


class DATForm(forms.ModelForm):
    participant_field_prefix = "participant_"

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        self._participant_roles: Dict[str, Role] = {}
        self._participant_field_names: Dict[str, str] = {}
        self._participant_labels: Dict[str, str] = {}
        self._participant_field_order: List[str] = []
        self._pending_participant_assignments: Optional[Dict[str, object]] = None
        self._roles_without_users: List[str] = []
        self._required_roles: Set[str] = self._determine_required_roles()

        if "application" in self.fields:
            refresh_url = reverse_lazy("dat:application_options")
            self.fields["application"].help_text = mark_safe(
                '<div class="application-field-actions">'
                '<a class="btn waves-effect waves-light cinta-btn-primary" '
                'href="/dat/manage/applications/crud/add/" target="_blank" rel="noopener">'
                '<i class="material-icons left" aria-hidden="true">add_circle</i>Creer une application</a>'
                f'<button type="button" '
                f'class="btn waves-effect waves-light cinta-btn-primary application-refresh-btn" '
                f'data-refresh-url="{refresh_url}">'
                '<i class="material-icons left" aria-hidden="true">refresh</i>Actualiser la liste</button>'
                "</div>"
            )

        self._initialise_participant_fields()

        if "owner" in self.fields:
            self.fields["owner"].required = False
            self.fields["owner"].disabled = True
            self.fields["owner"].help_text = (
                "(TMP) Le porteur de la demande determine automatiquement le responsable."
            )
            porteur_initial = None
            porteur_field_name = self._participant_field_names.get(DAT_PORTEUR_ROLE_SLUG)
            if porteur_field_name and porteur_field_name in self.initial:
                porteur_initial = self.initial[porteur_field_name]
            elif self.instance.pk and self.instance.owner_id:
                porteur_initial = self.instance.owner
            elif self.user is not None:
                porteur_initial = self.user
            if porteur_initial is not None:
                self.initial.setdefault("owner", porteur_initial)
        if "status" in self.fields:
            self.fields["status"].disabled = True
            self.fields["status"].help_text = "(TMP) Le statut est defini automatiquement en fonction de l'avancement."
        if "secure_export_requires_dual_admin_approval" in self.fields:
            self.fields["secure_export_requires_dual_admin_approval"].label = (
                "Sécurité double approbation pour l'export du DAT"
            )
            self.fields["secure_export_requires_dual_admin_approval"].help_text = (
                "Si activé, 2 administrateurs DAT doivent approuver avant export JSON/PDF."
            )

    @classmethod
    def participant_field_name(cls, role_slug: str) -> str:
        safe_slug = role_slug.replace("-", "_")
        return f"{cls.participant_field_prefix}{safe_slug}"

    def _determine_required_roles(self) -> Set[str]:
        current_status = getattr(self.instance, "status", None)
        if not current_status:
            current_status = workflow_initial_state()
        required = set(workflow_permission_roles_for_state(current_status))
        required.add(DAT_PORTEUR_ROLE_SLUG)
        return required

    def _initialise_participant_fields(self) -> None:
        UserModel = get_user_model()
        roles = Role.objects.filter(slug__in=DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS)
        role_map = {role.slug: role for role in roles}

        existing_participants: Dict[str, DATParticipant] = {}
        if self.instance.pk:
            for participant in self.instance.participants.select_related("role", "user"):
                role = participant.role
                if role and role.slug in DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS:
                    existing_participants.setdefault(role.slug, participant)

        for slug in DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS:
            field_name = self.participant_field_name(slug)
            role = role_map.get(slug)
            label = role.name if role else DAT_REQUIRED_PARTICIPANT_ROLE_LABELS.get(slug, slug)
            queryset = UserModel.objects.filter(role__slug=slug).order_by("username")
            extra_user_ids = set()
            if slug == DAT_PORTEUR_ROLE_SLUG and self.user is not None:
                extra_user_ids.add(self.user.pk)
            existing = existing_participants.get(slug)
            if existing and existing.user_id:
                extra_user_ids.add(existing.user_id)
            if slug == DAT_PORTEUR_ROLE_SLUG and self.instance.pk and self.instance.owner_id:
                extra_user_ids.add(self.instance.owner_id)
            if extra_user_ids:
                queryset = queryset | UserModel.objects.filter(pk__in=extra_user_ids)
                queryset = queryset.distinct()
            is_required = slug in self._required_roles

            field = forms.ModelChoiceField(
                queryset=queryset,
                required=is_required,
                label=label,
                help_text="Selectionnez l'utilisateur responsable pour ce role.",
            )
            if not is_required:
                field.empty_label = "---------"
            else:
                field.empty_label = None

            if not queryset.exists():
                if is_required:
                    self._roles_without_users.append(slug)
                    field.help_text = "Aucun utilisateur disponible pour ce role."
                field.required = False

            if slug == DAT_PORTEUR_ROLE_SLUG:
                field.required = False
                field.empty_label = None
                field.disabled = True
                field.help_text = "Le porteur de la demande correspond automatiquement au createur du DAT."

            self.fields[field_name] = field
            self._participant_field_names[slug] = field_name
            self._participant_labels[slug] = label
            self._participant_field_order.append(field_name)
            if role:
                self._participant_roles[slug] = role

            participant = existing_participants.get(slug)
            if participant:
                self.initial[field_name] = participant.user
            elif slug == DAT_PORTEUR_ROLE_SLUG:
                if self.instance.pk and self.instance.owner_id:
                    self.initial[field_name] = self.instance.owner
                elif self.user is not None:
                    self.initial[field_name] = self.user

    def participant_field_names(self) -> List[str]:
        return list(self._participant_field_order)

    def clean(self):
        cleaned_data = super().clean()

        porteur_field_name = self._participant_field_names.get(DAT_PORTEUR_ROLE_SLUG)
        if porteur_field_name and DAT_PORTEUR_ROLE_SLUG not in self._roles_without_users:
            porteur_value = cleaned_data.get(porteur_field_name)
            if porteur_field_name in self.data:
                raw_value = self.data.get(porteur_field_name)
                if raw_value not in (None, ""):
                    try:
                        selected = self.fields[porteur_field_name].queryset.get(pk=raw_value)
                        if porteur_value is None or getattr(porteur_value, "pk", None) != selected.pk:
                            porteur_value = selected
                            cleaned_data[porteur_field_name] = selected
                    except Exception:
                        pass
            if porteur_value is None:
                if self.instance.pk and self.instance.owner_id:
                    cleaned_data[porteur_field_name] = self.instance.owner
                elif self.user is not None:
                    cleaned_data[porteur_field_name] = self.user

        missing_roles: List[str] = []
        for slug, field_name in self._participant_field_names.items():
            if slug not in self._required_roles:
                continue
            if slug in self._roles_without_users:
                continue
            if field_name in self.errors:
                continue
            if cleaned_data.get(field_name) is None:
                missing_roles.append(slug)

        for slug in missing_roles:
            field_name = self._participant_field_names[slug]
            label = self._participant_labels.get(slug, slug)
            self.add_error(
                field_name,
                f"Selectionnez un utilisateur pour le role {label}.",
            )

        if self._roles_without_users:
            labels = [self._participant_labels.get(slug, slug) for slug in self._roles_without_users]
            self.add_error(
                None,
                "Aucun utilisateur n'est disponible pour les roles suivants : "
                + ", ".join(labels),
            )

        return cleaned_data

    def _collect_participant_assignments(self) -> Dict[str, object]:
        assignments: Dict[str, object] = {}
        for slug, field_name in self._participant_field_names.items():
            user = self.cleaned_data.get(field_name)
            if user is not None:
                assignments[slug] = user
        return assignments

    def save(self, commit=True):
        instance = super().save(commit=False)
        assignments = self._collect_participant_assignments()

        porteur_user = assignments.get(DAT_PORTEUR_ROLE_SLUG)

        if self.user is not None:
            instance._history_actor = self.user  # type: ignore[attr-defined]
        if not instance.pk:
            if self.user is not None and not getattr(instance, "created_by_id", None):
                instance.created_by = self.user
            if porteur_user is not None:
                instance.owner = porteur_user
            elif self.user is not None:
                instance.owner = self.user
            instance.status = workflow_initial_state()
            instance._workflow_initial_state = instance.status  # type: ignore[attr-defined]
        else:
            if porteur_user is not None and porteur_user != instance.owner:
                instance.owner = porteur_user

        if commit:
            instance.save()
            self._save_participants(instance, assignments)
        else:
            self._pending_participant_assignments = assignments
        return instance

    def save_m2m(self):
        super().save_m2m()
        if self._pending_participant_assignments is not None:
            self._save_participants(self.instance, self._pending_participant_assignments)
            self._pending_participant_assignments = None

    def _save_participants(self, instance: DAT, assignments: Dict[str, object]) -> None:
        if not assignments:
            return

        existing_participants = {
            participant.role.slug: participant
            for participant in instance.participants.select_related("role")
            if participant.role and participant.role.slug in self._participant_field_names
        }

        for slug, user in assignments.items():
            role = self._participant_roles.get(slug)
            if role is None:
                continue
            participant = existing_participants.get(slug)
            user_id = getattr(user, "id", None)
            if participant:
                if participant.user_id != user_id:
                    participant.user = user
                    participant.save(update_fields=["user"])
            else:
                DATParticipant.objects.create(dat=instance, role=role, user=user)

        removable_slugs = set(existing_participants) - set(assignments)
        if removable_slugs:
            instance.participants.filter(role__slug__in=removable_slugs).delete()

    class Meta:
        model = DAT
        fields = [
            "reference",
            "title",
            "application",
            "description",
            "status",
            "owner",
            "secure_export_requires_dual_admin_approval",
        ]
        labels = {
            "reference": "Référence",
            "title": "Titre",
        }


def build_dat_part_field(entry: DATPart) -> forms.Field:
    """
    Construct a Django form field matching the DAT part configuration.
    """
    config = entry.config or {}
    help_text = config.get("help_text")
    required = entry.required
    field_kwargs = {"label": entry.label, "required": required, "help_text": help_text}

    choices = None
    widget_type = None
    multiple_choices = False
    if isinstance(config, dict):
        choices = config.get("choices")
        widget_type = config.get("widget")
        multiple_choices = bool(config.get("multiple"))
    if choices:
        choice_list = [
            (item.get("value"), item.get("label", item.get("value")))
            for item in choices
        ]
        if multiple_choices:
            widget = MaterialCheckboxSelectMultiple() if widget_type in ("checkbox", "checkboxes") else None
            return forms.MultipleChoiceField(choices=choice_list, widget=widget, **field_kwargs)
        widget = MaterialRadioSelect() if widget_type == "radio" else None
        return forms.ChoiceField(choices=choice_list, widget=widget, **field_kwargs)

    if entry.data_type == DATPartEntryType.TEXT:
        max_length = config.get("max_length") if isinstance(config, dict) else None
        if max_length:
            field_kwargs["max_length"] = max_length
        pattern = config.get("pattern") if isinstance(config, dict) else None
        pattern_message = config.get("pattern_message") if isinstance(config, dict) else None
        if pattern:
            validator = RegexValidator(regex=pattern, message=pattern_message or "Format invalide.")
            field_kwargs.setdefault("validators", []).append(validator)
            attrs = {"pattern": pattern, "title": pattern_message or "Format attendu."}
            field_kwargs.setdefault("widget", forms.TextInput(attrs=attrs))
        return forms.CharField(**field_kwargs)
    if entry.data_type == DATPartEntryType.LONG_TEXT:
        rows = config.get("rows") if isinstance(config, dict) else None
        attrs = {"rows": rows} if rows else {"style": "height:160px;"}
        existing_class = attrs.get("class", "").strip()
        extra_class = "materialize-textarea"
        attrs["class"] = f"{existing_class} {extra_class}".strip() if existing_class else extra_class
        field_kwargs["widget"] = forms.Textarea(attrs=attrs)
        return forms.CharField(**field_kwargs)
    if entry.data_type == DATPartEntryType.INTEGER:
        return forms.IntegerField(**field_kwargs)
    if entry.data_type == DATPartEntryType.DECIMAL:
        max_digits = 12
        decimal_places = 2
        if isinstance(config, dict):
            max_digits = config.get("max_digits", max_digits)
            decimal_places = config.get("decimal_places", decimal_places)
        return forms.DecimalField(
            max_digits=max_digits,
            decimal_places=decimal_places,
            **field_kwargs,
        )
    if entry.data_type == DATPartEntryType.DATE:
        field_kwargs["widget"] = forms.DateInput(attrs={"type": "date"})
        return forms.DateField(**field_kwargs)
    if entry.data_type == DATPartEntryType.BOOLEAN:
        field_kwargs["required"] = False
        return forms.BooleanField(**field_kwargs)
    if entry.data_type == DATPartEntryType.JSON:
        return forms.JSONField(**field_kwargs)
    if entry.data_type == DATPartEntryType.URL:
        return forms.URLField(**field_kwargs)
    if entry.data_type == DATPartEntryType.REPEATER:
        columns = config.get("columns", []) if isinstance(config, dict) else []
        min_rows = config.get("min_rows") if isinstance(config, dict) else None
        max_rows = config.get("max_rows") if isinstance(config, dict) else None
        allow_row_addition = config.get("allow_row_addition", True) if isinstance(config, dict) else True
        allow_row_removal = config.get("allow_row_removal", True) if isinstance(config, dict) else True
        widget = RepeatableTableWidget(
            columns=columns,
            min_rows=min_rows,
            max_rows=max_rows,
            allow_row_addition=allow_row_addition,
            allow_row_removal=allow_row_removal,
        )
        _attach_drawio_support(entry, widget)
        return forms.JSONField(
            required=False,
            widget=widget,
            label=entry.label,
            help_text=help_text,
        )
    return forms.CharField(**field_kwargs)


class DATSubSectionForm(forms.Form):
    """
    Form that exposes entries for a single DAT sub-section.
    """

    def __init__(self, sub_section: DATSubSection, *args, user=None, **kwargs):
        self.sub_section = sub_section
        self.user = user
        self.entries: list[dict[str, object]] = []
        super().__init__(*args, **kwargs)
        parts = sub_section.parts.order_by("order", "id")
        for part in parts:
            field_name = part.form_field_name()
            field = build_dat_part_field(part)
            self.fields[field_name] = field
            initial_value = part.initial_value()
            if initial_value is not None:
                self.initial[field_name] = initial_value
            self.entries.append(
                {
                    "entry": part,
                    "field_name": field_name,
                }
            )

    def iter_entries(self):
        for item in self.entries:
            field_name = item["field_name"]
            yield {
                "entry": item["entry"],
                "field": self[field_name],
                "field_name": field_name,
            }

    def save(self):
        if not self.is_valid():
            raise ValueError("Cannot save an invalid form.")
        changes: dict[str, dict[str, str]] = {}
        for entry_info in self.entries:
            entry = entry_info["entry"]
            field_name = entry_info["field_name"]
            if field_name not in self.cleaned_data:
                continue
            new_value = self.cleaned_data[field_name]
            prepared = entry.prepare_value(new_value)
            if prepared == entry.value:
                continue
            before_display = entry.render_value(entry.value)
            entry.update_value(prepared)
            after_display = entry.render_value(entry.value)
            if entry.data_type == DATPartEntryType.REPEATER:
                before_display = json.dumps(before_display or [], ensure_ascii=False)
                after_display = json.dumps(after_display or [], ensure_ascii=False)
            changes[entry.key] = {
                "label": entry.label,
                "part": self.sub_section.title,
                "from": before_display,
                "to": after_display,
            }
        return changes


class DATImportForm(forms.Form):
    data_file = forms.FileField(label="Fichier JSON du DAT")
    reference_override = forms.CharField(
        label="Référence du DAT",
        help_text="Inséré la référence du nouveau DAT",
        required=False,
        widget=forms.TextInput(attrs={"class": "validate"}),
    )

    def clean_data_file(self):
        uploaded = self.cleaned_data.get("data_file")
        if not uploaded:
            return uploaded
        try:
            content = uploaded.read().decode("utf-8")
        except UnicodeDecodeError:
            raise forms.ValidationError("Le fichier doit être encodé en UTF-8.")
        try:
            payload = json.loads(content or "{}")
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Le contenu du fichier n'est pas un JSON valide: {exc}") from exc
        if not isinstance(payload, dict):
            raise forms.ValidationError("Le fichier importé doit contenir un objet JSON.")
        self.cleaned_data["payload"] = payload
        uploaded.seek(0)
        return uploaded

    def clean_reference_override(self):
        reference = (self.cleaned_data.get("reference_override") or "").strip()
        if not reference:
            return ""
        if DAT.objects.filter(reference=reference).exists():
            raise forms.ValidationError(
                f"Un DAT avec la référence « {reference} » existe déjà. Merci d'en saisir une autre."
            )
        return reference

    @property
    def payload(self):
        return self.cleaned_data.get("payload")
