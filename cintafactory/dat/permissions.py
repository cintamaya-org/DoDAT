# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.db.models import Q, QuerySet

from .constants import DAT_PORTEUR_ROLE_SLUG


def user_can_create_dat_entities(user) -> bool:
    is_role = getattr(user, "is_role", None)
    return bool(
        getattr(user, "is_authenticated", False)
        and callable(is_role)
        and is_role(DAT_PORTEUR_ROLE_SLUG)
    )


def user_can_manage_dat(user) -> bool:
    if user is None:
        return False
    is_role = getattr(user, "is_role", None)
    return bool(
        getattr(user, "is_superuser", False)
        or getattr(user, "is_staff", False)
        or (callable(is_role) and is_role("admin"))
    )


def user_can_change_dat(dat, user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user_can_manage_dat(user):
        return True
    if dat is None or getattr(dat, "owner_id", None) != getattr(user, "pk", None):
        return False
    from workflows.services import workflow_has_capability

    return workflow_has_capability(dat, "owner_editable")


def user_is_dat_admin(user) -> bool:
    """
    Determine whether the given user should bypass DAT visibility restrictions.
    """
    if user is None:
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    is_role = getattr(user, "is_role", None)
    if callable(is_role):
        try:
            if is_role("admin"):
                return True
        except Exception:
            pass
    return False


def user_is_dat_admin_for_dat(dat, user) -> bool:
    """
    DAT-scoped administration rights.
    """
    if user_is_dat_admin(user):
        return True
    if dat is None or user is None or not getattr(user, "is_authenticated", False):
        return False
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    if getattr(dat, "owner_id", None) == user_id:
        return True
    try:
        return dat.dat_admins.filter(user_id=user_id).exists()
    except Exception:
        return False


def user_is_responsible_for_section(dat, section, user, *, participants=None) -> bool:
    """
    Determine whether the user is the responsible (manager) of the business group of
    at least one participant in charge of the given section.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user_is_dat_admin(user):
        return True
    if dat is None or section is None:
        return False
    allowed_role_ids = getattr(section, "_allowed_role_ids_cache", None)
    if allowed_role_ids is None:
        try:
            allowed_role_ids = set(section.allowed_roles.values_list("pk", flat=True))
        except Exception:
            allowed_role_ids = set()
    elif not allowed_role_ids:
        try:
            allowed_role_ids = set(section.allowed_roles.values_list("pk", flat=True))
        except Exception:
            allowed_role_ids = set()
    section._allowed_role_ids_cache = allowed_role_ids
    if not allowed_role_ids:
        allowed_role_ids = None
    if participants is None:
        try:
            participants = list(
                dat.participants.select_related("user__business_group__responsible").all()
            )
        except Exception:
            participants = []
    user_id = getattr(user, "id", None)
    for participant in participants:
        if allowed_role_ids is not None and getattr(participant, "role_id", None) not in allowed_role_ids:
            continue
        assignee = getattr(participant, "user", None)
        group = getattr(assignee, "business_group", None) if assignee is not None else None
        if group is None:
            continue
        if getattr(group, "responsible_id", None) == user_id:
            return True
    return False


def user_is_assigned_to_section(section, user) -> bool:
    """
    Strict section assignment check.

    A user is considered assigned only when explicitly set as:
    - section responsible, or
    - section participant.
    """
    if section is None or user is None or not getattr(user, "is_authenticated", False):
        return False
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    try:
        responsible_assignment = getattr(section, "responsible_assignment", None)
    except Exception:
        responsible_assignment = None
    if getattr(responsible_assignment, "user_id", None) == user_id:
        return True
    try:
        participant_assignment = getattr(section, "participant_assignment", None)
    except Exception:
        participant_assignment = None
    if getattr(participant_assignment, "user_id", None) == user_id:
        return True
    return False


def user_can_update_section_status(dat, section, user, *, participants=None) -> bool:
    """
    Determine whether the user can update the status of a section.
    Only explicitly assigned users can update status.
    """
    if section is None or dat is None:
        return False
    can_user_edit = getattr(section, "can_user_edit", None)
    if callable(can_user_edit):
        try:
            return bool(section.can_user_edit(user))
        except Exception:
            return False
    return False


def filter_dat_queryset_for_user(queryset: QuerySet, user) -> QuerySet:
    """
    Restrict a DAT queryset so that non-administrative users only see assigned items.
    """
    if user_is_dat_admin(user):
        return queryset
    if user is None or not getattr(user, "is_authenticated", False):
        return queryset.none()
    model = queryset.model
    owner_field = None
    if model is not None:
        try:
            owner_field = model._meta.get_field("owner")
        except Exception:
            owner_field = None
    if owner_field is None:
        # Fallback to prevent leaking data if schema is unexpected
        return queryset.none()
    return queryset.filter(
        Q(owner=user)
        | Q(participants__user=user)
        | Q(participants__user__business_group__responsible=user)
        | Q(dat_admins__user=user)
    ).distinct()
