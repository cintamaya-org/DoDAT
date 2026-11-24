# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from users.models import BusinessDirection, Role

from .models import (
    Application, DAT, DATParticipant, DATSection, DATSectionMetadata,
    DATSectionParticipant, DATSectionResponsible, DATStatus,
)
from .search_filters import SEARCH_FILTER_OPTIONS_LIMIT


class AdvancedSearchFiltersTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(username="filter-admin", is_staff=True)
        cls.member = get_user_model().objects.create_user(username="filter-member")
        cls.direction = BusinessDirection.objects.create(name="Filter Finance", slug="filter-finance")
        cls.other_direction = BusinessDirection.objects.create(name="Filter Operations", slug="filter-operations")
        cls.application = Application.objects.create(
            code="filter-finance", name="Filter Finance App", business_direction=cls.direction,
        )
        cls.other_application = Application.objects.create(
            code="filter-operations", name="Filter Operations App", business_direction=cls.other_direction,
        )
        cls.first = DAT.objects.create(
            reference="FILTER-001", title="First", application=cls.application, owner=cls.staff,
        )
        cls.second = DAT.objects.create(
            reference="FILTER-002", title="Second", application=cls.application, owner=cls.member,
        )
        cls.third = DAT.objects.create(
            reference="FILTER-003", title="Third", application=cls.other_application, owner=cls.staff,
        )
        DAT.objects.filter(pk=cls.first.pk).update(status=DATStatus.EN_COURS)
        DAT.objects.filter(pk=cls.second.pk).update(status=DATStatus.EN_ATTENTE_DE_REVUE)
        DAT.objects.filter(pk=cls.third.pk).update(status=DATStatus.REFUSE)
        cls.url = reverse("dat:search_page")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_application_filter_without_query_matches_both_result_types(self):
        response = self.client.get(self.url, {"application": self.application.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], 3)
        self.assertCountEqual(
            [item["id"] for item in response.context["search_results"]],
            [str(self.application.pk), str(self.first.pk), str(self.second.pk)],
        )
        self.assertFalse(response.context["search_too_short"])
        self.assertContains(response, "Filtres avancés")

    def test_direction_filter_matches_both_result_types(self):
        response = self.client.get(self.url, {"business_direction": self.other_direction.pk})
        self.assertCountEqual(
            [item["id"] for item in response.context["search_results"]],
            [str(self.other_application.pk), str(self.third.pk)],
        )

    def test_statuses_use_or_and_other_filters_use_and(self):
        response = self.client.get(self.url, {
            "applications": "0", "dats": "1", "application": self.application.pk,
            "business_direction": self.direction.pk,
            "status": [DATStatus.EN_COURS, DATStatus.EN_ATTENTE_DE_REVUE],
        })
        self.assertCountEqual(
            [item["id"] for item in response.context["search_results"]],
            [str(self.first.pk), str(self.second.pk)],
        )
        params = parse_qs(response.context["base_querystring"])
        self.assertCountEqual(params["status"], [DATStatus.EN_COURS, DATStatus.EN_ATTENTE_DE_REVUE])
        self.assertEqual(params["applications"], ["0"])

    def test_owner_filter_combines_with_application(self):
        response = self.client.get(self.url, {
            "applications": "0", "dats": "1", "application": self.application.pk, "owner": self.member.pk,
        })
        self.assertEqual([item["id"] for item in response.context["search_results"]], [str(self.second.pk)])

    def test_date_ranges_include_entire_boundary_day_for_both_types(self):
        boundary = timezone.make_aware(datetime(2025, 6, 15, 23, 59, 59))
        DAT.objects.filter(pk=self.first.pk).update(created_at=boundary, updated_at=boundary)
        Application.objects.filter(pk=self.application.pk).update(created_at=boundary, updated_at=boundary)
        response = self.client.get(self.url, {
            "created_from": "2025-06-15", "created_to": "2025-06-15",
            "updated_from": "2025-06-15", "updated_to": "2025-06-15",
        })
        self.assertCountEqual(
            [item["id"] for item in response.context["search_results"]],
            [str(self.first.pk), str(self.application.pk)],
        )

    def test_invalid_filters_show_errors_without_running_unfiltered_search(self):
        for params in (
            {"application": "not-a-uuid"},
            {"business_direction": "not-a-uuid"},
            {"owner": "not-a-uuid"},
            {"status": "unknown"},
            {"created_from": "not-a-date"},
            {"updated_to": "2025-02-30"},
            {"created_from": "2025-06-16", "created_to": "2025-06-15"},
            {"updated_from": "2025-06-16", "updated_to": "2025-06-15"},
        ):
            with self.subTest(params=params):
                response = self.client.get(self.url, {"q": "FILTER", **params})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["filter_form"].errors)
                self.assertEqual(response.context["search_results"], [])
                self.assertContains(response, 'role="alert"')

    def test_mine_includes_direct_involvement_without_duplicates(self):
        role, _ = Role.objects.get_or_create(slug="filter-role", defaults={"name": "Filter Role"})
        DATParticipant.objects.create(dat=self.second, user=self.staff, role=role)
        DAT.objects.filter(pk=self.third.pk).update(owner=self.member)
        responsible_dat = DAT.objects.create(
            reference="FILTER-005", title="Section responsible", application=self.application, owner=self.member,
        )
        for dat, assignment_model in (
            (self.second, DATSectionResponsible), (self.third, DATSectionParticipant),
            (responsible_dat, DATSectionResponsible),
        ):
            metadata = DATSectionMetadata.objects.create(title=dat.reference, slug=dat.reference.lower())
            section = DATSection.objects.create(dat=dat, metadata=metadata)
            assignment_model.objects.create(dat=dat, section=section, user=self.staff)
        unassigned = DAT.objects.create(
            reference="FILTER-004", title="Unassigned", application=self.application, owner=self.member,
        )
        response = self.client.get(self.url, {"applications": "0", "dats": "1", "mine": "on"})
        ids = [item["id"] for item in response.context["search_results"]]
        self.assertCountEqual(ids, [str(dat.pk) for dat in (self.first, self.second, self.third, responsible_dat)])
        self.assertNotIn(str(unassigned.pk), ids)

    def test_dat_filters_do_not_filter_application_results(self):
        response = self.client.get(self.url, {
            "application": self.application.pk, "status": DATStatus.REFUSE, "owner": self.staff.pk,
        })
        self.assertEqual([item["id"] for item in response.context["search_results"]], [str(self.application.pk)])

    def test_dat_filters_are_ignored_when_dat_search_disabled(self):
        response = self.client.get(self.url, {
            "applications": "1", "dats": "0", "q": "filter-finance",
            "status": "invalid", "owner": "invalid", "mine": "on",
        })
        self.assertFalse(response.context["filter_form"].errors)
        self.assertEqual([item["id"] for item in response.context["search_results"]], [str(self.application.pk)])
        self.assertEqual(response.context["active_filters"], [])

    def test_filters_cannot_expand_visibility_or_reveal_private_choices(self):
        self.client.force_login(self.member)
        response = self.client.get(self.url, {"status": [DATStatus.EN_COURS, DATStatus.EN_ATTENTE_DE_REVUE]})
        self.assertCountEqual(
            [item["id"] for item in response.context["search_results"]],
            [str(self.application.pk), str(self.second.pk)],
        )
        for field, private_id in (
            ("application", self.other_application.pk),
            ("business_direction", self.other_direction.pk),
            ("owner", self.staff.pk),
        ):
            with self.subTest(field=field):
                options = self.client.get(reverse("dat:search_filter_options", args=[field])).json()
                self.assertNotIn(str(private_id), [item["value"] for item in options["options"]])
                invalid = self.client.get(self.url, {field: private_id})
                self.assertTrue(invalid.context["filter_form"].errors)
                self.assertEqual(invalid.context["search_results"], [])

    def test_chips_remove_one_status_preserve_other_filters_and_reset_page(self):
        response = self.client.get(self.url, {
            "q": "FILTER", "applications": "0", "dats": "1", "application": self.application.pk,
            "status": [DATStatus.EN_COURS, DATStatus.EN_ATTENTE_DE_REVUE], "page": "99999999",
        })
        self.assertEqual(response.status_code, 200)
        chips = response.context["active_filters"]
        status_chip = next(chip for chip in chips if chip["label"] == "Statut du DAT : En cours")
        params = parse_qs(urlsplit(status_chip["url"]).query)
        self.assertEqual(params["status"], [DATStatus.EN_ATTENTE_DE_REVUE])
        self.assertEqual(params["application"], [str(self.application.pk)])
        self.assertEqual(params["applications"], ["0"])
        self.assertNotIn("page", params)

    def test_pagination_keeps_filters_and_counts(self):
        for index in range(21):
            DAT.objects.create(
                reference=f"FILTER-PAGE-{index:02d}", title="Paged", application=self.application, owner=self.member,
            )
        response = self.client.get(self.url, {
            "applications": "0", "dats": "1", "application": self.application.pk, "owner": self.member.pk,
            "page": "2",
        })
        self.assertEqual(response.context["total_count"], 22)
        self.assertEqual(len(response.context["search_results"]), 2)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertContains(response, f"owner={self.member.pk}")

    def test_blank_search_stays_empty_and_short_query_still_validates(self):
        response = self.client.get(self.url, {"page": "999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["search_results"], [])
        response = self.client.get(self.url, {"q": "fi", "application": self.application.pk})
        self.assertTrue(response.context["search_too_short"])
        self.assertEqual(response.context["search_results"], [])

    def test_remote_choices_are_bounded_searchable_and_require_authentication(self):
        for index in range(SEARCH_FILTER_OPTIONS_LIMIT + 1):
            Application.objects.create(
                code=f"bounded-{index:02d}", name=f"Bounded {index:02d}", business_direction=self.direction,
            )
        url = reverse("dat:search_filter_options", args=["application"])
        payload = self.client.get(url).json()
        self.assertEqual(len(payload["options"]), SEARCH_FILTER_OPTIONS_LIMIT)
        self.assertTrue(payload["has_more"])
        selected = self.client.get(url, {"q": "bounded-20"}).json()
        self.assertEqual(len(selected["options"]), 1)
        self.assertIn("bounded-20", selected["options"][0]["label"])
        self.assertEqual(self.client.get(reverse("dat:search_filter_options", args=["unknown"])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
