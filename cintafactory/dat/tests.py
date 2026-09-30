# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import json
from io import BytesIO
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from cintafactory.logging.logging_utils import bind_request_context, clear_request_context

from diagrams.models import DrawIODiagram
from users.models import BusinessDirection, BusinessGroup, Role, TechnicalDirection

from .constants import (
    DAT_PORTEUR_ROLE_SLUG,
    DAT_REQUIRED_PARTICIPANT_ROLE_LABELS,
    DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS,
)
from .exporters import DATExportModelBuilder
from .forms import DATForm
from .models import (
    Application,
    DAT,
    DATAdmin,
    DATExportAccessApproval,
    DATExportAccessEventType,
    DATExportAccessHistory,
    DATExportAccessRequest,
    DATExportAccessRequestStatus,
    DATParticipant,
    DATParticipantType,
    DATPart,
    DATPartEntryType,
    DATSection,
    DATSectionParticipant,
    DATSectionResponsible,
    DATSectionMetadata,
    DATSubSection,
    DATStatus,
    DATHistoryAction,
)
from .sections import SECTION_STATUS_VALIDATED_VALUE, dat_sections_need_sync, sync_dat_sections_if_needed
from .permissions import (
    filter_dat_queryset_for_user,
    user_can_update_section_status,
    user_is_dat_admin,
    user_is_dat_admin_for_dat,
    user_is_responsible_for_section,
)
from .drawio_parser import _clean_model_xml, dedupe_architecture_rows, extract_drawio_pages, parse_architecture_diagram
from .tasks import _run_pdf_generation
from .utils import dat_pdf_export_exists, dat_pdf_export_modified_at, format_user_display, open_dat_pdf_export
from workflows.models import UserNotification

def get_default_business_direction():
    direction, _ = BusinessDirection.objects.get_or_create(
        slug="direction-metier-test",
        defaults={"name": "Direction Métier Test"},
    )
    return direction


def get_secondary_business_direction():
    direction, _ = BusinessDirection.objects.get_or_create(
        slug="direction-metier-secondaire-test",
        defaults={"name": "Direction Métier Secondaire Test"},
    )
    return direction


def get_default_technical_direction():
    direction, _ = TechnicalDirection.objects.get_or_create(
        slug="direction-technique-test",
        defaults={"name": "Direction Technique Test"},
    )
    return direction


def create_role(slug: str, name: str) -> Role:
    direction = get_default_technical_direction()
    role, _ = Role.objects.get_or_create(
        slug=slug,
        defaults={"name": name, "technical_direction": direction},
    )
    updates = {}
    if role.name != name:
        updates["name"] = name
    if role.slug != slug:
        updates["slug"] = slug
    if role.technical_direction_id != direction.id:
        updates["technical_direction"] = direction
    if updates:
        Role.objects.filter(pk=role.pk).update(**updates)
        role.refresh_from_db()
    return role


def ensure_role(slug: str, name: str) -> Role:
    defaults = {"name": name, "technical_direction": get_default_technical_direction()}
    role, created = Role.objects.get_or_create(slug=slug, defaults=defaults)
    if not created and role.technical_direction_id is None:
        role.technical_direction = defaults["technical_direction"]
        role.save(update_fields=["technical_direction"])
    return role


class SmokeTest(TestCase):
    def test_import(self):
        self.assertTrue(DAT)

class DATApplicationRelationTest(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create(username="owner")
        self.business_direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-code",
            name="App Name",
            business_direction=self.business_direction,
        )

    def test_dat_links_to_single_application(self):
        dat = DAT.objects.create(
            reference="DAT-001",
            title="Integration Test",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        self.assertEqual(dat.application, self.application)
        self.assertIn(dat, self.application.dats.all())

    def test_protects_application_from_deletion(self):
        dat = DAT.objects.create(
            reference="DAT-002",
            title="Deletion Test",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
        )
        with self.assertRaisesMessage(ProtectedError, "protected"):
            self.application.delete()
        dat.delete()
        self.application.delete()
        self.assertFalse(Application.objects.filter(pk=self.application.pk).exists())

    def test_dat_inherits_application_business_direction(self):
        dat = DAT.objects.create(
            reference="DAT-003",
            title="Direction Test",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        self.assertEqual(dat.business_direction, self.business_direction)

    def test_dat_ignores_direct_business_direction_on_create(self):
        other_direction = get_secondary_business_direction()
        dat = DAT.objects.create(
            reference="DAT-004",
            title="Direction Create Integrity Test",
            application=self.application,
            business_direction=other_direction,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        self.assertEqual(dat.business_direction, self.business_direction)

    def test_dat_resets_direct_business_direction_change_on_save(self):
        other_direction = get_secondary_business_direction()
        dat = DAT.objects.create(
            reference="DAT-005",
            title="Direction Save Integrity Test",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        dat.business_direction = other_direction
        dat.save()
        dat.refresh_from_db()
        self.assertEqual(dat.business_direction, self.business_direction)

    def test_dat_tracks_application_business_direction_after_application_change(self):
        other_direction = get_secondary_business_direction()
        dat = DAT.objects.create(
            reference="DAT-006",
            title="Direction Application Change Test",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        self.application.business_direction = other_direction
        self.application.save(update_fields=["business_direction"])

        dat.save()
        dat.refresh_from_db()

        self.assertEqual(dat.business_direction, other_direction)

class ApplicationOptionsViewTest(TestCase):
    def setUp(self) -> None:
        self.url = reverse("dat:application_options")
        self.staff = get_user_model().objects.create_user(
            username="manager",
            password="pwd",
            is_staff=True,
        )
        self.role_porteur = create_role("porteur-demande", "Porteur de la demande")
        self.porteur = get_user_model().objects.create_user(
            username="porteur",
            password="pwd",
        )
        self.porteur.role = self.role_porteur
        self.porteur.save()
        direction = get_default_business_direction()
        Application.objects.create(code="app-1", name="App One", business_direction=direction)
        Application.objects.create(code="app-2", name="App Two", business_direction=direction)

    def test_requires_management_rights(self):
        user = get_user_model().objects.create_user(username="regular", password="pwd")
        non_porteur_role = create_role("architecte-technique", "Architecte technique")
        user.role = non_porteur_role
        user.save(update_fields=["role"])
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_porteur_can_access(self):
        self.client.force_login(self.porteur)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_returns_sorted_options(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("options", payload)
        labels = [option["label"] for option in payload["options"]]
        self.assertEqual(labels, sorted(labels))

    def test_refresh_returns_new_applications_without_cache(self):
        self.client.force_login(self.staff)
        first_response = self.client.get(self.url)
        self.assertEqual(first_response.status_code, 200)
        self.assertIn("no-store", first_response["Cache-Control"])
        self.assertNotIn(
            "New application",
            [option["label"] for option in first_response.json()["options"]],
        )

        application = Application.objects.create(
            code="new-application",
            name="New application",
            business_direction=get_default_business_direction(),
        )
        refreshed_response = self.client.get(self.url)

        self.assertEqual(refreshed_response.status_code, 200)
        self.assertIn("no-store", refreshed_response["Cache-Control"])
        self.assertIn(
            {"value": str(application.pk), "label": application.name},
            refreshed_response.json()["options"],
        )

    def test_skips_applications_without_direction(self):
        Application.objects.create(code="app-3", name="Sans direction", business_direction=None)
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        option_labels = [option["label"] for option in payload["options"]]
        self.assertNotIn("Sans direction", option_labels)


class MyApplicationOptionsViewTest(TestCase):
    def setUp(self) -> None:
        self.url = reverse("dat:my_application_options")
        self.owner = get_user_model().objects.create_user(
            username="application-option-owner",
            password="pwd",
        )
        self.other = get_user_model().objects.create_user(
            username="application-option-other",
            password="pwd",
        )
        self.direction = get_default_business_direction()
        self.applications = [
            Application(
                code=f"visible-{index:02d}",
                name=f"Visible application {index:02d}",
                business_direction=self.direction,
            )
            for index in range(35)
        ]
        Application.objects.bulk_create(self.applications)
        DAT.objects.bulk_create(
            [
                DAT(
                    reference=f"DAT-OPTION-{index:02d}",
                    title=f"Option {index:02d}",
                    application=application,
                    business_direction=self.direction,
                    owner=self.owner,
                )
                for index, application in enumerate(self.applications)
            ]
        )
        self.hidden_application = Application.objects.create(
            code="hidden-application",
            name="Hidden application",
            business_direction=self.direction,
        )
        DAT.objects.create(
            reference="DAT-OPTION-HIDDEN",
            title="Hidden",
            application=self.hidden_application,
            owner=self.other,
        )

    def test_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_returns_at_most_thirty_visible_applications(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["options"]), 30)
        self.assertTrue(payload["has_more"])
        self.assertEqual(payload["max_results"], 30)

    def test_search_is_server_side_and_visibility_scoped(self):
        self.client.force_login(self.owner)

        visible_response = self.client.get(self.url, {"q": "visible-34"})
        hidden_response = self.client.get(self.url, {"q": "hidden-application"})

        self.assertEqual(len(visible_response.json()["options"]), 1)
        self.assertEqual(hidden_response.json()["options"], [])

    def test_my_dat_application_filter_accepts_uuid(self):
        selected_application = self.applications[34]
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("dat:my_list"),
            {"application": selected_application.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["object_list"]), 1)
        self.assertEqual(response.context["object_list"][0].application, selected_application)


class TopbarSearchViewTest(TestCase):
    def setUp(self) -> None:
        self.url = reverse("dat:topbar_search")
        self.staff = get_user_model().objects.create_user(
            username="search-admin",
            password="pwd",
            is_staff=True,
        )
        direction = get_default_business_direction()
        self.app_inventory = Application.objects.create(
            code="inventory-core",
            name="Inventory Platform",
            business_direction=direction,
        )
        self.app_billing = Application.objects.create(
            code="billing-suite",
            name="Billing Platform",
            business_direction=direction,
        )
        DAT.objects.create(
            reference="DAT-INV-001",
            title="Inventory Dat",
            application=self.app_inventory,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.staff,
        )
        DAT.objects.create(
            reference="DAT-BILL-001",
            title="Billing Dat",
            application=self.app_billing,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.staff,
        )

    def test_requires_authentication(self):
        response = self.client.get(self.url, {"q": "inventory"})
        self.assertEqual(response.status_code, 302)

    def test_rejects_query_shorter_than_three_chars(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url, {"q": "in"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["too_short"])
        self.assertEqual(payload["results"], [])

    def test_application_filter_matches_code_and_name(self):
        self.client.force_login(self.staff)

        code_response = self.client.get(
            self.url,
            {"q": "inventory", "applications": "1", "dats": "0"},
        )
        payload = code_response.json()
        self.assertEqual(code_response.status_code, 200)
        self.assertFalse(payload["too_short"])
        self.assertTrue(payload["results"])
        self.assertTrue(all(item["type"] == "application" for item in payload["results"]))
        self.assertIn("inventory-core", payload["results"][0]["label"].lower())

        name_response = self.client.get(
            self.url,
            {"q": "billing", "applications": "1", "dats": "0"},
        )
        name_payload = name_response.json()
        labels = [item["label"] for item in name_payload["results"]]
        self.assertTrue(any("Billing Platform" in label for label in labels))

    def test_dat_filter_matches_reference(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {"q": "DAT-INV", "applications": "0", "dats": "1"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["results"])
        self.assertTrue(all(item["type"] == "dat" for item in payload["results"]))
        self.assertIn("DAT-INV-001", [item["label"] for item in payload["results"]])

    def test_returns_only_top_ten_results(self):
        for index in range(12):
            DAT.objects.create(
                reference=f"DAT-SEARCH-{index:02d}",
                title=f"Search Dat {index}",
                application=self.app_inventory,
                status=DATStatus.NOUVELLE_DEMANDE,
                owner=self.staff,
            )
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {"q": "DAT-SEARCH-", "applications": "0", "dats": "1"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 10)


class SearchPageViewTest(TestCase):
    def setUp(self) -> None:
        self.url = reverse("dat:search_page")
        self.staff = get_user_model().objects.create_user(
            username="search-page-admin",
            password="pwd",
            is_staff=True,
        )
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="search-page-app",
            name="Search Page Application",
            business_direction=direction,
        )
        DAT.objects.create(
            reference="DAT-PAGE-000",
            title="Search Page Seed",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.staff,
        )

    def test_requires_authentication(self):
        response = self.client.get(self.url, {"q": "search"})
        self.assertEqual(response.status_code, 302)

    def test_displays_paginated_results(self):
        for index in range(45):
            DAT.objects.create(
                reference=f"DAT-PAGE-{index + 1:03d}",
                title=f"Search Page Dat {index}",
                application=self.application,
                status=DATStatus.NOUVELLE_DEMANDE,
                owner=self.staff,
            )

        self.client.force_login(self.staff)
        first_page = self.client.get(
            self.url,
            {"q": "DAT-PAGE-", "applications": "0", "dats": "1"},
        )
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(len(first_page.context["search_results"]), 20)
        self.assertEqual(first_page.context["total_count"], 46)
        self.assertTrue(first_page.context["page_obj"].has_next())

        third_page = self.client.get(
            self.url,
            {"q": "DAT-PAGE-", "applications": "0", "dats": "1", "page": "3"},
        )
        self.assertEqual(third_page.status_code, 200)
        self.assertEqual(len(third_page.context["search_results"]), 6)
        self.assertFalse(third_page.context["page_obj"].has_next())

    def test_supports_application_only_filter(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {"q": "Search Page Application", "applications": "1", "dats": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["search_results"])
        self.assertTrue(all(item["type"] == "application" for item in response.context["search_results"]))

    def test_requires_at_least_one_filter(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            self.url,
            {"q": "Search", "applications": "0", "dats": "0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filters_empty"])
        self.assertEqual(response.context["search_results"], [])


class DatCreationPermissionTest(TestCase):
    def setUp(self) -> None:
        self.dat_add_url = "/dat/manage/dats/crud/add/"
        self.application_add_url = "/dat/manage/applications/crud/add/"
        self.role_porteur = create_role("porteur-demande", "Porteur de la demande")
        self.role_other = create_role("architecte-technique", "Architecte technique")
        self.porteur = get_user_model().objects.create_user(
            username="porteur-creator",
            password="pwd",
        )
        self.porteur.role = self.role_porteur
        self.porteur.save(update_fields=["role"])
        self.staff = get_user_model().objects.create_user(
            username="staff-editor",
            password="pwd",
            is_staff=True,
        )
        self.staff.role = self.role_other
        self.staff.save(update_fields=["role"])

    def test_staff_cannot_access_dat_creation(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.dat_add_url)
        self.assertEqual(response.status_code, 403)

    def test_porteur_can_access_dat_creation(self):
        self.client.force_login(self.porteur)
        response = self.client.get(self.dat_add_url)
        self.assertEqual(response.status_code, 200)

    def test_staff_cannot_access_application_creation(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.application_add_url)
        self.assertEqual(response.status_code, 403)

    def test_porteur_can_access_application_creation(self):
        self.client.force_login(self.porteur)
        response = self.client.get(self.application_add_url)
        self.assertEqual(response.status_code, 200)


class ApplicationManagementPaginationTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="application-pagination-admin",
            password="pwd",
        )
        self.direction = BusinessDirection.objects.create(
            name="Applications Pagination",
            slug="applications-pagination",
        )
        Application.objects.bulk_create(
            [
                Application(
                    code=f"PAGE-{index:02d}",
                    name=f"Application pagination {index:02d}",
                    business_direction=self.direction,
                )
                for index in range(30)
            ]
        )
        self.client.force_login(self.user)

    def test_application_crud_uses_server_side_pages(self):
        response = self.client.get("/dat/manage/applications/crud/", {"page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["paginator"].count, 30)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertEqual(len(response.context["object_list"]), 5)
        self.assertEqual(response.context["total_applications"], 30)
        self.assertContains(response, "Page 2 sur 2")


class DatAdminListViewTest(TestCase):
    def setUp(self) -> None:
        self.url = reverse("dat:admin_list")
        self.staff = get_user_model().objects.create_user(
            username="staff-user",
            password="pwd",
            is_staff=True,
        )
        self.regular = get_user_model().objects.create_user(
            username="regular-user",
            password="pwd",
        )
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-admin",
            name="Application Admin",
            business_direction=direction,
        )
        DAT.objects.create(
            reference="DAT-ADMIN-1",
            title="Admin DAT 1",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.staff,
        )
        DAT.objects.create(
            reference="DAT-ADMIN-2",
            title="Admin DAT 2",
            application=self.application,
            status=DATStatus.EN_COURS,
            owner=self.regular,
        )

    def test_requires_management_rights(self):
        self.client.force_login(self.regular)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_staff_sees_all_dats(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("DAT-ADMIN-1", content)
        self.assertIn("DAT-ADMIN-2", content)


class DatImportViewTest(TestCase):
    def setUp(self) -> None:
        self.url = reverse("dat:import")
        self.admin = get_user_model().objects.create_user(
            username="import-admin",
            password="pwd",
            is_staff=True,
        )
        self.regular = get_user_model().objects.create_user(username="import-user", password="pwd")
        self.porteur_role = create_role(DAT_PORTEUR_ROLE_SLUG, "Porteur")
        self.porteur = get_user_model().objects.create_user(username="porteur-import", password="pwd")
        self.porteur.role = self.porteur_role
        self.porteur.save(update_fields=["role"])
        self.business_direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-import",
            name="Application Import",
            business_direction=self.business_direction,
        )
        self.sample_part_value = "Architecture importée"

    def _build_payload(self):
        dat = DAT.objects.create(
            reference="DAT-EXPORT-IMPORT",
            title="DAT pour export",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.porteur,
        )
        sync_dat_sections_if_needed(dat)
        DATParticipant.objects.create(dat=dat, role=self.porteur_role, user=self.porteur)
        part = (
            dat.sections.get(metadata__slug="architecture")
            .sub_sections.get(slug="presentation-generale")
            .parts.get(key="presentation_generale")
        )
        part.update_value(part.prepare_value(self.sample_part_value))
        builder = DATExportModelBuilder()
        payload = builder.build(dat)
        payload["dat"]["reference"] = "DAT-IMPORT-001"
        payload["dat"]["title"] = "DAT importé"
        return payload

    def test_requires_management_rights(self):
        self.client.force_login(self.regular)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_imports_dat_from_json(self):
        payload = self._build_payload()
        upload = SimpleUploadedFile(
            "dat.json",
            json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {"data_file": upload})
        self.assertEqual(response.status_code, 302)
        imported = DAT.objects.get(reference=payload["dat"]["reference"])
        self.assertEqual(imported.title, payload["dat"]["title"])
        self.assertEqual(imported.application, self.application)
        self.assertEqual(imported.owner, self.porteur)
        participant_qs = imported.participants.filter(role__slug=DAT_PORTEUR_ROLE_SLUG)
        self.assertEqual(participant_qs.count(), 1)
        part = (
            imported.sections.get(metadata__slug="architecture")
            .sub_sections.get(slug="presentation-generale")
            .parts.get(key="presentation_generale")
        )
        self.assertEqual(part.value, self.sample_part_value)

    def test_allows_reference_override(self):
        payload = self._build_payload()
        DAT.objects.create(
            reference=payload["dat"]["reference"],
            title="Existing DAT",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.porteur,
        )
        upload = SimpleUploadedFile(
            "dat.json",
            json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )
        override_reference = "DAT-IMPORT-NEW"
        self.client.force_login(self.admin)
        response = self.client.post(
            self.url,
            {"data_file": upload, "reference_override": override_reference},
        )
        self.assertEqual(response.status_code, 302)
        imported = DAT.objects.get(reference=override_reference)
        self.assertEqual(imported.title, payload["dat"]["title"])


class DatVisibilityRestrictionTest(TestCase):
    def setUp(self) -> None:
        self.roles = {}
        for slug, label in DAT_REQUIRED_PARTICIPANT_ROLE_LABELS.items():
            self.roles[slug] = ensure_role(slug, label)
        self.owner = get_user_model().objects.create_user(username="owner-user", password="pwd")
        self.other = get_user_model().objects.create_user(username="other-user", password="pwd")
        self.admin = get_user_model().objects.create_user(
            username="admin-user",
            password="pwd",
            is_staff=True,
        )
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-vis",
            name="Visibility App",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-VIS-1",
            title="Visibility DAT",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.owner,
        )

    def test_owner_can_view_dat_detail(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:dat_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visibility DAT")

    def test_unassigned_user_cannot_view_dat_detail(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("dat:dat_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 404)

    def test_participant_can_view_dat_detail(self):
        self._bind_participant(self.dat, DAT_PORTEUR_ROLE_SLUG, self.other)
        self.client.force_login(self.other)
        response = self.client.get(reverse("dat:dat_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visibility DAT")
        self.assertContains(response, "Validation actuelle")

    def test_admin_can_view_any_dat_detail(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dat:dat_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visibility DAT")

    def test_owner_can_view_my_detail_page(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visibility DAT")

    def test_my_detail_section_switch_keeps_page_structure_valid(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]), {"section": "architecture"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_section_slug"], "architecture")
        content = response.content.decode()
        self.assertIn('class="dat-section-status-pill chip dat-section-link"', content)
        self.assertNotIn('class="dat-section-status-pill chip dat-section-link">\n        <div', content)

    def test_unassigned_user_cannot_view_my_detail_page(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 404)

    def test_participant_can_view_my_detail_page(self):
        self._bind_participant(self.dat, DAT_PORTEUR_ROLE_SLUG, self.other)
        self.client.force_login(self.other)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visibility DAT")
        self.assertContains(response, "Validation actuelle")
        self.assertContains(response, self.other.username)

    def test_admin_can_view_my_detail_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visibility DAT")
        self.assertContains(response, "Validation actuelle")

    def _assign_role(self, user, slug):
        role = self.roles[slug]
        user.role = role
        user.save(update_fields=["role"])
        return role

    def _bind_participant(self, dat, slug, user):
        role = self._assign_role(user, slug)
        DATParticipant.objects.update_or_create(
            dat=dat,
            role=role,
            defaults={"user": user},
        )
        return role

    def test_progress_button_is_hidden(self):
        detail_url = reverse("dat:my_detail", args=[self.dat.pk])

        self.client.force_login(self.owner)
        response = self.client.get(detail_url)
        self.assertNotContains(response, "Passer à l'étape suivante")

        self._bind_participant(self.dat, DAT_PORTEUR_ROLE_SLUG, self.owner)
        response_with_role = self.client.get(detail_url)
        self.assertNotContains(response_with_role, "Passer à l'étape suivante")

    def test_reviewer_can_validate_from_validation_section(self):
        referent = get_user_model().objects.create_user(username="referent-user", password="pwd")
        dat = DAT.objects.create(
            reference="DAT-VIS-REFERENT",
            title="Referent DAT",
            application=self.application,
            status=DATStatus.EN_ATTENTE_DE_REVUE,
            owner=self.owner,
        )
        self._bind_participant(dat, DAT_PORTEUR_ROLE_SLUG, self.owner)
        self._bind_participant(dat, "architecte-referent", referent)

        self.client.force_login(referent)
        response = self.client.post(
            reverse("dat:my_validation_decision", args=[dat.pk]),
            {"decision": "valider"},
        )
        self.assertRedirects(response, reverse("dat:my_detail", args=[dat.pk]))
        dat.refresh_from_db()
        self.assertEqual(dat.status, DATStatus.VALIDER)

    def test_list_only_shows_assigned_dats(self):
        DAT.objects.create(
            reference="DAT-VIS-2",
            title="Other DAT",
            application=self.application,
            status=DATStatus.EN_COURS,
            owner=self.other,
        )
        shared_dat = DAT.objects.create(
            reference="DAT-VIS-3",
            title="Shared DAT",
            application=self.application,
            status=DATStatus.EN_ATTENTE_DE_REVUE,
            owner=self.other,
        )
        self._bind_participant(shared_dat, DAT_PORTEUR_ROLE_SLUG, self.owner)

        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DAT-VIS-1")
        self.assertContains(response, "DAT-VIS-3")
        self.assertNotContains(response, "DAT-VIS-2")

        self.client.force_login(self.other)
        response_other = self.client.get(reverse("dat:my_list"))
        self.assertEqual(response_other.status_code, 200)
        self.assertContains(response_other, "DAT-VIS-2")
        self.assertNotContains(response_other, "DAT-VIS-1")
        self.assertContains(response_other, "DAT-VIS-3")

class DatParticipantAssignmentFormTest(TestCase):
    def setUp(self) -> None:
        self.roles: dict[str, Role] = {}
        for slug in DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS:
            label = DAT_REQUIRED_PARTICIPANT_ROLE_LABELS.get(slug, slug)
            self.roles[slug] = create_role(slug, label)
        self.users: dict[str, object] = {}
        User = get_user_model()
        for slug in DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS:
            username = f"{slug.replace('-', '_')}_user"
            user, _created = User.objects.get_or_create(username=username, defaults={"password": "pwd"})
            if _created:
                user.set_password("pwd")
                user.save(update_fields=["password"])
            user.role = self.roles[slug]
            user.save()
            self.users[slug] = user
        self.porteur = self.users[DAT_PORTEUR_ROLE_SLUG]
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="form-app",
            name="Form App",
            business_direction=direction,
        )

    def _make_user(self, role_slug: str, suffix: str) -> object:
        User = get_user_model()
        username = f"{role_slug.replace('-', '_')}_{suffix}"
        user = User.objects.create_user(username=username, password="pwd")
        user.role = self.roles[role_slug]
        user.save()
        return user

    def _build_form_data(
        self,
        *,
        reference="DAT-FORM-1",
        title="Form DAT",
        description="Description",
        status=None,
        porteur=None,
        overrides=None,
    ):
        data: dict[str, object] = {
            "reference": reference,
            "title": title,
            "application": self.application.pk,
            "description": description,
            "status": status or DATStatus.NOUVELLE_DEMANDE,
        }
        for slug in DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS:
            user = self.users[slug]
            if slug == DAT_PORTEUR_ROLE_SLUG and porteur is not None:
                user = porteur
            if overrides and slug in overrides:
                user = overrides[slug]
            data[DATForm.participant_field_name(slug)] = user.pk
        return data

    def test_form_creates_required_participants(self):
        form_data = self._build_form_data()
        form = DATForm(data=form_data, user=self.porteur)
        self.assertTrue(form.is_valid(), form.errors)
        dat = form.save()
        dat.refresh_from_db()
        self.assertEqual(dat.owner, self.porteur)
        self.assertEqual(
            dat.participants.filter(role__slug__in=DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS).count(),
            len(DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS),
        )
        for slug in DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS:
            participant = dat.participants.get(role__slug=slug)
            expected_user = self.users[slug]
            self.assertEqual(participant.user, expected_user)

    def test_form_updates_existing_participants(self):
        create_data = self._build_form_data()
        create_form = DATForm(data=create_data, user=self.porteur)
        self.assertTrue(create_form.is_valid(), create_form.errors)
        dat = create_form.save()
        dat.refresh_from_db()

        new_porteur = self._make_user(DAT_PORTEUR_ROLE_SLUG, "alt")
        new_analyste = self._make_user("analyste-secu", "alt")

        update_data = self._build_form_data(
            reference=dat.reference,
            title="Form DAT Updated",
            description="Updated description",
            status=dat.status,
            porteur=new_porteur,
            overrides={"analyste-secu": new_analyste},
        )
        update_form = DATForm(data=update_data, instance=dat, user=self.porteur)
        self.assertTrue(update_form.is_valid(), update_form.errors)
        updated_dat = update_form.save()
        updated_dat.refresh_from_db()

        self.assertEqual(updated_dat.owner, new_porteur)
        self.assertEqual(
            updated_dat.participants.filter(role__slug__in=DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS).count(),
            len(DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS),
        )
        self.assertEqual(
            updated_dat.participants.get(role__slug=DAT_PORTEUR_ROLE_SLUG).user,
            new_porteur,
        )
        self.assertEqual(
            updated_dat.participants.get(role__slug="analyste-secu").user,
            new_analyste,
        )


class DatOverviewSectionResponsibleUpdateTest(TestCase):
    def setUp(self) -> None:
        self.roles: dict[str, Role] = {}
        for slug, label in DAT_REQUIRED_PARTICIPANT_ROLE_LABELS.items():
            self.roles[slug] = create_role(slug, label)

        User = get_user_model()
        self.owner = User.objects.create_user(username="overview-owner", password="pwd")
        self.owner.role = self.roles[DAT_PORTEUR_ROLE_SLUG]
        self.owner.save(update_fields=["role"])

        self.architect_responsable = User.objects.create_user(username="overview-archi-resp", password="pwd")
        self.architect_responsable.role = self.roles["architecte-technique"]
        self.architect_responsable.save(update_fields=["role"])

        self.referent_executant = User.objects.create_user(username="overview-ref-exec", password="pwd")
        self.referent_executant.role = self.roles["architecte-referent"]
        self.referent_executant.save(update_fields=["role"])
        self.rssi_user = None

        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="overview-update-app",
            name="Overview Update App",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-OVERVIEW-UPD",
            title="Overview update",
            application=self.application,
            status=DATStatus.EN_COURS,
            owner=self.owner,
        )
        sync_dat_sections_if_needed(self.dat)

        DATParticipant.objects.create(
            dat=self.dat,
            role=self.roles[DAT_PORTEUR_ROLE_SLUG],
            user=self.owner,
            participant_type=DATParticipantType.RESPONSABLE,
        )
        DATParticipant.objects.create(
            dat=self.dat,
            role=self.roles["architecte-technique"],
            user=self.architect_responsable,
            participant_type=DATParticipantType.RESPONSABLE,
        )
        DATParticipant.objects.create(
            dat=self.dat,
            role=self.roles["architecte-referent"],
            user=self.referent_executant,
            participant_type=DATParticipantType.EXECUTANT,
        )
        for slug in DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS:
            if slug in {DAT_PORTEUR_ROLE_SLUG, "architecte-technique", "architecte-referent"}:
                continue
            user = User.objects.create_user(
                username=f"overview-{slug.replace('-', '_')}",
                password="pwd",
            )
            user.role = self.roles[slug]
            user.save(update_fields=["role"])
            DATParticipant.objects.create(
                dat=self.dat,
                role=self.roles[slug],
                user=user,
                participant_type=DATParticipantType.RESPONSABLE,
            )
            if slug == "rssi":
                self.rssi_user = user
        self.assertIsNotNone(self.rssi_user)

    def _build_section_responsible_payload(self, overrides: dict[str, str] | None = None):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        rows = (response.context.get("section_responsible_editor") or {}).get("rows", [])
        payload = {}
        for row in rows:
            if not row.get("has_options"):
                continue
            section_slug = row["section_slug"]
            if overrides and section_slug in overrides:
                payload[row["field_name"]] = overrides[section_slug]
                continue
            current = row.get("current_user_id")
            if current:
                payload[row["field_name"]] = current
            else:
                payload[row["field_name"]] = row["options"][0]["id"]
        return payload

    def _build_section_responsible_payload_with_admins(
        self,
        *,
        actor,
        overrides: dict[str, str] | None = None,
        admin_sections: set[str] | None = None,
    ):
        self.client.force_login(actor)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        rows = (response.context.get("section_responsible_editor") or {}).get("rows", [])
        payload = {}
        for row in rows:
            if not row.get("has_options"):
                continue
            section_slug = row["section_slug"]
            if overrides and section_slug in overrides:
                payload[row["field_name"]] = overrides[section_slug]
            else:
                payload[row["field_name"]] = row.get("current_user_id") or row["options"][0]["id"]
            if admin_sections and section_slug in admin_sections:
                payload[row["admin_field_name"]] = "1"
        return payload

    def _build_section_participant_payload(
        self,
        *,
        actor,
        overrides: dict[str, str] | None = None,
    ):
        self.client.force_login(actor)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        rows = (response.context.get("section_participant_editor") or {}).get("rows", [])
        payload = {}
        for row in rows:
            if not row.get("can_edit"):
                continue
            if not row.get("has_options"):
                continue
            section_slug = row["section_slug"]
            if overrides and section_slug in overrides:
                payload[row["field_name"]] = overrides[section_slug]
            else:
                payload[row["field_name"]] = row.get("current_user_id", "")
        return payload

    def test_owner_can_update_section_responsible_assignments(self):
        self.client.force_login(self.owner)
        architecture_section = DATSection.objects.get(dat=self.dat, metadata__slug="architecture")
        cyber_section = DATSection.objects.get(dat=self.dat, metadata__slug="cybersecurite")
        response = self.client.post(
            reverse("dat:my_section_responsibles_update", args=[self.dat.pk]),
            self._build_section_responsible_payload(),
        )
        self.assertEqual(response.status_code, 302, response.url)
        architecture_assignment = DATSectionResponsible.objects.get(section=architecture_section)
        self.assertEqual(architecture_assignment.user, self.referent_executant)
        cyber_assignment = DATSectionResponsible.objects.get(section=cyber_section)
        self.assertEqual(cyber_assignment.user, self.rssi_user)

    def test_owner_is_dat_admin_for_own_dat(self):
        self.assertTrue(user_is_dat_admin_for_dat(self.dat, self.owner))

    def test_owner_can_promote_section_responsible_to_dat_admin(self):
        architecture_section = DATSection.objects.get(dat=self.dat, metadata__slug="architecture")
        payload = self._build_section_responsible_payload_with_admins(
            actor=self.owner,
            overrides={architecture_section.slug: str(self.referent_executant.pk)},
            admin_sections={architecture_section.slug},
        )
        response = self.client.post(
            reverse("dat:my_section_responsibles_update", args=[self.dat.pk]),
            payload,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            DATAdmin.objects.filter(dat=self.dat, user=self.referent_executant).exists()
        )
        self.assertTrue(user_is_dat_admin_for_dat(self.dat, self.referent_executant))

    def test_dat_admin_editor_block_is_exposed_in_overview_context(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        editor = response.context.get("dat_admin_editor") or {}
        self.assertIn("admins", editor)
        self.assertIn("candidate_options", editor)
        self.assertIn("add_url", editor)
        self.assertTrue(editor.get("can_edit"))

    def test_dat_admin_editor_candidate_option_ids_keep_uuid_format(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        editor = response.context.get("dat_admin_editor") or {}
        candidate_ids = {option.get("id") for option in editor.get("candidate_options", [])}
        self.assertIn(str(self.referent_executant.pk), candidate_ids)

    def test_owner_can_add_dat_admin_from_dedicated_endpoint(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("dat:my_dat_admin_add", args=[self.dat.pk]),
            {"user_id": str(self.referent_executant.pk)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DATAdmin.objects.filter(dat=self.dat, user=self.referent_executant).exists())

    def test_non_admin_cannot_add_dat_admin_from_dedicated_endpoint(self):
        self.client.force_login(self.referent_executant)
        response = self.client.post(
            reverse("dat:my_dat_admin_add", args=[self.dat.pk]),
            {"user_id": str(self.rssi_user.pk)},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DATAdmin.objects.filter(dat=self.dat, user=self.rssi_user).exists())

    def test_owner_can_remove_dat_admin_from_dedicated_endpoint(self):
        DATAdmin.objects.create(dat=self.dat, user=self.referent_executant)
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("dat:my_dat_admin_remove", args=[self.dat.pk, self.referent_executant.pk]),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DATAdmin.objects.filter(dat=self.dat, user=self.referent_executant).exists())

    def test_owner_cannot_remove_dat_owner_from_admins(self):
        DATAdmin.objects.create(dat=self.dat, user=self.owner)
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("dat:my_dat_admin_remove", args=[self.dat.pk, self.owner.pk]),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DATAdmin.objects.filter(dat=self.dat, user=self.owner).exists())

    def test_owner_can_update_section_participants(self):
        architecture_section = DATSection.objects.get(dat=self.dat, metadata__slug="architecture")
        payload = self._build_section_participant_payload(
            actor=self.owner,
            overrides={architecture_section.slug: str(self.architect_responsable.pk)},
        )
        response = self.client.post(
            reverse("dat:my_section_participants_update", args=[self.dat.pk]),
            payload,
        )
        self.assertEqual(response.status_code, 302)
        assignment = DATSectionParticipant.objects.get(section=architecture_section)
        self.assertEqual(assignment.user, self.architect_responsable)

    def test_section_responsible_can_update_only_own_section_participant(self):
        architecture_section = DATSection.objects.get(dat=self.dat, metadata__slug="architecture")
        cyber_section = DATSection.objects.get(dat=self.dat, metadata__slug="cybersecurite")
        DATSectionResponsible.objects.create(
            dat=self.dat,
            section=architecture_section,
            user=self.referent_executant,
        )
        DATSectionResponsible.objects.create(
            dat=self.dat,
            section=cyber_section,
            user=self.rssi_user,
        )
        payload = self._build_section_participant_payload(
            actor=self.referent_executant,
            overrides={architecture_section.slug: str(self.architect_responsable.pk)},
        )
        payload[f"section_participant__{cyber_section.slug}"] = str(self.owner.pk)
        response = self.client.post(
            reverse("dat:my_section_participants_update", args=[self.dat.pk]),
            payload,
        )
        self.assertEqual(response.status_code, 302)
        architecture_assignment = DATSectionParticipant.objects.get(section=architecture_section)
        self.assertEqual(architecture_assignment.user, self.architect_responsable)
        self.assertFalse(
            DATSectionParticipant.objects.filter(section=cyber_section).exists()
        )

    def test_forced_section_roles_are_enforced_in_editor_options(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        editor = response.context["section_responsible_editor"]
        rows = editor.get("rows") or []
        architecture_row = None
        for row in rows:
            if row["section_slug"] == "architecture":
                architecture_row = row
                break
        self.assertIsNotNone(architecture_row)
        option_ids = {opt["id"] for opt in architecture_row["options"]}
        self.assertIn(str(self.referent_executant.pk), option_ids)
        self.assertNotIn(str(self.architect_responsable.pk), option_ids)
        self.assertEqual(architecture_row["current_user_id"], str(self.referent_executant.pk))

        cyber_row = None
        for row in rows:
            if row["section_slug"] == "cybersecurite":
                cyber_row = row
                break
        self.assertIsNotNone(cyber_row)
        cyber_option_ids = {opt["id"] for opt in cyber_row["options"]}
        self.assertEqual(cyber_option_ids, {str(self.rssi_user.pk)})
        self.assertEqual(cyber_row["current_user_id"], str(self.rssi_user.pk))

    def test_editor_infers_current_assignment_from_participants_when_missing(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        editor = response.context["section_responsible_editor"]
        rows = editor.get("rows") or []
        architecture_row = None
        for row in rows:
            if row["section_slug"] == "architecture":
                architecture_row = row
                break
        self.assertIsNotNone(architecture_row)
        self.assertEqual(architecture_row["current_user_id"], str(self.referent_executant.pk))

    def test_owner_can_deassign_section_responsible_from_overview_table(self):
        self.client.force_login(self.owner)
        architecture_section = DATSection.objects.get(dat=self.dat, metadata__slug="architecture")

        create_response = self.client.post(
            reverse("dat:my_section_responsibles_update", args=[self.dat.pk]),
            self._build_section_responsible_payload(),
        )
        self.assertEqual(create_response.status_code, 302)
        self.assertTrue(DATSectionResponsible.objects.filter(section=architecture_section).exists())

        payload = self._build_section_responsible_payload(overrides={architecture_section.slug: ""})
        clear_response = self.client.post(
            reverse("dat:my_section_responsibles_update", args=[self.dat.pk]),
            payload,
        )
        self.assertEqual(clear_response.status_code, 302)
        self.assertFalse(DATSectionResponsible.objects.filter(section=architecture_section).exists())

        refresh_response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(refresh_response.status_code, 200)
        editor = refresh_response.context["section_responsible_editor"]
        rows = editor.get("rows") or []
        architecture_row = None
        for row in rows:
            if row["section_slug"] == "architecture":
                architecture_row = row
                break
        self.assertIsNotNone(architecture_row)
        self.assertEqual(architecture_row["current_user_id"], "")

    def test_section_card_shows_unassigned_after_explicit_deassignment(self):
        self.client.force_login(self.owner)
        architecture_section = DATSection.objects.get(dat=self.dat, metadata__slug="architecture")

        self.client.post(
            reverse("dat:my_section_responsibles_update", args=[self.dat.pk]),
            self._build_section_responsible_payload(),
        )
        self.client.post(
            reverse("dat:my_section_responsibles_update", args=[self.dat.pk]),
            self._build_section_responsible_payload(overrides={architecture_section.slug: ""}),
        )

        response = self.client.get(
            reverse("dat:my_detail", args=[self.dat.pk]) + "?section=architecture"
        )
        self.assertEqual(response.status_code, 200)
        selected_sections = response.context.get("selected_sections") or []
        self.assertTrue(selected_sections)
        architecture_payload = selected_sections[0]
        responsibles = architecture_payload.get("section_responsibles") or []
        self.assertEqual(responsibles, [])

    def test_section_card_infers_responsible_from_participants_when_missing(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("dat:my_detail", args=[self.dat.pk]) + "?section=architecture"
        )
        self.assertEqual(response.status_code, 200)
        selected_sections = response.context.get("selected_sections") or []
        self.assertTrue(selected_sections)
        architecture_payload = selected_sections[0]
        responsibles = architecture_payload.get("section_responsibles") or []
        self.assertTrue(responsibles)
        displays = {item.get("display") for item in responsibles}
        self.assertIn(format_user_display(self.referent_executant), displays)

    def test_invalid_responsible_selection_is_rejected(self):
        self.client.force_login(self.owner)
        architecture_section = DATSection.objects.get(dat=self.dat, metadata__slug="architecture")
        payload = self._build_section_responsible_payload(
            overrides={architecture_section.slug: str(self.architect_responsable.pk)}
        )
        response = self.client.post(
            reverse("dat:my_section_responsibles_update", args=[self.dat.pk]),
            payload,
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            DATSectionResponsible.objects.filter(
                section=architecture_section,
                user=self.architect_responsable,
            ).exists()
        )

    def test_group_responsible_is_not_available_for_forced_architecture_role(self):
        referent_role = self.roles["architecte-referent"]
        manager = get_user_model().objects.create_user(
            username="overview-group-manager",
            password="pwd",
        )
        manager.role = referent_role
        manager.save(update_fields=["role"])
        group = BusinessGroup.objects.create(
            name="Overview Group",
            direction=referent_role.technical_direction,
            responsible=manager,
            business_direction=get_default_business_direction(),
        )
        manager.business_group = group
        manager.save(update_fields=["business_group"])
        self.referent_executant.business_group = group
        self.referent_executant.save(update_fields=["business_group"])

        self.client.force_login(self.owner)
        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        editor = response.context["section_responsible_editor"]
        rows = editor.get("rows") or []
        architecture_row = None
        for row in rows:
            if row["section_slug"] == "architecture":
                architecture_row = row
                break
        self.assertIsNotNone(architecture_row)
        option_ids = {opt["id"] for opt in architecture_row["options"]}
        self.assertNotIn(str(manager.pk), option_ids)
        self.assertIn(str(self.referent_executant.pk), option_ids)


class ApplicationModelFormattingTest(TestCase):
    def test_formatted_dates(self):
        direction = get_default_business_direction()
        application = Application.objects.create(
            code="format-app",
            name="Format App",
            business_direction=direction,
        )
        formatted_created = application.formatted_created_at()
        formatted_updated = application.formatted_updated_at()
        self.assertIn("/", formatted_created)
        self.assertIn("h", formatted_created)
        self.assertIn("/", formatted_updated)
        self.assertIn("h", formatted_updated)


class DatHistoryTest(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="history-user")
        self.manager = get_user_model().objects.create_user(
            username="history-manager",
            is_staff=True,
        )
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="hist-app",
            name="History App",
            business_direction=direction,
        )
        self.addCleanup(clear_request_context)

    def _create_dat(self) -> DAT:
        dat = DAT(
            reference="DAT-HIST-1",
            title="History Tracking",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
        )
        dat._history_actor = self.user  # type: ignore[attr-defined]
        dat.save()
        return dat

    def test_history_entry_created_on_creation(self):
        dat = self._create_dat()
        entries = dat.history_entries.all()
        self.assertEqual(entries.count(), 1)
        entry = entries.first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.action, DATHistoryAction.CREATED)
        self.assertEqual(entry.performed_by, self.user)
        self.assertEqual(entry.actor_name(), self.user.username)

    def test_status_change_records_history_entry(self):
        dat = self._create_dat()
        dat.status = DATStatus.EN_ATTENTE_DE_REVUE
        dat._history_actor = self.manager  # type: ignore[attr-defined]
        dat.save()
        status_entries = dat.history_entries.filter(action=DATHistoryAction.STATUS_CHANGED)
        self.assertEqual(status_entries.count(), 1)
        entry = status_entries.first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.status_change_from, DATStatus.NOUVELLE_DEMANDE.label)
        self.assertEqual(entry.status_change_to, DATStatus.EN_ATTENTE_DE_REVUE.label)
        self.assertEqual(entry.details.get("from"), DATStatus.NOUVELLE_DEMANDE.label)
        self.assertEqual(entry.details.get("to"), DATStatus.EN_ATTENTE_DE_REVUE.label)
        self.assertEqual(entry.performed_by, self.manager)
        self.assertEqual(entry.actor_name(), self.manager.username)
        self.assertEqual(
            dat.history_entries.filter(action=DATHistoryAction.UPDATED).count(),
            0,
        )

    def test_detail_view_displays_history(self):
        dat = self._create_dat()
        dat.status = DATStatus.EN_ATTENTE_DE_REVUE
        dat._history_actor = self.manager  # type: ignore[attr-defined]
        dat.save()
        self.client.force_login(self.manager)
        url = reverse("dat:dat_detail", args=[dat.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Historique du dossier", content)
        self.assertIn(self.manager.username, content)
        self.assertIn(DATStatus.EN_ATTENTE_DE_REVUE.label, content)
        self.assertIn("Passage de", content)

    def test_history_uses_request_context_when_actor_not_set(self):
        bind_request_context(user_id=self.manager.id, username=self.manager.username)
        dat = DAT.objects.create(
            reference="DAT-HIST-CTX",
            title="Request Context Actor",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
        )
        entry = dat.history_entries.first()
        self.assertIsNotNone(entry)
        if entry:
            self.assertEqual(entry.action, DATHistoryAction.CREATED)
            self.assertEqual(entry.performed_by_id, self.manager.id)
            self.assertEqual(entry.performed_by_display, self.manager.username)
            self.assertEqual(entry.actor_name(), self.manager.username)


class DatSectionIntegrationTest(TestCase):
    def setUp(self) -> None:
        self.roles = {}
        role_defs = [
            ("porteur-demande", "Porteur de la demande"),
            ("architecte-technique", "Architecte technique"),
            ("architecte-referent", "Architecte referent"),
            ("analyste-secu", "Analyste securite"),
            ("rssi", "RSSI"),
            ("infra-exploitation", "Infra / Exploitation"),
        ]
        for slug, name in role_defs:
            self.roles[slug] = ensure_role(slug, name)

        User = get_user_model()
        self.porteur = User.objects.create_user(username="sections-porteur", password="pwd")
        self.porteur.role = self.roles["porteur-demande"]
        self.porteur.save(update_fields=["role"])
        self.architect = User.objects.create_user(username="sections-architecte", password="pwd")
        self.architect.role = self.roles["architecte-technique"]
        self.architect.save(update_fields=["role"])

        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-sections",
            name="Sections App",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-SECT-1",
            title="DAT Sections",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.porteur,
        )
        DATParticipant.objects.create(dat=self.dat, role=self.roles["porteur-demande"], user=self.porteur)
        sync_dat_sections_if_needed(self.dat)
        besoins_section = self.dat.sections.get(metadata__slug="besoins")
        DATSectionParticipant.objects.create(dat=self.dat, section=besoins_section, user=self.porteur)

    def test_default_sections_created(self):
        sections = list(self.dat.sections.order_by("order"))
        self.assertEqual(len(sections), 7)
        besoins = next((section for section in sections if section.slug == "besoins"), None)
        self.assertIsNotNone(besoins)
        if besoins:
            self.assertEqual(besoins.sub_sections.count(), 2)
            for part in besoins.sub_sections.all():
                self.assertGreaterEqual(part.parts.count(), 1)

    def test_porteur_can_update_section_and_history_logged(self):
        section, sub_section, entry = self._prepare_sub_section_with_entry()
        url = reverse("dat:sub_section_edit", args=[self.dat.pk, section.slug, sub_section.slug])
        self.client.force_login(self.porteur)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        post_data = {
            entry.form_field_name(): "Nouveau besoin prioritaire",
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("dat:my_detail", args=[self.dat.pk])))

        entry.refresh_from_db()
        self.assertEqual(entry.value, "Nouveau besoin prioritaire")

        history_entry = self.dat.history_entries.filter(action=DATHistoryAction.SECTION_UPDATED).first()
        self.assertIsNotNone(history_entry)
        if history_entry and history_entry.details:
            changes = history_entry.details.get("changes", {})
            self.assertIn("besoin_creation", changes)

        detail_url = reverse("dat:my_detail", args=[self.dat.pk])
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(entry.label, content)
        self.assertIn("Nouveau besoin prioritaire", content)

    def _prepare_sub_section_with_entry(self):
        section = self.dat.sections.get(metadata__slug="besoins")
        sub_section = section.sub_sections.filter(slug="detail-besoin").first() or section.sub_sections.first()
        if not sub_section:
            self.fail("Section sans sous-section initialisée")
        entry = sub_section.parts.filter(key="besoin_creation").first() or sub_section.parts.first()
        if entry is None:
            entry = DATPart.objects.create(
                sub_section=sub_section,
                key="besoin_creation",
                label="Besoin de création",
                data_type=DATPartEntryType.LONG_TEXT,
            )
        return section, sub_section, entry

    def test_ajax_get_returns_form_html(self):
        section, sub_section, entry = self._prepare_sub_section_with_entry()
        url = reverse("dat:sub_section_edit", args=[self.dat.pk, section.slug, sub_section.slug])
        self.client.force_login(self.porteur)
        response = self.client.get(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("form_html", payload)
        self.assertIn(entry.label, payload["form_html"])
        self.assertEqual(payload.get("title"), sub_section.title)

    def test_ajax_post_updates_sub_section(self):
        section, sub_section, entry = self._prepare_sub_section_with_entry()
        url = reverse("dat:sub_section_edit", args=[self.dat.pk, section.slug, sub_section.slug])
        self.client.force_login(self.porteur)
        response = self.client.post(
            url,
            {entry.form_field_name(): "Mise à jour via AJAX"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        self.assertEqual(payload.get("sub_section_slug"), sub_section.slug)
        self.assertIn(sub_section.slug, payload.get("sub_section_html", ""))
        entry.refresh_from_db()
        self.assertEqual(entry.value, "Mise à jour via AJAX")

    def test_user_without_assignment_cannot_edit_section(self):
        section = self.dat.sections.get(metadata__slug="besoins")
        sub_section = section.sub_sections.first()
        url = reverse("dat:sub_section_edit", args=[self.dat.pk, section.slug, sub_section.slug])
        self.client.force_login(self.architect)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_sections_visible_in_detail_view(self):
        self.client.force_login(self.porteur)
        url = reverse("dat:my_detail", args=[self.dat.pk])
        response = self.client.get(url, {"section": "besoins"})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("BESOIN(S)", content)


class CreateSchemaDiagramViewTest(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            username="diagram-staff",
            password="pwd",
            is_staff=True,
        )
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-diagram",
            name="Diagram App",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-DIAG-1",
            title="Schema DAT",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        architecture_section = self.dat.sections.get(metadata__slug="architecture")
        DATSectionParticipant.objects.create(dat=self.dat, section=architecture_section, user=self.user)
        self.url = reverse("dat:schema_create_diagram", args=[self.dat.pk])

    def test_rejects_invalid_diagram_title(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({"title": "<script>alert(1)</script>"}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload.get("ok"))
        self.assertEqual(payload.get("error"), "invalid_title")
        self.assertIn("caract", payload.get("message", "").lower())
        self.assertEqual(DrawIODiagram.objects.count(), 0)

    def test_creates_diagram_with_normalized_title(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({"title": "   Nouveau    diagramme   critique   "}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        diagram_payload = payload.get("diagram") or {}
        self.assertEqual(diagram_payload.get("title"), "Nouveau diagramme critique")
        diagram = DrawIODiagram.objects.get(pk=diagram_payload.get("id"))
        self.assertEqual(diagram.owner, self.user)
        self.assertEqual(diagram.title, "Nouveau diagramme critique")

    def test_uses_reference_based_title_when_missing(self):
        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        diagram_payload = payload.get("diagram") or {}
        self.assertIn("DAT-DIAG-1", diagram_payload.get("title", ""))
        diagram = DrawIODiagram.objects.get(pk=diagram_payload.get("id"))
        self.assertTrue(diagram.title.startswith("DAT-DIAG-1"))

    def test_allows_when_schema_sub_section_is_editable_even_if_section_is_not(self):
        role_section = ensure_role("test-section-role", "Section Role")
        role_schemas = ensure_role("test-schemas-role", "Schemas Role")
        self.user.role = role_schemas
        self.user.save(update_fields=["role"])
        DATParticipant.objects.create(
            dat=self.dat,
            user=self.user,
            role=role_schemas,
            participant_type=DATParticipantType.EXECUTANT,
        )

        architecture_section = DATSection.objects.filter(dat=self.dat, metadata__slug="architecture").order_by("order", "id").first()
        self.assertIsNotNone(architecture_section)
        DATSectionParticipant.objects.update_or_create(
            dat=self.dat,
            section=architecture_section,
            defaults={"user": self.user},
        )
        architecture_section.allowed_roles.set([role_section])
        schema_sub_section = DATSubSection.objects.get(
            section=architecture_section,
            slug="schemas",
        )
        schema_sub_section.allowed_roles.set([role_schemas])
        other_arch_metadata = DATSectionMetadata.objects.create(
            title="Architecture legacy",
            slug="architecture",
            description="",
        )
        other_arch_section = DATSection.objects.create(
            dat=self.dat,
            metadata=other_arch_metadata,
            order=0,
        )
        other_arch_section.allowed_roles.set([role_section])

        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({"title": "Schema autorise"}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload.get("ok"))

    def test_creates_diagram_for_editable_non_architecture_sub_section(self):
        urbanisme_section = DATSection.objects.get(dat=self.dat, metadata__slug="urbanisme")
        DATSectionParticipant.objects.create(dat=self.dat, section=urbanisme_section, user=self.user)
        sub_section = DATSubSection.objects.get(
            section=urbanisme_section,
            slug="mapping-urbanisation-si",
        )

        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "title": "Cartographie urbanisme",
                    "section_slug": "urbanisme",
                    "sub_section_slug": sub_section.slug,
                }
            ),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload["diagram"]["title"], "Cartographie urbanisme")

    def test_rejects_non_architecture_sub_section_when_user_cannot_edit_it(self):
        urbanisme_section = DATSection.objects.get(dat=self.dat, metadata__slug="urbanisme")
        sub_section = DATSubSection.objects.get(
            section=urbanisme_section,
            slug="mapping-urbanisation-si",
        )

        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "title": "Cartographie interdite",
                    "section_slug": "urbanisme",
                    "sub_section_slug": sub_section.slug,
                }
            ),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(DrawIODiagram.objects.count(), 0)

    def test_creates_diagram_from_referer_section_when_payload_has_no_context(self):
        urbanisme_section = DATSection.objects.get(dat=self.dat, metadata__slug="urbanisme")
        DATSectionParticipant.objects.create(dat=self.dat, section=urbanisme_section, user=self.user)
        sub_section = DATSubSection.objects.get(
            section=urbanisme_section,
            slug="mapping-urbanisation-si",
        )

        self.client.force_login(self.user)
        response = self.client.post(
            self.url,
            data=json.dumps({"title": "Cartographie depuis referer"}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_REFERER=f"/dat/my/{self.dat.pk}/?section=urbanisme",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload.get("ok"))
        self.assertEqual(payload["diagram"]["title"], "Cartographie depuis referer")


class DatPdfExportNotificationTest(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="pdf-user", password="pwd")
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-pdf",
            name="Application PDF",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-PDF",
            title="DAT PDF",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        self.client.force_login(self.user)

    @mock.patch("dat.tasks.enqueue_pdf_export_job")
    def test_trigger_pdf_export_sends_user_notification(self, enqueue_job):
        enqueue_job.return_value = mock.Mock(id="55555555-5555-5555-5555-555555555555", status="queued")
        url = reverse("dat:my_export_pdf_trigger", args=[self.dat.pk])
        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        notifications = UserNotification.objects.filter(user=self.user)
        self.assertEqual(notifications.count(), 0)
        enqueue_job.assert_called_once()

    @mock.patch("dat.views.schedule_dat_pdf_generation")
    def test_trigger_pdf_export_ajax_returns_job_contract(self, schedule_job):
        schedule_job.return_value = mock.Mock(id="77777777-7777-7777-7777-777777777777", status="queued")
        url = reverse("dat:my_export_pdf_trigger", args=[self.dat.pk])
        response = self.client.post(
            url,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["job_id"], "77777777-7777-7777-7777-777777777777")
        self.assertEqual(payload["status"], "queued")
        self.assertIn("/api/jobs/77777777-7777-7777-7777-777777777777/", payload["status_url"])

    @mock.patch("dat.views.schedule_dat_pdf_generation")
    def test_trigger_pdf_export_ajax_conflict_when_already_running(self, schedule_job):
        schedule_job.return_value = None
        url = reverse("dat:my_export_pdf_trigger", args=[self.dat.pk])
        response = self.client.post(
            url,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "already_in_progress")

    @mock.patch("dat.tasks.store_dat_pdf_export")
    @mock.patch("dat.tasks.generate_dat_pdf")
    def test_pdf_generation_completion_creates_notification(self, generate_pdf, store_pdf):
        generate_pdf.return_value = (b"%PDF", {})
        store_pdf.return_value = "dat/path.pdf"
        DAT.objects.filter(pk=self.dat.pk).update(
            pdf_export_in_progress=True,
            pdf_export_requested_by=self.user,
            pdf_export_requested_by_display="PDF User",
        )

        _run_pdf_generation(self.dat.pk, base_url=None)

        notification = UserNotification.objects.get(user=self.user)
        self.assertEqual(notification.title, "Export PDF disponible")
        self.assertEqual(notification.dat, self.dat)
        self.assertEqual(notification.level, "success")
        self.assertIn("prêt", notification.message)

    @mock.patch("dat.utils.get_dat_export_storage")
    def test_pdf_export_helpers_tolerate_storage_outage(self, get_storage):
        storage = mock.Mock()
        storage.exists.side_effect = OSError("temporary storage outage")
        get_storage.return_value = storage

        self.assertFalse(dat_pdf_export_exists(self.dat))
        self.assertIsNone(dat_pdf_export_modified_at(self.dat))
        self.assertIsNone(open_dat_pdf_export(self.dat))

    @mock.patch("dat.utils.get_dat_export_storage")
    def test_dat_detail_tolerates_pdf_storage_outage(self, get_storage):
        storage = mock.Mock()
        storage.exists.side_effect = OSError("temporary storage outage")
        get_storage.return_value = storage

        response = self.client.get(reverse("dat:my_detail", args=[self.dat.pk]))

        self.assertEqual(response.status_code, 200)


class DatSecureExportAccessTest(TestCase):
    def setUp(self) -> None:
        self.admin_1 = get_user_model().objects.create_user(username="secure-admin-1", password="pwd")
        self.admin_2 = get_user_model().objects.create_user(username="secure-admin-2", password="pwd")
        self.admin_3 = get_user_model().objects.create_user(username="secure-admin-3", password="pwd")
        self.regular = get_user_model().objects.create_user(username="secure-regular", password="pwd")
        direction = get_default_business_direction()
        self.application = Application.objects.create(
            code="app-secure-export",
            name="Application Secure Export",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-SECURE-EXPORT",
            title="DAT Secure Export",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.regular,
            secure_export_requires_dual_admin_approval=True,
        )
        DATAdmin.objects.create(dat=self.dat, user=self.admin_1)
        DATAdmin.objects.create(dat=self.dat, user=self.admin_2)
        DATAdmin.objects.create(dat=self.dat, user=self.admin_3)

    def test_only_explicit_dat_admin_can_create_secure_request(self):
        self.client.force_login(self.regular)
        response = self.client.post(reverse("dat:my_export_secure_request", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.admin_1)
        response = self.client.post(reverse("dat:my_export_secure_request", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 302)
        request_obj = DATExportAccessRequest.objects.get(dat=self.dat)
        self.assertEqual(request_obj.status, DATExportAccessRequestStatus.PENDING)
        self.assertEqual(request_obj.required_approvals, 2)
        self.assertEqual(request_obj.approvals.count(), 1)
        self.assertEqual(request_obj.approvals.first().approved_by, self.admin_1)
        self.assertTrue(
            DATExportAccessHistory.objects.filter(
                dat=self.dat,
                request=request_obj,
                event_type=DATExportAccessEventType.REQUEST_CREATED,
            ).exists()
        )

    @mock.patch("dat.views.get_dat_export_model_builder")
    def test_json_download_requires_two_approvals_and_only_approvers_can_download(self, get_builder):
        class _Builder:
            def build(self, dat):
                return {"reference": dat.reference}

        get_builder.return_value = _Builder()
        self.client.force_login(self.admin_1)
        self.client.post(reverse("dat:my_export_secure_request", args=[self.dat.pk]))
        response = self.client.get(reverse("dat:my_export_json", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.admin_2)
        self.client.post(reverse("dat:my_export_secure_approve", args=[self.dat.pk]))
        self.client.force_login(self.admin_1)
        response = self.client.get(reverse("dat:my_export_json", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reference"], self.dat.reference)

        self.client.force_login(self.admin_2)
        response = self.client.get(reverse("dat:my_export_json", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reference"], self.dat.reference)

        self.client.force_login(self.admin_3)
        response = self.client.get(reverse("dat:my_export_json", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 302)

        self.assertTrue(
            DATExportAccessHistory.objects.filter(
                dat=self.dat,
                event_type=DATExportAccessEventType.DOWNLOAD_JSON,
                actor=self.admin_2,
            ).exists()
        )

    def test_requester_cannot_add_second_approval_secure_request(self):
        self.client.force_login(self.admin_1)
        self.client.post(reverse("dat:my_export_secure_request", args=[self.dat.pk]))
        response = self.client.post(reverse("dat:my_export_secure_approve", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 403)
        request_obj = DATExportAccessRequest.objects.get(dat=self.dat)
        self.assertEqual(request_obj.status, DATExportAccessRequestStatus.PENDING)
        self.assertEqual(request_obj.approvals.count(), 1)
        self.assertEqual(request_obj.approvals.first().approved_by, self.admin_1)

    @mock.patch("dat.views.open_dat_pdf_export")
    def test_pdf_download_allowed_for_approvers_within_one_hour(self, open_export):
        open_export.return_value = BytesIO(b"%PDF-1.4 test")
        self.client.force_login(self.admin_1)
        self.client.post(reverse("dat:my_export_secure_request", args=[self.dat.pk]))
        self.client.force_login(self.admin_2)
        self.client.post(reverse("dat:my_export_secure_approve", args=[self.dat.pk]))

        request_obj = DATExportAccessRequest.objects.get(dat=self.dat)
        self.assertEqual(request_obj.status, DATExportAccessRequestStatus.APPROVED)
        self.assertIsNotNone(request_obj.access_valid_until)
        self.assertGreater(request_obj.access_valid_until, timezone.now() + timedelta(minutes=59))

        self.client.force_login(self.admin_2)
        response = self.client.get(reverse("dat:my_export_pdf_download", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)

        self.client.force_login(self.admin_3)
        response = self.client.get(reverse("dat:my_export_pdf_download", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 302)

    def test_status_endpoint_returns_secure_export_payload(self):
        self.client.force_login(self.admin_1)
        self.client.post(reverse("dat:my_export_secure_request", args=[self.dat.pk]))

        response = self.client.get(reverse("dat:my_export_pdf_status", args=[self.dat.pk]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("secure_export", payload)
        secure = payload["secure_export"]
        self.assertTrue(secure["enabled"])
        self.assertTrue(secure["is_pending"])
        self.assertEqual(secure["approval_count"], 1)
        self.assertTrue(secure["user_is_explicit_admin"])


class DatReserveNotificationTest(TestCase):
    def setUp(self) -> None:
        self.admin = get_user_model().objects.create_user(
            username="reserve-admin",
            password="pwd",
            is_staff=True,
        )
        self.manager = get_user_model().objects.create_user(
            username="reserve-manager",
            password="pwd",
        )
        self.assignee = get_user_model().objects.create_user(
            username="reserve-assignee",
            password="pwd",
        )
        self.role_architecture = ensure_role("architecte-technique", "Architecte technique")
        self.assignee.role = self.role_architecture
        self.assignee.save(update_fields=["role"])
        self.group = BusinessGroup.objects.create(
            name="Groupe reserve",
            direction=get_default_technical_direction(),
            responsible=self.manager,
        )
        self.assignee.business_group = self.group
        self.assignee.save(update_fields=["business_group"])
        self.application = Application.objects.create(
            code="app-reserve",
            name="Application Reserve",
            business_direction=get_default_business_direction(),
        )
        self.dat = DAT.objects.create(
            reference="DAT-RESERVE",
            title="DAT Reserve",
            application=self.application,
            status=DATStatus.EN_COURS,
            owner=self.assignee,
        )
        sync_dat_sections_if_needed(self.dat)
        DATParticipant.objects.create(dat=self.dat, role=self.role_architecture, user=self.assignee)
        section = self.dat.sections.get(metadata__slug="architecture")
        DATSectionParticipant.objects.create(dat=self.dat, section=section, user=self.assignee)
        DATSectionResponsible.objects.create(dat=self.dat, section=section, user=self.manager)
        self.section_slug = "architecture"
        self.reserve_url = reverse("dat:section_reserve", args=[self.dat.pk, self.section_slug])
        self.status_url = reverse("dat:section_status", args=[self.dat.pk, self.section_slug])

    def _set_reserve(self):
        self.client.force_login(self.assignee)
        response = self.client.post(
            self.reserve_url,
            data={"reserve_message": "Corriger la section."},
        )
        self.assertEqual(response.status_code, 302)

    def test_reserve_notifies_manager(self):
        self._set_reserve()
        self.assertTrue(
            UserNotification.objects.filter(
                user=self.manager,
                notification_type__title="Réserve sur votre section",
            ).exists()
        )

    def test_validation_notifies_reserve_author(self):
        self._set_reserve()
        self.client.force_login(self.manager)
        response = self.client.post(
            self.status_url,
            data={"status": SECTION_STATUS_VALIDATED_VALUE},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            UserNotification.objects.filter(
                user=self.assignee,
                notification_type__title="Réserve à lever",
            ).exists()
        )


class SectionStatusGroupResponsibleTest(TestCase):
    def setUp(self) -> None:
        User = get_user_model()
        self.manager = User.objects.create_user(username="group-manager", password="pwd")
        self.technical_direction = get_default_technical_direction()
        self.role_architecture = ensure_role("architecte-technique", "Architecte technique")
        self.group = BusinessGroup.objects.create(
            name="Groupe technique test",
            direction=self.technical_direction,
            responsible=self.manager,
        )
        self.member = User.objects.create_user(
            username="archi-user",
            password="pwd",
            role=self.role_architecture,
            business_group=self.group,
        )
        self.application = Application.objects.create(
            code="app-group",
            name="Application Groupe",
            business_direction=get_default_business_direction(),
        )
        self.dat = DAT.objects.create(
            reference="DAT-GROUP-001",
            title="DAT group validation",
            application=self.application,
            status=DATStatus.EN_COURS,
            owner=self.member,
        )
        DATParticipant.objects.create(dat=self.dat, role=self.role_architecture, user=self.member)
        sync_dat_sections_if_needed(self.dat)
        architecture_section = self.dat.sections.get(metadata__slug="architecture")
        DATSectionParticipant.objects.create(dat=self.dat, section=architecture_section, user=self.member)

    def test_assigned_user_can_view_dat_and_validate_architecture(self):
        self.client.force_login(self.member)
        detail_url = reverse("dat:my_detail", args=[self.dat.pk]) + "?section=validation"
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("dat:section_status", args=[self.dat.pk, "architecture"]),
            {"status": "valide"},
        )
        self.assertEqual(response.status_code, 302)
        from .views import build_section_status_map

        status_map, _choices = build_section_status_map(DAT.objects.get(pk=self.dat.pk))
        self.assertEqual(status_map["architecture"]["value"], "valide")

    def test_group_responsible_cannot_validate_architecture_when_unassigned(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("dat:section_status", args=[self.dat.pk, "architecture"]),
            {"status": "valide"},
        )
        self.assertEqual(response.status_code, 403)


class DrawioParserTests(SimpleTestCase):
    def test_clean_model_xml_extracts_mxgraphmodel(self):
        payload = "junk <mxGraphModel><root /></mxGraphModel>"
        cleaned = _clean_model_xml(payload)
        self.assertIsNotNone(cleaned)
        self.assertTrue(cleaned.startswith("<mxGraphModel"))

    def test_extract_drawio_pages_from_model(self):
        xml = "<mxGraphModel><root /></mxGraphModel>"
        pages = extract_drawio_pages(xml)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["name"], "Page 1")

    def test_parse_architecture_diagram_builds_rows(self):
        xml = """
        <mxGraphModel>
            <root>
                <object id="1" objectType="brique" idBrique="B1" labelBrique="Service A" description="Desc" />
                <object id="2" objectType="brique" idBrique="B2" labelBrique="Service B" />
                <object id="3" objectType="flux" idFlux="F1" source="1" target="2" protocole="https" port="443" mecanismeAuth="certificat" />
            </root>
        </mxGraphModel>
        """
        briques, fluxes = parse_architecture_diagram(xml)
        self.assertEqual(len(briques), 2)
        self.assertEqual(len(fluxes), 1)
        self.assertEqual(fluxes[0]["source"], "Service A")
        self.assertEqual(fluxes[0]["cible"], "Service B")
        self.assertEqual(fluxes[0]["chiffrement"], "oui")
        self.assertEqual(fluxes[0]["authentification"], "oui")

    def test_dedupe_architecture_rows_merges_values(self):
        briques = [
            {"brique_id": "A", "nom": "", "description": "Desc A"},
            {"brique_id": "A", "nom": "Service A", "description": ""},
        ]
        fluxes = []
        deduped_briques, _deduped_fluxes = dedupe_architecture_rows(briques, fluxes)
        self.assertEqual(len(deduped_briques), 1)
        self.assertEqual(deduped_briques[0]["nom"], "Service A")
        self.assertEqual(deduped_briques[0]["description"], "Desc A")


class DatPermissionsTests(TestCase):
    def setUp(self) -> None:
        self.manager = get_user_model().objects.create_user(username="perm-manager", password="pwd", is_staff=True)
        self.responsible = get_user_model().objects.create_user(username="perm-resp", password="pwd")
        self.member = get_user_model().objects.create_user(username="perm-member", password="pwd")
        self.role_architecture = ensure_role("architecte-technique", "Architecte technique")
        self.group = BusinessGroup.objects.create(
            name="Perm Group",
            direction=get_default_technical_direction(),
            responsible=self.responsible,
        )
        self.member.role = self.role_architecture
        self.member.business_group = self.group
        self.member.save(update_fields=["role", "business_group"])
        self.application = Application.objects.create(
            code="perm-app",
            name="Perm App",
            business_direction=get_default_business_direction(),
        )
        self.dat = DAT.objects.create(
            reference="DAT-PERM-1",
            title="Perm DAT",
            application=self.application,
            status=DATStatus.EN_COURS,
            owner=self.member,
        )
        sync_dat_sections_if_needed(self.dat)
        DATParticipant.objects.create(dat=self.dat, role=self.role_architecture, user=self.member)
        self.section = self.dat.sections.get(metadata__slug="architecture")
        self.section.allowed_roles.set([self.role_architecture])
        DATSectionParticipant.objects.create(dat=self.dat, section=self.section, user=self.member)

    def test_user_is_dat_admin_for_staff(self):
        self.assertTrue(user_is_dat_admin(self.manager))

    def test_responsible_can_update_section(self):
        self.assertTrue(user_is_responsible_for_section(self.dat, self.section, self.responsible))

    def test_user_can_update_section_status_for_assignee(self):
        self.assertTrue(user_can_update_section_status(self.dat, self.section, self.member))

    def test_user_can_update_section_status_for_section_responsible(self):
        DATSectionResponsible.objects.create(dat=self.dat, section=self.section, user=self.responsible)
        self.assertTrue(user_can_update_section_status(self.dat, self.section, self.responsible))

    def test_unassigned_user_cannot_update_section_status(self):
        other = get_user_model().objects.create_user(username="perm-unassigned", password="pwd")
        self.assertFalse(user_can_update_section_status(self.dat, self.section, other))

    def test_filter_dat_queryset_for_user(self):
        other = get_user_model().objects.create_user(username="perm-other", password="pwd")
        other_dat = DAT.objects.create(
            reference="DAT-PERM-2",
            title="Other DAT",
            application=self.application,
            status=DATStatus.EN_COURS,
            owner=other,
        )
        queryset = filter_dat_queryset_for_user(DAT.objects.all(), self.member)
        self.assertIn(self.dat, list(queryset))
        self.assertNotIn(other_dat, list(queryset))

    def test_dat_owner_cannot_edit_section_without_explicit_section_assignment(self):
        owner = get_user_model().objects.create_user(username="perm-owner-only", password="pwd")
        dat = DAT.objects.create(
            reference="DAT-PERM-OWNER-1",
            title="Owner Perm DAT",
            application=self.application,
            status=DATStatus.EN_COURS,
            owner=owner,
        )
        sync_dat_sections_if_needed(dat)
        section = dat.sections.get(metadata__slug="architecture")
        section.allowed_roles.set([self.role_architecture])
        sub_section = section.sub_sections.order_by("order", "id").first()
        self.assertIsNotNone(sub_section)
        self.assertFalse(section.can_user_edit(owner))
        self.assertFalse(sub_section.can_user_edit(owner))

    def test_dat_admin_cannot_edit_section_without_explicit_section_assignment(self):
        dat_admin = get_user_model().objects.create_user(username="perm-dat-admin-only", password="pwd")
        DATAdmin.objects.create(dat=self.dat, user=dat_admin)
        sub_section = self.section.sub_sections.order_by("order", "id").first()
        self.assertIsNotNone(sub_section)
        self.assertFalse(self.section.can_user_edit(dat_admin))
        self.assertFalse(sub_section.can_user_edit(dat_admin))


class DatSectionsSyncTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="sync-user", password="pwd")
        self.application = Application.objects.create(
            code="sync-app",
            name="Sync App",
            business_direction=get_default_business_direction(),
        )
        self.dat = DAT.objects.create(
            reference="DAT-SYNC-1",
            title="Sync DAT",
            application=self.application,
            status=DATStatus.NOUVELLE_DEMANDE,
            owner=self.user,
        )
        sync_dat_sections_if_needed(self.dat)

    def test_dat_sections_need_sync_detects_changes(self):
        self.assertFalse(dat_sections_need_sync(self.dat))
        section = self.dat.sections.select_related("metadata").first()
        self.assertIsNotNone(section)
        if section:
            section.metadata.title = "Modified"
            section.metadata.save(update_fields=["title"])
        self.assertTrue(dat_sections_need_sync(self.dat))

    def test_sync_dat_sections_if_needed_applies_updates(self):
        section = self.dat.sections.select_related("metadata").first()
        self.assertIsNotNone(section)
        if section:
            section.metadata.title = "Modified"
            section.metadata.save(update_fields=["title"])
        self.assertTrue(sync_dat_sections_if_needed(self.dat))
        self.assertFalse(dat_sections_need_sync(self.dat))
