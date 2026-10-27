# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from diagrams.references import prepare_diagram_references

from users.models import Role
from workflows.services import workflow_initial_state, workflow_states

from .constants import DAT_PORTEUR_ROLE_SLUG
from .models import Application, DAT, DATParticipant, DATStatus
from .resources import (
    LEGACY_RESOURCE_TYPES,
    RESOURCE_ENTRY_KEY,
    RESOURCE_SECTION_SLUG,
    RESOURCE_SUB_SECTION_SLUG,
    group_resource_rows,
)
from .sections import sync_dat_sections_if_needed


UserModel = get_user_model()


class DATImportError(Exception):
    """Raised when a DAT JSON payload cannot be imported."""


@dataclass
class DATImportResult:
    dat: DAT
    warnings: List[str]


class DATImportService:
    """Convert a DAT JSON export payload into persisted models."""

    def __init__(self, *, actor=None):
        self.actor = actor
        self._warnings: List[str] = []
        self._user_cache: Dict[Tuple[str, Optional[int]], Optional[UserModel]] = {}
        self._missing_users: set[str] = set()
        self._role_cache: Dict[str, Optional[Role]] = {}
        self._missing_roles: set[str] = set()
        self._missing_sections: set[str] = set()
        self._missing_sub_sections: set[Tuple[str, str]] = set()
        self._missing_parts: set[Tuple[str, str, str]] = set()

    def import_from_payload(self, payload: Dict[str, Any], *, reference_override: str | None = None) -> DATImportResult:
        dat_data = payload.get("dat")
        if not isinstance(dat_data, dict):
            raise DATImportError("Fichier invalide: section \"dat\" manquante.")

        override_reference = (reference_override or "").strip()
        reference = override_reference or (dat_data.get("reference") or "").strip()
        if not reference:
            if override_reference:
                raise DATImportError("La référence du DAT fournie est vide.")
            raise DATImportError("La référence du DAT est absente du fichier importé.")
        if DAT.objects.filter(reference=reference).exists():
            raise DATImportError(f"Un DAT avec la référence « {reference} » existe déjà.")

        title = (dat_data.get("title") or "").strip()
        if not title:
            raise DATImportError("Le titre du DAT est absent du fichier importé.")

        application_payload = payload.get("application")
        application = self._resolve_application(application_payload)
        owner_payload = payload.get("owner")
        owner = self._resolve_user(owner_payload)
        status = self._normalise_status(dat_data.get("status"))

        description = dat_data.get("description") or ""

        with transaction.atomic():
            dat = DAT(
                reference=reference,
                title=title,
                description=description,
                application=application,
                status=status,
                owner=owner,
            )
            dat._history_actor = self.actor  # type: ignore[attr-defined]
            dat._workflow_initial_state = status  # type: ignore[attr-defined]
            dat.save()

            participants_payload = payload.get("participants")
            self._import_participants(dat, participants_payload)
            self._synchronise_owner(dat)

            sections_payload = payload.get("sections")
            self._import_sections(dat, sections_payload)

        return DATImportResult(dat=dat, warnings=list(self._warnings))

    def _resolve_application(self, payload: Any) -> Application:
        if not isinstance(payload, dict):
            raise DATImportError("L'application associée au DAT est absente du fichier importé.")
        app_id = payload.get("id")
        app_code = (payload.get("code") or "").strip()
        application = None
        if app_id:
            application = Application.objects.filter(pk=app_id).first()
        if application is None and app_code:
            application = Application.objects.filter(code=app_code).first()
        if application is None:
            identifier = app_code or app_id or "inconnu"
            raise DATImportError(
                f"Impossible de trouver l'application « {identifier} ». "
                "Veuillez la créer avant d'importer ce DAT."
            )
        return application

    def _resolve_user(self, payload: Any):
        if not isinstance(payload, dict):
            return None
        username = (payload.get("username") or "").strip()
        user_id = payload.get("id")
        cache_key = (username, user_id)
        if cache_key in self._user_cache:
            return self._user_cache[cache_key]
        user = None
        if user_id:
            user = UserModel.objects.filter(pk=user_id).first()
        if user is None and username:
            user = UserModel.objects.filter(username=username).first()
        if user is None and payload.get("email"):
            user = UserModel.objects.filter(email=payload["email"]).first()
        if user is None and username and username not in self._missing_users:
            self._missing_users.add(username)
            self._warn(f"Utilisateur introuvable pour « {username} ». Le participant correspondant a été ignoré.")
        self._user_cache[cache_key] = user
        return user

    def _resolve_role(self, slug: str | None):
        if not slug:
            return None
        if slug in self._role_cache:
            return self._role_cache[slug]
        role = Role.objects.filter(slug=slug).first()
        if role is None and slug not in self._missing_roles:
            self._missing_roles.add(slug)
            self._warn(f"Rôle « {slug} » introuvable. Le participant correspondant a été ignoré.")
        self._role_cache[slug] = role
        return role

    def _normalise_status(self, raw_status: str | None):
        initial_state = workflow_initial_state()
        status = raw_status or initial_state
        mapping = {
            "demande_initiale": DATStatus.NOUVELLE_DEMANDE,
            "validation_referent": DATStatus.EN_COURS,
            "instruction_architecture": DATStatus.EN_COURS,
            "instruction_urbanisme": DATStatus.EN_COURS,
            "analyse_securite": DATStatus.EN_COURS,
            "generation_cartographie": DATStatus.EN_COURS,
            "revue_infra_exploitation": DATStatus.EN_COURS,
            "validation_finale": DATStatus.EN_ATTENTE_DE_REVUE,
            "validation_reserve": DATStatus.RESERVE,
            "dat_refuse": DATStatus.REFUSE,
            "dat_valide": DATStatus.VALIDER,
        }
        valid_states = {state["status"] for state in workflow_states()}
        if status in valid_states:
            return status
        if status in mapping and mapping[status] in valid_states:
            self._warn(
                f"Statut hérité « {status} » converti en « {mapping[status]} » lors de l'import."
            )
            return mapping[status]
        self._warn(f"Statut inconnu « {status} ». Utilisation du statut initial par défaut.")
        return initial_state

    def _import_participants(self, dat: DAT, payload: Any):
        if not isinstance(payload, (list, tuple)):
            return
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            role_slug = entry.get("role_slug") or ((entry.get("role") or {}).get("slug"))
            role = self._resolve_role(role_slug)
            if role is None:
                continue
            user_payload = entry.get("user")
            user = self._resolve_user(user_payload)
            if user is None:
                continue
            DATParticipant.objects.create(dat=dat, role=role, user=user)

    def _synchronise_owner(self, dat: DAT):
        if dat.owner_id:
            return
        porteur = (
            dat.participants.select_related("user", "role")
            .filter(role__slug=DAT_PORTEUR_ROLE_SLUG)
            .first()
        )
        if porteur and porteur.user_id:
            dat.owner_id = porteur.user_id
            dat._history_actor = self.actor  # type: ignore[attr-defined]
            dat.save(update_fields=["owner", "updated_at"])

    def _import_sections(self, dat: DAT, payload: Any):
        if not isinstance(payload, (list, tuple)):
            return
        sync_dat_sections_if_needed(dat)
        section_map = self._build_section_map(dat)
        pending = []
        for section_payload in payload:
            if not isinstance(section_payload, dict):
                continue
            section_slug = section_payload.get("slug")
            section_entry = section_map.get(section_slug)
            if section_entry is None:
                if section_slug and section_slug not in self._missing_sections:
                    self._missing_sections.add(section_slug)
                    self._warn(f"Section « {section_slug} » inconnue. Les données associées ont été ignorées.")
                continue
            sub_section_map = section_entry["sub_sections"]
            for sub_payload in section_payload.get("sub_sections") or []:
                if not isinstance(sub_payload, dict):
                    continue
                sub_slug = sub_payload.get("slug")
                sub_entry = sub_section_map.get(sub_slug)
                if sub_entry is None:
                    key = (section_slug, sub_slug or "")
                    if sub_slug and key not in self._missing_sub_sections:
                        self._missing_sub_sections.add(key)
                        self._warn(
                            f"Sous-section « {section_slug}/{sub_slug} » inconnue. Les données associées ont été ignorées."
                        )
                    continue
                part_map = sub_entry["parts"]
                parts_payload = sub_payload.get("parts") or []
                if (
                    section_slug == RESOURCE_SECTION_SLUG
                    and sub_slug == RESOURCE_SUB_SECTION_SLUG
                    and RESOURCE_ENTRY_KEY in part_map
                ):
                    resource_keys = {*LEGACY_RESOURCE_TYPES, RESOURCE_ENTRY_KEY}
                    values = {
                        part["key"]: part.get("value")
                        for part in parts_payload
                        if isinstance(part, dict) and part.get("key") in resource_keys
                    }
                    if any(key in values for key in LEGACY_RESOURCE_TYPES):
                        try:
                            rows = group_resource_rows(values)
                        except ValueError as exc:
                            raise DATImportError("Les ressources du fichier importé sont invalides.") from exc
                        parts_payload = [
                            part for part in parts_payload
                            if not isinstance(part, dict) or part.get("key") not in resource_keys
                        ]
                        parts_payload.append({"key": RESOURCE_ENTRY_KEY, "value": rows})
                for part_payload in parts_payload:
                    if not isinstance(part_payload, dict):
                        continue
                    part_key = part_payload.get("key")
                    part = part_map.get(part_key)
                    if part is None:
                        marker = (section_slug or "", sub_slug or "", part_key or "")
                        if part_key and marker not in self._missing_parts:
                            self._missing_parts.add(marker)
                            self._warn(
                                f"Champ « {section_slug}/{sub_slug}/{part_key} » introuvable. Valeur ignorée."
                            )
                        continue
                    value = part_payload.get("value")
                    prepared = part.prepare_value(value)
                    # Skip empty values to avoid creating useless entries
                    if prepared in (None, "", [], {}):
                        continue
                    pending.append((part, prepared))
        # An invalid later reference must not cause an earlier source to be read.
        try:
            for part, prepared in pending:
                prepare_diagram_references(part, prepared, self.actor)
            copies = {}
            for part, prepared in pending:
                prepared = prepare_diagram_references(
                    part, prepared, self.actor, attach=True, copies=copies,
                )
                part.update_value(prepared)
        except ValidationError as exc:
            raise DATImportError("Les références de diagrammes du fichier sont invalides ou inaccessibles.") from exc

    def _build_section_map(self, dat: DAT):
        section_map: Dict[str, Dict[str, Any]] = {}
        sections = dat.sections.select_related("metadata").prefetch_related("sub_sections__parts")
        for section in sections:
            sub_map: Dict[str, Dict[str, Any]] = {}
            for sub_section in section.sub_sections.all():
                part_map = {part.key: part for part in sub_section.parts.all()}
                sub_map[sub_section.slug] = {"sub_section": sub_section, "parts": part_map}
            section_map[section.slug] = {"section": section, "sub_sections": sub_map}
        return section_map

    def _warn(self, message: str):
        self._warnings.append(message)
