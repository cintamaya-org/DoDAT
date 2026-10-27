# SPDX-License-Identifier: AGPL-3.0-only

"""Shared object authorization for editor, DAT, and export entry points."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from dat.models import DAT
from dat.permissions import (
    filter_dat_queryset_for_user,
    user_is_assigned_to_section,
    user_is_dat_admin,
    user_is_dat_admin_for_dat,
)
from workflows.services import workflow_has_capability


def diagram_queryset_for_user(model, user):
    queryset = model.objects.all()
    if not getattr(user, "is_authenticated", False):
        return queryset.none()
    if user_is_dat_admin(user):
        return queryset
    visible_dats = filter_dat_queryset_for_user(DAT.objects.all(), user).values("pk")
    return queryset.filter(
        Q(dat_id__in=visible_dats) | Q(dat__isnull=True, owner_id=user.pk)
    ).distinct()


def get_diagram_for_user(model, user, **lookup):
    try:
        return get_object_or_404(diagram_queryset_for_user(model, user), **lookup)
    except (ValidationError, ValueError, TypeError):
        raise Http404("Diagram not found.") from None


def require_section_write(section, user):
    dat = section.dat
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied
    if workflow_has_capability(dat, "terminal"):
        raise PermissionDenied
    if not (
        user_is_dat_admin_for_dat(dat, user)
        or user_is_assigned_to_section(section, user)
    ):
        raise PermissionDenied


def require_diagram_write(diagram, user):
    # Read denial stays indistinguishable from a nonexistent object.
    get_diagram_for_user(type(diagram), user, pk=diagram.pk)
    if diagram.dat_id is None:
        if diagram.owner_id != getattr(user, "pk", None) and not user_is_dat_admin(user):
            raise PermissionDenied
        return diagram
    if workflow_has_capability(diagram.dat, "terminal"):
        raise PermissionDenied
    if user_is_dat_admin_for_dat(diagram.dat, user):
        return diagram
    if not any(
        user_is_assigned_to_section(section, user)
        for section in diagram.sections.filter(dat_id=diagram.dat_id)
    ):
        raise PermissionDenied
    return diagram


@transaction.atomic
def bind_diagram(diagram, section, user):
    """Attach an owned/administered source without sharing it across DATs."""
    require_section_write(section, user)
    diagram = get_diagram_for_user(type(diagram), user, pk=diagram.pk)
    diagram = type(diagram).objects.select_for_update().get(pk=diagram.pk)
    if diagram.dat_id is not None and diagram.dat_id != section.dat_id:
        raise PermissionDenied("Copy this diagram before attaching it to another DAT.")
    if not (
        diagram.owner_id == getattr(user, "pk", None)
        or (diagram.dat_id and user_is_dat_admin_for_dat(diagram.dat, user))
        or user_is_dat_admin(user)
    ):
        raise PermissionDenied
    if diagram.dat_id is None:
        diagram.dat_id = section.dat_id
        diagram.save(update_fields=["dat"])
    diagram.sections.add(section)
    return diagram


def get_dat_diagram(model, dat, **lookup):
    """Resolve stored references only inside their containing DAT."""
    if dat is None or getattr(dat, "pk", None) is None:
        raise Http404("Diagram not found.")
    try:
        return get_object_or_404(model.objects.filter(dat_id=dat.pk), **lookup)
    except (ValidationError, ValueError, TypeError):
        raise Http404("Diagram not found.") from None
