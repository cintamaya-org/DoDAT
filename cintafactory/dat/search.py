# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from django.db.models.functions import Abs, Least, Length
from django.urls import reverse

from .models import Application, DAT
from .permissions import filter_dat_queryset_for_user
from .search_filters import SearchPageFilters, visible_search_applications


TOPBAR_SEARCH_MIN_QUERY_LENGTH = 3
TOPBAR_SEARCH_MAX_RESULTS = 10


def _to_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class TopbarSearchOptions:
    query: str
    include_applications: bool = True
    include_dats: bool = True
    limit: int = TOPBAR_SEARCH_MAX_RESULTS
    min_length: int = TOPBAR_SEARCH_MIN_QUERY_LENGTH


@dataclass(frozen=True)
class TopbarSearchResult:
    kind: str
    identifier: str
    label: str
    subtitle: str
    url: str
    score: int
    length_delta: int

    def to_payload(self) -> dict:
        return {
            "type": self.kind,
            "id": self.identifier,
            "label": self.label,
            "subtitle": self.subtitle,
            "url": self.url,
        }


class BaseTopbarSearchProvider:
    key = ""

    def search(self, query: str, limit: int) -> list[TopbarSearchResult]:
        raise NotImplementedError

    def count(self, query: str) -> int:
        raise NotImplementedError


class ApplicationTopbarSearchProvider(BaseTopbarSearchProvider):
    key = "applications"

    def __init__(self, user, page_filters: SearchPageFilters | None = None):
        self.user = user
        self.page_filters = page_filters

    def _queryset(self) -> QuerySet[Application]:
        queryset = visible_search_applications(self.user).select_related("business_direction")
        if self.page_filters:
            queryset = self.page_filters.apply(queryset, self.user, dats=False)
        return queryset

    def _matching_queryset(self, query: str) -> QuerySet[Application]:
        return self._queryset().filter(Q(code__icontains=query) | Q(name__icontains=query))

    def count(self, query: str) -> int:
        return self._matching_queryset(query).count()

    def search(self, query: str, limit: int) -> list[TopbarSearchResult]:
        query_length = len(query)
        queryset = (
            self._matching_queryset(query)
            .annotate(
                match_score=Case(
                    When(code__iexact=query, then=Value(320)),
                    When(name__iexact=query, then=Value(300)),
                    When(code__istartswith=query, then=Value(220)),
                    When(name__istartswith=query, then=Value(200)),
                    When(code__icontains=query, then=Value(120)),
                    When(name__icontains=query, then=Value(100)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                code_delta=Abs(Length("code") - Value(query_length)),
                name_delta=Abs(Length("name") - Value(query_length)),
            )
            .annotate(length_delta=Least("code_delta", "name_delta"))
            .order_by("-match_score", "length_delta", "name", "code")[:limit]
        )
        results = []
        for application in queryset:
            results.append(
                TopbarSearchResult(
                    kind="application",
                    identifier=str(application.pk),
                    label=f"{application.code} - {application.name}",
                    subtitle="Application",
                    url=f"/dat/manage/applications/crud/{application.pk}/detail/",
                    score=getattr(application, "match_score", 0) or 0,
                    length_delta=getattr(application, "length_delta", 0) or 0,
                )
            )
        return results


class DATTopbarSearchProvider(BaseTopbarSearchProvider):
    key = "dats"

    def __init__(self, user, page_filters: SearchPageFilters | None = None):
        self.user = user
        self.page_filters = page_filters

    def _queryset(self) -> QuerySet[DAT]:
        base_queryset = DAT.objects.select_related("application")
        queryset = filter_dat_queryset_for_user(base_queryset, self.user)
        if self.page_filters:
            queryset = self.page_filters.apply(queryset, self.user, dats=True)
        return queryset

    def _matching_queryset(self, query: str) -> QuerySet[DAT]:
        return self._queryset().filter(reference__icontains=query)

    def count(self, query: str) -> int:
        return self._matching_queryset(query).count()

    def search(self, query: str, limit: int) -> list[TopbarSearchResult]:
        query_length = len(query)
        queryset = (
            self._matching_queryset(query)
            .annotate(
                match_score=Case(
                    When(reference__iexact=query, then=Value(300)),
                    When(reference__istartswith=query, then=Value(200)),
                    When(reference__icontains=query, then=Value(100)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                length_delta=Abs(Length("reference") - Value(query_length)),
            )
            .order_by("-match_score", "length_delta", "reference")[:limit]
        )
        results = []
        for dat in queryset:
            results.append(
                TopbarSearchResult(
                    kind="dat",
                    identifier=str(dat.pk),
                    label=dat.reference,
                    subtitle=f"DAT - {dat.title}",
                    url=reverse("dat:my_detail", args=[dat.pk]),
                    score=getattr(dat, "match_score", 0) or 0,
                    length_delta=getattr(dat, "length_delta", 0) or 0,
                )
            )
        return results


class TopbarSearchService:
    def __init__(self, user, providers: Iterable[BaseTopbarSearchProvider] | None = None,
                 page_filters: SearchPageFilters | None = None):
        self.user = user
        self.page_filters = page_filters
        self.providers = list(providers) if providers is not None else [
            ApplicationTopbarSearchProvider(user, page_filters),
            DATTopbarSearchProvider(user, page_filters),
        ]

    def search(self, options: TopbarSearchOptions) -> dict:
        query = (options.query or "").strip()
        include_applications = bool(options.include_applications)
        include_dats = bool(options.include_dats)
        limit = min(max(int(options.limit or TOPBAR_SEARCH_MAX_RESULTS), 1), TOPBAR_SEARCH_MAX_RESULTS)
        min_length = max(int(options.min_length or TOPBAR_SEARCH_MIN_QUERY_LENGTH), 1)

        if len(query) < min_length:
            return {
                "query": query,
                "min_length": min_length,
                "limit": limit,
                "too_short": bool(query),
                "filters": {"applications": include_applications, "dats": include_dats},
                "results": [],
            }

        enabled_provider_keys = set()
        if include_applications:
            enabled_provider_keys.add("applications")
        if include_dats:
            enabled_provider_keys.add("dats")

        per_provider_limit = max(limit * 2, limit)
        candidates: list[TopbarSearchResult] = []
        for provider in self.providers:
            if provider.key not in enabled_provider_keys:
                continue
            candidates.extend(provider.search(query=query, limit=per_provider_limit))

        ranked = sorted(
            candidates,
            key=lambda item: (-item.score, item.length_delta, item.kind, item.label.lower()),
        )
        results = [item.to_payload() for item in ranked[:limit]]
        return {
            "query": query,
            "min_length": min_length,
            "limit": limit,
            "too_short": False,
            "filters": {"applications": include_applications, "dats": include_dats},
            "results": results,
        }

    def search_page(self, options: TopbarSearchOptions, page: int, per_page: int) -> dict:
        query = (options.query or "").strip()
        include_applications = bool(options.include_applications)
        include_dats = bool(options.include_dats)
        min_length = max(int(options.min_length or TOPBAR_SEARCH_MIN_QUERY_LENGTH), 1)
        page = max(int(page or 1), 1)
        per_page = max(int(per_page or 1), 1)

        payload = {
            "query": query,
            "min_length": min_length,
            "page": page,
            "per_page": per_page,
            "too_short": False,
            "filters": {"applications": include_applications, "dats": include_dats},
            "total_count": 0,
            "results": [],
        }

        if (query and len(query) < min_length) or (
            not query and not (self.page_filters and self.page_filters.has_filters())
        ):
            payload["too_short"] = bool(query)
            return payload

        enabled_provider_keys = set()
        if include_applications:
            enabled_provider_keys.add("applications")
        if include_dats:
            enabled_provider_keys.add("dats")

        if not enabled_provider_keys:
            return payload

        providers = [provider for provider in self.providers if provider.key in enabled_provider_keys]
        total_count = sum(provider.count(query=query) for provider in providers)
        page = min(page, max((total_count + per_page - 1) // per_page, 1))
        payload["page"] = page
        end_index = page * per_page
        candidates: list[TopbarSearchResult] = []
        for provider in providers:
            candidates.extend(provider.search(query=query, limit=end_index))

        ranked = sorted(
            candidates,
            key=lambda item: (-item.score, item.length_delta, item.kind, item.label.lower()),
        )
        start = (page - 1) * per_page
        stop = start + per_page
        payload["total_count"] = total_count
        payload["results"] = [item.to_payload() for item in ranked[start:stop]]
        return payload
