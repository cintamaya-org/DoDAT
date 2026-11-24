# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse

from users.models import BusinessDirection

from .models import Application, DAT, DATParticipant, DATSectionParticipant, DATSectionResponsible, DATStatus
from .permissions import filter_dat_queryset_for_user, user_is_dat_admin


SEARCH_FILTER_OPTIONS_LIMIT = 20
DAT_ONLY_FILTERS = ("status", "owner", "mine")


def visible_search_applications(user):
    applications = Application.objects.all()
    if not user_is_dat_admin(user):
        visible_dats = filter_dat_queryset_for_user(DAT.objects.all(), user)
        applications = applications.filter(pk__in=visible_dats.order_by().values("application_id"))
    return applications


@dataclass(frozen=True)
class SearchPageFilters:
    application: UUID | None = None
    business_direction: UUID | None = None
    status: tuple[str, ...] = ()
    owner: UUID | None = None
    mine: bool = False
    created_from: date | None = None
    created_to: date | None = None
    updated_from: date | None = None
    updated_to: date | None = None

    def has_filters(self):
        return any(vars(self).values())

    def apply(self, queryset, user, *, dats):
        if self.application:
            queryset = queryset.filter(**{"application_id" if dats else "pk": self.application})
        if self.business_direction:
            queryset = queryset.filter(business_direction_id=self.business_direction)
        for field in ("created", "updated"):
            start = getattr(self, f"{field}_from")
            end = getattr(self, f"{field}_to")
            if start:
                queryset = queryset.filter(**{f"{field}_at__date__gte": start})
            if end:
                queryset = queryset.filter(**{f"{field}_at__date__lte": end})
        if dats:
            if self.status:
                queryset = queryset.filter(status__in=self.status)
            if self.owner:
                queryset = queryset.filter(owner_id=self.owner)
            if self.mine:
                queryset = queryset.filter(
                    Q(owner=user)
                    | Q(pk__in=DATParticipant.objects.filter(user=user).order_by().values("dat_id"))
                    | Q(pk__in=DATSectionResponsible.objects.filter(user=user).order_by().values("dat_id"))
                    | Q(pk__in=DATSectionParticipant.objects.filter(user=user).order_by().values("dat_id"))
                )
        return queryset


class SearchApplicationChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.code} — {obj.name}"


class SearchOwnerChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.get_full_name() or obj.get_username()


class SearchPageFilterForm(forms.Form):
    application = SearchApplicationChoiceField(
        label="Application", queryset=Application.objects.none(), required=False, empty_label="Toutes",
    )
    business_direction = forms.ModelChoiceField(
        label="Direction métier", queryset=BusinessDirection.objects.none(), required=False, empty_label="Toutes",
    )
    status = forms.MultipleChoiceField(label="Statut du DAT", choices=DATStatus.choices, required=False)
    owner = SearchOwnerChoiceField(
        label="Porteur du DAT", queryset=get_user_model().objects.none(), required=False, empty_label="Tous",
    )
    mine = forms.BooleanField(label="Mes DAT uniquement", required=False)
    created_from = forms.DateField(label="Créé à partir du", required=False)
    created_to = forms.DateField(label="Créé jusqu’au", required=False)
    updated_from = forms.DateField(label="Fiche mise à jour à partir du", required=False)
    updated_to = forms.DateField(label="Fiche mise à jour jusqu’au", required=False)

    def __init__(self, *args, user, include_dats=True, **kwargs):
        super().__init__(*args, auto_id="search-filter-%s", **kwargs)
        if not include_dats and self.is_bound:
            self.data = self.data.copy()
            for name in DAT_ONLY_FILTERS:
                self.data.pop(name, None)
        visible_dats = filter_dat_queryset_for_user(DAT.objects.all(), user).order_by()
        applications = visible_search_applications(user).order_by()
        self.fields["application"].queryset = applications.order_by("name", "pk")
        self.fields["business_direction"].queryset = BusinessDirection.objects.filter(
            Q(pk__in=applications.values("business_direction_id"))
            | Q(pk__in=visible_dats.values("business_direction_id"))
        ).order_by("name", "pk")
        self.fields["owner"].queryset = get_user_model().objects.filter(
            pk__in=visible_dats.values("owner_id")
        ).order_by("username", "pk")
        for name in ("application", "business_direction", "owner"):
            self.fields[name].widget.attrs.update({
                "class": "browser-default",
                "data-remote-select-url": reverse("dat:search_filter_options", args=[name]),
                "data-remote-select-limit": SEARCH_FILTER_OPTIONS_LIMIT,
                "data-remote-select-placeholder": f"Rechercher : {self.fields[name].label.lower()}",
            })
        for name in ("created_from", "created_to", "updated_from", "updated_to"):
            self.fields[name].input_formats = ["%Y-%m-%d"]
            self.fields[name].widget = forms.DateInput(attrs={"type": "date", "class": "browser-default"}, format="%Y-%m-%d")

    def clean(self):
        cleaned = super().clean()
        for field in ("created", "updated"):
            start, end = cleaned.get(f"{field}_from"), cleaned.get(f"{field}_to")
            if start and end and start > end:
                self.add_error(f"{field}_to", "La date de fin doit être postérieure ou égale à la date de début.")
        return cleaned

    def search_filters(self):
        return SearchPageFilters(**{
            name: (tuple(value) if name == "status" else value.pk if hasattr(value, "pk") else value)
            for name, value in self.cleaned_data.items()
        })

    def limit_rendered_choices(self):
        # Validate against the full authorized queryset, render only selected options.
        # Remaining choices are fetched in bounded batches by remote_select.js.
        for name in ("application", "business_direction", "owner"):
            field = self.fields[name]
            selected = self.cleaned_data.get(name)
            field.choices = [("", field.empty_label)]
            if selected:
                field.choices = [*field.choices, (selected.pk, field.label_from_instance(selected))]
