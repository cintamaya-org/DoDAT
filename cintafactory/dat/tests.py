# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

import base64
import json
import uuid
import zlib
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cintafactory.logging.logging_utils import bind_request_context, clear_request_context

from diagrams.models import DrawIODiagram
from users.models import BusinessDirection, BusinessGroup, Role, TechnicalDirection

from . import views as dat_views
from .constants import (
    DAT_PORTEUR_ROLE_SLUG,
    DAT_REQUIRED_PARTICIPANT_ROLE_LABELS,
    DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS,
)
from .exporters import DATExportModelBuilder
from .forms import DATForm, DATImportForm, DATSubSectionForm, RepeatableTableWidget, build_dat_part_field
from .importers import DATImportError, DATImportService
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
    DATPartEntry,
    DATPartEntryType,
    DATPartPayload,
    DATSection,
    DATSectionMetadata,
    DATSectionParticipant,
    DATSectionResponsible,
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
from .drawio_parser import (
    _clean_model_xml,
    _inflate_drawio_payload,
    dedupe_architecture_rows,
    extract_drawio_pages,
    parse_architecture_diagram,
)
from .drawio_parser import MAX_XML_CHARS
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


class DatCreationNotificationTest(SimpleTestCase):
    def test_dat_creation_emits_one_success_notification(self):
        from .views import DATCreateView

        request = RequestFactory().post("/dat/manage/dats/crud/add/")
        request.user = mock.Mock(
            is_authenticated=True,
            is_staff=False,
            is_superuser=False,
        )
        request.user.is_role.return_value = False
        request.session = {}
        request._messages = FallbackStorage(request)

        view = DATCreateView()
        view.setup(request)
        view.model = DAT
        form = mock.Mock()
        form.save.return_value = DAT(
            pk="4a83e2e8-d58a-4f43-a6fa-b61f707805d0",
            reference="DAT-TEST",
            title="Test",
        )

        response = view.form_valid(form)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [str(message) for message in get_messages(request)],
            ["Le DAT a été créé avec succès."],
        )


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


class DatPartFieldCoverageTests(SimpleTestCase):
    @staticmethod
    def _part(data_type, config=None, *, required=False):
        return SimpleNamespace(
            data_type=data_type,
            config=config or {},
            required=required,
            label="Test field",
        )

    def test_choice_text_and_long_text_fields_use_configuration(self):
        single = build_dat_part_field(
            self._part(
                DATPartEntryType.TEXT,
                {"choices": [{"value": "a", "label": "Alpha"}], "widget": "radio"},
            )
        )
        self.assertEqual(single.choices, [("a", "Alpha")])
        self.assertEqual(single.widget.__class__.__name__, "MaterialRadioSelect")

        multiple = build_dat_part_field(
            self._part(
                DATPartEntryType.TEXT,
                {"choices": [{"value": "a"}], "multiple": True, "widget": "checkboxes"},
            )
        )
        self.assertEqual(multiple.choices, [("a", "a")])
        self.assertEqual(multiple.widget.__class__.__name__, "MaterialCheckboxSelectMultiple")

        text = build_dat_part_field(
            self._part(DATPartEntryType.TEXT, {"max_length": 15, "pattern": "[A-Z]+", "pattern_message": "Caps"})
        )
        self.assertEqual(text.max_length, 15)
        self.assertEqual(text.widget.attrs["pattern"], "[A-Z]+")
        self.assertEqual(text.widget.attrs["title"], "Caps")
        self.assertTrue(text.validators)

        long_text = build_dat_part_field(self._part(DATPartEntryType.LONG_TEXT, {"rows": 5}))
        self.assertEqual(long_text.widget.attrs["rows"], 5)
        default_long_text = build_dat_part_field(self._part(DATPartEntryType.LONG_TEXT))
        self.assertEqual(default_long_text.widget.attrs["style"], "height:160px;")

    def test_supported_part_types_and_repeatable_widget_options(self):
        cases = [
            (DATPartEntryType.INTEGER, forms.IntegerField),
            (DATPartEntryType.DECIMAL, forms.DecimalField),
            (DATPartEntryType.DATE, forms.DateField),
            (DATPartEntryType.BOOLEAN, forms.BooleanField),
            (DATPartEntryType.JSON, forms.JSONField),
            (DATPartEntryType.URL, forms.URLField),
            ("unknown-type", forms.CharField),
        ]
        for data_type, expected_type in cases:
            with self.subTest(data_type=data_type):
                field = build_dat_part_field(self._part(data_type))
                self.assertIsInstance(field, expected_type)

        repeater = build_dat_part_field(
            self._part(
                DATPartEntryType.REPEATER,
                {"columns": [{"key": "name"}], "min_rows": 1, "max_rows": 4, "allow_row_removal": False},
            )
        )
        self.assertIsInstance(repeater.widget, RepeatableTableWidget)
        self.assertEqual(repeater.widget.min_rows, 1)
        self.assertEqual(repeater.widget.max_rows, 4)
        self.assertFalse(repeater.widget.allow_row_removal)

    def test_repeatable_widget_normalizes_empty_and_json_values(self):
        widget = RepeatableTableWidget(columns=[{"key": "service"}], min_rows=1)
        self.assertEqual(widget.format_value(None), [])
        self.assertEqual(widget.format_value(""), [])
        self.assertEqual(widget.get_context("parts", "not-json", {})["widget"]["value"], [])
        context = widget.get_context("parts", '[{"service":"API"}]', {})["widget"]
        self.assertEqual(context["value"], [{"service": "API"}])
        self.assertEqual(context["columns"], [{"key": "service"}])
        self.assertEqual(context["min_rows"], 1)

    def test_subsection_form_saves_only_changed_values_and_rejects_invalid_save(self):
        part = SimpleNamespace(
            data_type=DATPartEntryType.TEXT,
            config={},
            required=True,
            label="Title",
            key="title",
            value="before",
            form_field_name=lambda: "part_title",
            initial_value=lambda: "before",
            prepare_value=lambda value: value.strip(),
            render_value=lambda value: value,
            update_value=None,
        )
        part.update_value = mock.Mock(side_effect=lambda value: setattr(part, "value", value))
        sub_section = SimpleNamespace(
            title="Architecture",
            parts=SimpleNamespace(order_by=lambda *_args: [part]),
        )
        form = DATSubSectionForm(sub_section, data={"part_title": " after "})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(next(form.iter_entries())["field_name"], "part_title")
        changes = form.save()
        self.assertEqual(changes["title"]["from"], "before")
        self.assertEqual(changes["title"]["to"], "after")
        part.update_value.assert_called_once_with("after")

        part.value = "before"
        part.update_value.reset_mock()
        unchanged = DATSubSectionForm(sub_section, data={"part_title": "before"})
        self.assertTrue(unchanged.is_valid(), unchanged.errors)
        self.assertEqual(unchanged.save(), {})
        invalid = DATSubSectionForm(sub_section, data={"part_title": ""})
        with self.assertRaisesMessage(ValueError, "Cannot save an invalid form."):
            invalid.save()


class DatImportFormCoverageTests(SimpleTestCase):
    @staticmethod
    def _upload(payload, *, name="dat.json"):
        return SimpleUploadedFile(name, payload, content_type="application/json")

    def test_valid_json_import_exposes_payload_and_resets_file_position(self):
        upload = self._upload(b'{"reference":"DAT-1"}')
        form = DATImportForm(files={"data_file": upload})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.payload, {"reference": "DAT-1"})
        self.assertEqual(form.cleaned_data["data_file"].tell(), 0)

    def test_import_rejects_non_utf8_invalid_json_and_non_object_payloads(self):
        cases = [
            (b"\xff", "UTF-8"),
            (b"{invalid", "JSON valide"),
            (b"[]", "objet JSON"),
        ]
        for content, message in cases:
            with self.subTest(content=content):
                form = DATImportForm(files={"data_file": self._upload(content)})
                self.assertFalse(form.is_valid())
                self.assertIn(message, str(form.errors["data_file"]))

    @mock.patch("dat.forms.DAT.objects.filter")
    def test_import_rejects_duplicate_reference_and_accepts_new_reference(self, filter_dat):
        filter_dat.return_value.exists.return_value = True
        duplicate = DATImportForm(
            data={"reference_override": "  DAT-EXISTS  "},
            files={"data_file": self._upload(b"{}")},
        )
        self.assertFalse(duplicate.is_valid())
        self.assertIn("reference_override", duplicate.errors)
        filter_dat.assert_called_with(reference="DAT-EXISTS")

        filter_dat.return_value.exists.return_value = False
        unique = DATImportForm(
            data={"reference_override": " DAT-NEW "},
            files={"data_file": self._upload(b"{}")},
        )
        self.assertTrue(unique.is_valid(), unique.errors)
        self.assertEqual(unique.cleaned_data["reference_override"], "DAT-NEW")


class DatViewHelperCoverageTests(SimpleTestCase):
    def test_section_status_map_normalizes_rows_and_uses_custom_choices(self):
        status_part = DATPart(
            key="suivi_sections",
            label="Section status",
            config={
                "columns": [
                    {
                        "key": "statut",
                        "choices": [
                            {"value": "todo", "label": "À faire"},
                            {"value": "blocked", "label": "Bloqué"},
                            None,
                            {"value": ""},
                        ],
                    }
                ]
            },
        )
        status_part._current_entry_cache = SimpleNamespace(
            resolved_value=[
                {
                    "section": "Ancien titre",
                    "section_slug": "architecture",
                    "statut": "removed-choice",
                    "statut_responsable": "en_cours",
                    "reserve_message": "  ",
                    "reserve_by_id": 42,
                    "reserve_by_display": "Admin",
                    "commentaire": "À corriger",
                },
                {"section": "Urbanisme", "statut": "blocked"},
                "invalid row",
                {"unknown": "stale"},
            ]
        )
        status_part.update_value = mock.Mock()
        sections = [
            SimpleNamespace(slug="architecture", title="Architecture"),
            SimpleNamespace(slug="informations-generales", title="Informations générales"),
            SimpleNamespace(slug="urbanisme", title="Urbanisme"),
        ]

        with mock.patch.object(dat_views, "_find_section_status_part", return_value=status_part):
            status_map, choices = dat_views.build_section_status_map(
                SimpleNamespace(), sections_list=sections
            )

        self.assertEqual(choices, {"todo": "À faire", "blocked": "Bloqué"})
        self.assertEqual(status_map["architecture"]["value"], "todo")
        self.assertEqual(status_map["architecture"]["responsable_value"], "todo")
        self.assertEqual(status_map["architecture"]["commentaire"], "À corriger")
        self.assertEqual(status_map["architecture"]["reserve_by_id"], None)
        self.assertFalse(status_map["informations-generales"]["has_status"])
        self.assertEqual(status_map["urbanisme"]["value"], "blocked")
        status_part.update_value.assert_called_once()
        normalized_rows = status_part.update_value.call_args.args[0]
        self.assertEqual([row["section_slug"] for row in normalized_rows], ["architecture", "urbanisme"])
        self.assertEqual(normalized_rows[0]["section"], "Architecture")

    def test_section_status_map_leaves_canonical_rows_unchanged_and_handles_missing_part(self):
        section = SimpleNamespace(slug="architecture", title="Architecture")
        status_part = DATPart(key="suivi_sections", label="Section status", config={})
        status_part._current_entry_cache = SimpleNamespace(
            resolved_value=[
                {
                    "section": "Architecture",
                    "section_slug": "architecture",
                    "statut": "en_cours",
                    "statut_responsable": "en_cours",
                    "reserve_message": "",
                    "reserve_by_id": None,
                    "reserve_by_display": "",
                    "commentaire": "",
                }
            ]
        )
        status_part.update_value = mock.Mock()

        with mock.patch.object(dat_views, "_find_section_status_part", return_value=status_part):
            status_map, _choices = dat_views.build_section_status_map(
                SimpleNamespace(), sections_list=[section]
            )
        self.assertEqual(status_map["architecture"]["label"], "En cours")
        status_part.update_value.assert_not_called()

        with mock.patch.object(dat_views, "_find_section_status_part", return_value=None):
            fallback_map, fallback_choices = dat_views.build_section_status_map(
                SimpleNamespace(), sections_list=[section]
            )
        self.assertEqual(fallback_map["architecture"]["value"], "en_cours")
        self.assertIn("valide", fallback_choices)

    def test_status_part_lookup_and_default_selection_cover_related_and_fallback_paths(self):
        part = SimpleNamespace(key="suivi_sections")
        subsection = SimpleNamespace(parts=SimpleNamespace(all=lambda: [SimpleNamespace(key="other"), part]))
        validation = SimpleNamespace(
            slug="validation",
            sub_sections=SimpleNamespace(all=lambda: [subsection]),
        )
        self.assertIs(dat_views._find_section_status_part(SimpleNamespace(), [validation]), part)

        query = mock.Mock()
        query.filter.return_value.first.return_value = part
        with mock.patch.object(dat_views.DATPart.objects, "select_related", return_value=query) as select:
            self.assertIs(dat_views._find_section_status_part(SimpleNamespace(), []), part)
        select.assert_called_once_with("sub_section__section")

        with mock.patch.object(dat_views.DATPart.objects, "select_related", side_effect=RuntimeError("db down")):
            self.assertIsNone(dat_views._find_section_status_part(SimpleNamespace(), []))
        self.assertEqual(dat_views._default_status_value({"custom": "Custom"}), "custom")
        self.assertEqual(dat_views._default_status_value({}), "en_cours")

    def test_schema_metadata_extractors_and_reserve_status_reset(self):
        schema_part = SimpleNamespace(
            value=[
                {"diagramme_id": "21cf4380-3a36-4885-a4d7-fb56ebf04c7f"},
                {"diagramme_id": "21cf4380-3a36-4885-a4d7-fb56ebf04c7f"},
                "invalid",
                {"schema_systeme": "LikeC4", "schema_reference": "models/a.c4"},
                {"schema_systeme": "likec4", "schema_reference": "/models/a.c4"},
                {"schema_systeme": "drawio", "schema_reference": "ignored.c4"},
                {"schema_systeme": "likec4", "schema_reference": "../outside.c4"},
            ]
        )
        subsection = SimpleNamespace(
            parts=SimpleNamespace(filter=lambda **_kwargs: SimpleNamespace(first=lambda: schema_part))
        )
        self.assertEqual(
            dat_views._extract_schema_diagram_ids(subsection),
            [uuid.UUID("21cf4380-3a36-4885-a4d7-fb56ebf04c7f")],
        )
        self.assertEqual(dat_views._extract_schema_likec4_paths(subsection), ["models/a.c4"])
        self.assertEqual(dat_views._extract_schema_diagram_ids(None), [])
        self.assertEqual(dat_views._extract_schema_likec4_paths(None), [])
        empty_subsection = SimpleNamespace(
            parts=SimpleNamespace(filter=lambda **_kwargs: SimpleNamespace(first=lambda: None))
        )
        self.assertEqual(dat_views._extract_schema_diagram_ids(empty_subsection), [])
        self.assertEqual(dat_views._extract_schema_likec4_paths(empty_subsection), [])

        status_part = SimpleNamespace(update_value=mock.Mock())
        sections = [
            SimpleNamespace(slug="architecture", title="Architecture"),
            SimpleNamespace(slug="informations-generales", title="Informations générales"),
        ]
        dat = SimpleNamespace(
            sections=SimpleNamespace(
                order_by=lambda *_args: SimpleNamespace(
                    select_related=lambda *_args: sections
                )
            )
        )
        with mock.patch.object(dat_views, "_find_section_status_part", return_value=status_part):
            dat_views.reset_section_statuses_to_default(
                dat,
                status_map={"architecture": {"commentaire": "Keep this note"}},
                status_choices={"custom": "Custom"},
            )
        rows = status_part.update_value.call_args.args[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["statut"], "custom")
        self.assertEqual(rows[0]["commentaire"], "Keep this note")
        self.assertEqual(rows[0]["reserve_by_id"], None)

    def test_workflow_node_statuses_covers_section_messages_and_capability_states(self):
        nodes = [
            None,
            {"scope": "section", "id": "missing-section"},
            {"scope": "section", "id": "validated", "section": "validated"},
            {"scope": "section", "id": "blocked", "section": "blocked"},
            {"scope": "section", "id": "reserved", "section": "reserved"},
            {"scope": "section", "id": "unknown", "section": "unknown"},
            {"scope": "workflow", "id": "validation"},
            {"scope": "workflow", "id": ""},
        ]
        status_map = {
            "validated": {"value": "valide"},
            "blocked": {"value": "bloque", "commentaire": "Corriger le dossier"},
            "reserved": {"value": "en_cours", "reserve_message": "En réserve"},
            "unknown": {"value": "legacy"},
        }
        capability_cases = [
            ({"approved"}, ("validated", "task_alt")),
            ({"rejected"}, ("blocked", "gpp_maybe")),
            ({"requires_corrections"}, ("blocked", "gpp_maybe")),
            ({"reviewable"}, ("review", "rate_review")),
            (set(), ("in_progress", "pending_actions")),
        ]
        for capabilities, expected in capability_cases:
            with self.subTest(capabilities=capabilities):
                with mock.patch.object(
                    dat_views,
                    "workflow_has_capability",
                    side_effect=lambda _dat, capability: capability in capabilities,
                ):
                    statuses = dat_views.build_workflow_node_statuses(
                        SimpleNamespace(),
                        status_map,
                        {"nodes": nodes},
                    )
                self.assertEqual((statuses["validation"]["tone"], statuses["validation"]["icon"]), expected)
                self.assertEqual(statuses["validated"]["tone"], "validated")
                self.assertEqual(statuses["blocked"]["message"], "Corriger le dossier")
                self.assertEqual(statuses["blocked"]["message_kind"], "blocked")
                self.assertEqual(statuses["reserved"]["message_kind"], "reserve")
                self.assertEqual(statuses["unknown"]["tone"], "unknown")
                self.assertNotIn("missing-section", statuses)

    def test_section_lock_only_checks_terminal_workflow_capability(self):
        status_info = {"value": "bloque"}
        self.assertFalse(dat_views.section_is_locked(status_info))
        with mock.patch.object(dat_views, "workflow_has_capability", return_value=False):
            self.assertFalse(dat_views.section_is_locked(status_info, dat=SimpleNamespace()))
        with mock.patch.object(dat_views, "workflow_has_capability", return_value=True) as has_capability:
            self.assertTrue(dat_views.section_is_locked(status_info, dat=SimpleNamespace()))
        has_capability.assert_called_once_with(mock.ANY, "terminal")

    def test_schema_normalizers_and_protocol_detection_handle_invalid_and_duplicate_values(self):
        first = uuid.uuid4()
        second = uuid.uuid4()
        self.assertEqual(
            dat_views._normalize_diagram_ids([str(first), first, "invalid", None, str(second)]),
            [first, second],
        )
        self.assertEqual(dat_views._normalize_diagram_ids("not-a-list"), [])
        self.assertEqual(dat_views._normalize_likec4_path(" /models/context.c4 "), "models/context.c4")
        self.assertEqual(dat_views._normalize_likec4_path("../outside.c4"), "")
        self.assertEqual(dat_views._normalize_likec4_path("model.txt"), "")
        self.assertEqual(
            dat_views._normalize_likec4_paths(["models/a.c4", "/models/a.c4", "", "bad.json"]),
            ["models/a.c4"],
        )
        self.assertEqual(dat_views._normalize_likec4_paths("models/a.c4"), [])
        self.assertEqual(dat_views._guess_likec4_protocol("HTTPS/443"), ("https", "443"))
        self.assertEqual(dat_views._guess_likec4_protocol("uses grpc"), ("grpc", ""))
        self.assertEqual(dat_views._guess_likec4_protocol("business event"), ("", ""))
        self.assertEqual(dat_views._guess_likec4_protocol("   "), ("", ""))

    def test_likec4_flow_matrix_maps_components_protocols_and_unknown_endpoints(self):
        components, flows = dat_views._likec4_rows_from_flow_matrix(
            {
                "components": [
                    {"name": "api", "title": "API", "props": {"description": "Front door"}},
                    {"name": "db", "metadata": {"commentaire": "Storage"}},
                    {"title": "Queue"},
                    {"name": "", "title": ""},
                    "invalid",
                ],
                "flows": [
                    {"from": "api", "to": "db", "label": "HTTPS/443"},
                    {"from": "new-service", "to": "API", "label": "event stream"},
                    {"from": "", "to": "db", "label": "ignored"},
                    None,
                ],
            }
        )
        self.assertEqual([row["nom"] for row in components], ["API", "db", "Queue", "new-service"])
        self.assertEqual(components[0]["description"], "Front door")
        self.assertEqual(components[1]["description"], "Storage")
        self.assertEqual(flows[0]["source"], "API")
        self.assertEqual(flows[0]["cible"], "db")
        self.assertEqual((flows[0]["protocole"], flows[0]["port"], flows[0]["chiffrement"]), ("https", "443", "oui"))
        self.assertEqual(flows[1]["flux_id"], "event stream")
        self.assertEqual(len(flows), 2)
        self.assertEqual(dat_views._likec4_rows_from_flow_matrix([]), ([], []))

    def test_flow_matrix_fetch_handles_configuration_responses_and_network_errors(self):
        with override_settings(LIKEC4_EDITOR_URL=""):
            self.assertIsNone(dat_views._fetch_likec4_flow_matrix("model.c4"))
        with override_settings(LIKEC4_EDITOR_URL="file:///tmp/editor"):
            self.assertIsNone(dat_views._fetch_likec4_flow_matrix("model.c4"))

        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"flows": []}'
        with override_settings(
            LIKEC4_EDITOR_URL="https://editor.example/",
            LIKEC4_API_TOKEN="test-token",
        ), mock.patch("dat.views.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            self.assertEqual(dat_views._fetch_likec4_flow_matrix("models/one.c4"), {"flows": []})
            request = urlopen.call_args.args[0]
            self.assertEqual(urlsplit(request.full_url).path, "/flow-matrix")
            self.assertEqual(parse_qs(urlsplit(request.full_url).query), {"file": ["models/one.c4"]})
            self.assertEqual(request.get_header("X-likec4-token"), "test-token")

            response.status = 503
            self.assertIsNone(dat_views._fetch_likec4_flow_matrix("models/one.c4"))
            response.status = 200
            response.read.return_value = b"not-json"
            self.assertIsNone(dat_views._fetch_likec4_flow_matrix("models/one.c4"))

            urlopen.side_effect = HTTPError(
                "https://editor.example/flow-matrix",
                500,
                "failed",
                hdrs=None,
                fp=BytesIO(b"error body"),
            )
            self.assertIsNone(dat_views._fetch_likec4_flow_matrix("models/one.c4"))
            urlopen.side_effect = OSError("network unavailable")
            self.assertIsNone(dat_views._fetch_likec4_flow_matrix("models/one.c4"))

    def test_drawio_repeater_detection_and_repeater_update_report_changes(self):
        sub_section = SimpleNamespace(
            parts=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(data_type=DATPartEntryType.TEXT, config={"columns": [{"drawio": True}]}),
                    SimpleNamespace(data_type=DATPartEntryType.REPEATER, config={"columns": [{"drawio": False}]}),
                    SimpleNamespace(data_type=DATPartEntryType.REPEATER, config={"columns": [{"drawio": True}]}),
                ]
            )
        )
        self.assertTrue(dat_views._sub_section_has_drawio_repeater(sub_section))
        self.assertFalse(
            dat_views._sub_section_has_drawio_repeater(
                SimpleNamespace(parts=SimpleNamespace(all=lambda: []))
            )
        )

        part = SimpleNamespace(
            key="schemas",
            label="Schemas",
            sub_section=SimpleNamespace(title="Architecture"),
            data_type=DATPartEntryType.REPEATER,
            value=[{"name": "Before"}],
            prepare_value=lambda value: value,
            render_value=lambda value: value,
        )
        part.update_value = mock.Mock(side_effect=lambda value: setattr(part, "value", value))
        changed, details = dat_views._update_repeater_part(part, [{"name": "Après"}])
        self.assertTrue(changed)
        self.assertEqual(json.loads(details["schemas"]["from"]), [{"name": "Before"}])
        self.assertEqual(json.loads(details["schemas"]["to"]), [{"name": "Après"}])
        part.update_value.assert_called_once_with([{"name": "Après"}])
        self.assertEqual(dat_views._update_repeater_part(part, part.value), (False, {}))
        self.assertEqual(dat_views._update_repeater_part(None, []), (False, {}))


class DatPartValueBehaviorTests(SimpleTestCase):
    @staticmethod
    def part(data_type, value, config=None):
        part = DATPart(key="test", label="Test", data_type=data_type, config=config or {})
        part._current_entry_cache = SimpleNamespace(resolved_value=value)
        return part

    def test_initial_value_converts_typed_values_and_preserves_invalid_values(self):
        self.assertIsNone(self.part(DATPartEntryType.TEXT, "").initial_value())
        self.assertTrue(self.part(DATPartEntryType.BOOLEAN, "yes").initial_value())
        self.assertEqual(self.part(DATPartEntryType.INTEGER, "12").initial_value(), 12)
        self.assertEqual(self.part(DATPartEntryType.INTEGER, "bad").initial_value(), "bad")
        self.assertEqual(self.part(DATPartEntryType.DECIMAL, "1.25").initial_value(), Decimal("1.25"))
        self.assertEqual(self.part(DATPartEntryType.DECIMAL, "bad").initial_value(), "bad")
        self.assertEqual(self.part(DATPartEntryType.DATE, "2026-10-02").initial_value(), date(2026, 10, 2))
        self.assertEqual(self.part(DATPartEntryType.DATE, "not-a-date").initial_value(), "not-a-date")
        self.assertEqual(self.part(DATPartEntryType.DATE, date(2026, 10, 2)).initial_value(), date(2026, 10, 2))

    def test_prepare_value_normalizes_each_supported_data_type(self):
        self.assertIsNone(DATPart(key="test", label="Test").prepare_value(""))
        self.assertEqual(self.part(DATPartEntryType.TEXT, "unused", {"multiple": True}).prepare_value(("a", "b")), ["a", "b"])
        self.assertTrue(self.part(DATPartEntryType.BOOLEAN, "unused").prepare_value("yes"))
        self.assertEqual(self.part(DATPartEntryType.INTEGER, "unused").prepare_value("12"), 12)
        self.assertIsNone(self.part(DATPartEntryType.INTEGER, "unused").prepare_value("invalid"))
        self.assertEqual(self.part(DATPartEntryType.DECIMAL, "unused").prepare_value(Decimal("1.20")), "1.20")
        self.assertEqual(self.part(DATPartEntryType.DECIMAL, "unused").prepare_value("2.5"), "2.5")
        self.assertIsNone(self.part(DATPartEntryType.DECIMAL, "unused").prepare_value("invalid"))
        self.assertEqual(self.part(DATPartEntryType.DATE, "unused").prepare_value(date(2026, 10, 2)), "2026-10-02")
        self.assertEqual(self.part(DATPartEntryType.DATE, "unused").prepare_value(20261002), "20261002")
        self.assertEqual(self.part(DATPartEntryType.REPEATER, "unused").prepare_value('[{"a":1}]'), [{"a": 1}])
        self.assertEqual(self.part(DATPartEntryType.REPEATER, "unused").prepare_value("invalid"), [])
        self.assertEqual(self.part(DATPartEntryType.REPEATER, "unused").prepare_value("{}"), [])
        self.assertEqual(self.part(DATPartEntryType.REPEATER, "unused").prepare_value([{"a": 1}]), [{"a": 1}])

    def test_render_value_formats_choices_and_typed_content(self):
        choice_part = self.part(
            DATPartEntryType.TEXT,
            None,
            {"choices": [{"value": "a", "label": "Alpha"}], "multiple": True},
        )
        self.assertEqual(choice_part.render_value(["a", "other"]), "Alpha, other")
        self.assertEqual(choice_part.render_value("a"), "Alpha")
        self.assertEqual(self.part(DATPartEntryType.BOOLEAN, None).render_value(True), "Oui")
        self.assertEqual(self.part(DATPartEntryType.BOOLEAN, None).render_value(False), "Non")
        self.assertEqual(self.part(DATPartEntryType.DATE, None).render_value(date(2026, 10, 2)), "2026-10-02")
        self.assertEqual(self.part(DATPartEntryType.INTEGER, None).render_value(5), "5")
        self.assertEqual(self.part(DATPartEntryType.DECIMAL, None).render_value(Decimal("1.5")), "1.5")
        self.assertEqual(self.part(DATPartEntryType.JSON, None).render_value({"a": 1}), '{\n  "a": 1\n}')
        self.assertEqual(self.part(DATPartEntryType.JSON, None).render_value("opaque"), "opaque")
        self.assertEqual(self.part(DATPartEntryType.REPEATER, None).render_value('[{"a":1}]'), [{"a": 1}])
        self.assertEqual(self.part(DATPartEntryType.REPEATER, None).render_value("invalid"), [])
        self.assertEqual(self.part(DATPartEntryType.REPEATER, None).render_value("{}"), [])
        self.assertEqual(self.part(DATPartEntryType.TEXT, None).render_value([]), "")
        self.assertEqual(self.part(DATPartEntryType.TEXT, None).render_value(3), "3")
        self.assertEqual(self.part(DATPartEntryType.TEXT, "current").formatted_value(), "current")

    def test_current_entry_uses_prefetch_sort_and_empty_cache(self):
        first = SimpleNamespace(
            updated_at=datetime(2026, 1, 1, tzinfo=datetime_timezone.utc),
            pk=1,
            resolved_value="first",
        )
        latest = SimpleNamespace(
            updated_at=datetime(2026, 1, 2, tzinfo=datetime_timezone.utc),
            pk=2,
            resolved_value="latest",
        )
        part = DATPart(key="test", label="Test")
        part._prefetched_objects_cache = {"entries": [first, latest]}
        self.assertEqual(part.value, "latest")
        self.assertIs(part._get_current_entry(), latest)

        empty_part = DATPart(key="empty", label="Empty")
        empty_part._prefetched_objects_cache = {"entries": []}
        self.assertIsNone(empty_part.value)


class DatPartPayloadPersistenceTests(TestCase):
    def setUp(self):
        direction = get_default_business_direction()
        application = Application.objects.create(
            code="payload-app",
            name="Payload app",
            business_direction=direction,
        )
        self.dat = DAT.objects.create(
            reference="DAT-PAYLOAD-1",
            title="Payload test",
            application=application,
        )
        metadata = DATSectionMetadata.objects.create(title="Test", slug="payload")
        section = DATSection.objects.create(dat=self.dat, metadata=metadata)
        sub_section = DATSubSection.objects.create(section=section, title="Test", slug="payload")
        self.part = DATPart.objects.create(sub_section=sub_section, key="payload", label="Payload")

    def test_payloads_deduplicate_values_and_part_updates_reuse_the_entry(self):
        self.assertIsNone(DATPartPayload.get_or_create_for_value({}))
        first_entry = self.part.update_value({"b": 2, "a": 1})
        first_payload = first_entry.payload
        same_payload = DATPartPayload.get_or_create_for_value({"a": 1, "b": 2})
        self.assertEqual(same_payload.pk, first_payload.pk)
        self.assertEqual(self.part.value, {"b": 2, "a": 1})

        same_entry = self.part.update_value({"a": 1, "b": 2})
        self.assertEqual(same_entry.pk, first_entry.pk)
        self.assertEqual(DATPartEntry.objects.filter(part=self.part).count(), 1)

        second_payload = DATPartPayload.get_or_create_for_value({"changed": True})
        same_entry = self.part.update_value({"changed": True})
        self.assertEqual(same_entry.pk, first_entry.pk)
        self.assertEqual(same_entry.payload_id, second_payload.pk)
        second_payload.data = {"mutated": True}
        second_payload.save()
        second_payload.refresh_from_db()
        self.assertEqual(second_payload.data, {"changed": True})

    def test_payload_hash_and_coercion_fall_back_for_circular_values(self):
        circular = []
        circular.append(circular)
        self.assertEqual(DATPartPayload._normalize_for_hash(circular), json.dumps(str(circular), ensure_ascii=False))
        self.assertEqual(DATPartPayload._coerce_json_value(circular), str(circular))


class DrawioParserEdgeCoverageTests(SimpleTestCase):
    @staticmethod
    def _compressed_payload(xml, wbits):
        compressor = zlib.compressobj(wbits=wbits)
        compressed = compressor.compress(xml.encode("utf-8")) + compressor.flush()
        return base64.b64encode(compressed).decode("ascii")

    def test_clean_model_xml_accepts_escaped_and_encoded_models_and_rejects_bad_inputs(self):
        xml = "<mxGraphModel><root /></mxGraphModel>"
        self.assertEqual(_clean_model_xml("  " + xml + "  "), xml)
        self.assertEqual(_clean_model_xml("&lt;mxGraphModel&gt;&lt;root /&gt;&lt;/mxGraphModel&gt;"), xml)
        self.assertEqual(_clean_model_xml("%3CmxGraphModel%3E%3Croot%20/%3E%3C/mxGraphModel%3E"), xml)
        self.assertIsNone(_clean_model_xml(""))
        self.assertIsNone(_clean_model_xml("not xml"))
        self.assertIsNone(_clean_model_xml("x" * (MAX_XML_CHARS + 1)))

    def test_extract_pages_accepts_compressed_drawio_pages_and_namespaced_children(self):
        model = "<mxGraphModel><root /></mxGraphModel>"
        for wbits in (-15, zlib.MAX_WBITS):
            with self.subTest(wbits=wbits):
                encoded = self._compressed_payload(model, wbits)
                pages = extract_drawio_pages(f'<mxfile><diagram name="Compressed">{encoded}</diagram></mxfile>')
                self.assertEqual(pages[0]["name"], "Compressed")
                self.assertIn("mxGraphModel", pages[0]["xml"])
                self.assertEqual(_inflate_drawio_payload(encoded), model)

        pages = extract_drawio_pages(
            '<mxfile xmlns="urn:drawio"><diagram label="Nested"><mxGraphModel><root /></mxGraphModel></diagram></mxfile>'
        )
        self.assertEqual(pages[0]["name"], "Nested")
        self.assertIn("mxGraphModel", pages[0]["xml"])

    def test_page_extraction_and_inflater_return_empty_for_invalid_documents(self):
        for invalid in ("", " ", "<broken", "<root />", "x" * (MAX_XML_CHARS + 1)):
            with self.subTest(invalid=invalid[:20]):
                self.assertEqual(extract_drawio_pages(invalid), [])
        self.assertEqual(_inflate_drawio_payload(""), None)
        self.assertEqual(_inflate_drawio_payload("not-base64"), None)
        self.assertEqual(_inflate_drawio_payload(base64.b64encode(b"not compressed xml").decode()), None)


class DatImportServiceCoverageTests(SimpleTestCase):
    def test_import_validates_required_sections_reference_duplicate_and_title(self):
        service = DATImportService()
        with self.assertRaisesMessage(DATImportError, 'section "dat" manquante'):
            service.import_from_payload({})
        with self.assertRaisesMessage(DATImportError, "référence du DAT est absente"):
            service.import_from_payload({"dat": {"title": "Title"}})

        with mock.patch("dat.importers.DAT.objects.filter") as filter_dat:
            filter_dat.return_value.exists.side_effect = [True, False, False]
            with self.assertRaisesMessage(DATImportError, "existe déjà"):
                service.import_from_payload({"dat": {"reference": "DAT-EXISTS", "title": "Title"}})
            with self.assertRaisesMessage(DATImportError, "titre du DAT est absent"):
                service.import_from_payload({"dat": {"reference": "DAT-NO-TITLE", "title": "  "}})
            with self.assertRaisesMessage(DATImportError, "application associée"):
                service.import_from_payload(
                    {"dat": {"reference": "DAT-NO-APP", "title": "Title"}, "application": None}
                )
        self.assertEqual(filter_dat.call_count, 3)

    def test_import_creates_dat_sets_actor_and_returns_warnings_without_live_database(self):
        actor = SimpleNamespace(pk=17)
        application = Application(code="import-app", name="Imported application")
        service = DATImportService(actor=actor)
        with (
            mock.patch("dat.importers.DAT.objects.filter") as filter_dat,
            mock.patch("dat.importers.transaction.atomic"),
            mock.patch.object(DAT, "save") as save_dat,
            mock.patch.object(service, "_resolve_application", return_value=application),
            mock.patch.object(service, "_resolve_user", return_value=None),
            mock.patch.object(service, "_normalise_status", return_value=DATStatus.EN_COURS),
            mock.patch.object(service, "_synchronise_owner"),
        ):
            filter_dat.return_value.exists.return_value = False
            result = service.import_from_payload(
                {
                    "dat": {
                        "reference": "DAT-NEW-IMPORT",
                        "title": "Imported DAT",
                        "description": "Details",
                        "status": "en_cours",
                    },
                    "application": {"code": "app"},
                    "owner": {"username": "missing"},
                    "participants": None,
                    "sections": None,
                }
            )

        self.assertEqual(result.dat.reference, "DAT-NEW-IMPORT")
        self.assertEqual(result.dat.title, "Imported DAT")
        self.assertEqual(result.dat.description, "Details")
        self.assertEqual(result.dat.status, DATStatus.EN_COURS)
        self.assertIs(result.dat._history_actor, actor)
        self.assertEqual(result.dat._workflow_initial_state, DATStatus.EN_COURS)
        self.assertEqual(result.warnings, [])
        save_dat.assert_called_once_with()

    def test_application_user_and_role_resolution_use_fallbacks_and_cache_misses(self):
        service = DATImportService()
        application = SimpleNamespace(code="app")
        with mock.patch("dat.importers.Application.objects.filter") as find_application:
            find_application.return_value.first.side_effect = [None, application]
            self.assertIs(service._resolve_application({"id": "missing", "code": "app"}), application)
            self.assertEqual(find_application.call_args_list, [mock.call(pk="missing"), mock.call(code="app")])
        with mock.patch("dat.importers.Application.objects.filter") as find_application:
            find_application.return_value.first.return_value = None
            with self.assertRaisesMessage(DATImportError, "Veuillez la créer"):
                DATImportService()._resolve_application({"id": "missing", "code": "missing"})
        with self.assertRaisesMessage(DATImportError, "absente"):
            DATImportService()._resolve_application("invalid")

        user = SimpleNamespace(username="alice")
        service = DATImportService()
        with mock.patch("dat.importers.UserModel.objects.filter") as find_user:
            find_user.return_value.first.side_effect = [None, user, None, None]
            self.assertIs(service._resolve_user({"id": 7, "username": "alice"}), user)
            self.assertIs(service._resolve_user({"id": 7, "username": "alice"}), user)
            self.assertIsNone(service._resolve_user({"id": 8, "username": "missing"}))
            self.assertIsNone(service._resolve_user({"id": 8, "username": "missing"}))
            self.assertEqual(find_user.call_count, 4)
        self.assertEqual(len(service._warnings), 1)
        self.assertIn("missing", service._warnings[0])
        self.assertIsNone(DATImportService()._resolve_user(None))

        role = SimpleNamespace(slug="architect")
        with mock.patch("dat.importers.Role.objects.filter") as find_role:
            find_role.return_value.first.side_effect = [role, None]
            self.assertIs(service._resolve_role("architect"), role)
            self.assertIs(service._resolve_role("architect"), role)
            self.assertIsNone(service._resolve_role("unknown"))
            self.assertIsNone(service._resolve_role("unknown"))
            self.assertEqual(find_role.call_count, 2)
        self.assertEqual(len(service._warnings), 2)

    def test_legacy_status_conversion_and_unknown_status_fall_back_to_initial(self):
        service = DATImportService()
        workflow_statuses = [
            {"status": DATStatus.NOUVELLE_DEMANDE},
            {"status": DATStatus.EN_COURS},
            {"status": DATStatus.EN_ATTENTE_DE_REVUE},
        ]
        with (
            mock.patch("dat.importers.workflow_initial_state", return_value=DATStatus.NOUVELLE_DEMANDE),
            mock.patch("dat.importers.workflow_states", return_value=workflow_statuses),
        ):
            self.assertEqual(service._normalise_status(None), DATStatus.NOUVELLE_DEMANDE)
            self.assertEqual(service._normalise_status(DATStatus.EN_COURS), DATStatus.EN_COURS)
            self.assertEqual(service._normalise_status("validation_finale"), DATStatus.EN_ATTENTE_DE_REVUE)
            self.assertEqual(service._normalise_status("legacy-invalid"), DATStatus.NOUVELLE_DEMANDE)
        self.assertEqual(len(service._warnings), 2)

    def test_participant_owner_and_section_imports_skip_unknown_and_empty_values(self):
        service = DATImportService(actor="importer")
        dat = SimpleNamespace(owner_id=None, save=mock.Mock())
        role = SimpleNamespace(slug="architect")
        user = SimpleNamespace(username="alice")
        with (
            mock.patch.object(service, "_resolve_role", side_effect=[None, role, role]),
            mock.patch.object(service, "_resolve_user", side_effect=[user, None, user]),
            mock.patch("dat.importers.DATParticipant.objects.create") as create_participant,
        ):
            service._import_participants(
                dat,
                [None, {"role_slug": "missing", "user": {}}, {"role": {"slug": "architect"}, "user": {}},
                 {"role_slug": "architect", "user": {}}, "invalid"],
            )
        create_participant.assert_called_once_with(dat=dat, role=role, user=user)
        service._import_participants(dat, "invalid")

        participant = SimpleNamespace(user_id=99)
        dat.participants = SimpleNamespace(
            select_related=lambda *_args: SimpleNamespace(
                filter=lambda **_kwargs: SimpleNamespace(first=lambda: participant)
            )
        )
        with mock.patch.object(dat, "save") as save:
            service._synchronise_owner(dat)
        self.assertEqual(dat.owner_id, 99)
        self.assertEqual(dat._history_actor, "importer")
        save.assert_called_once_with(update_fields=["owner", "updated_at"])
        already_owned = SimpleNamespace(owner_id=42, save=mock.Mock())
        with mock.patch.object(already_owned, "save") as save:
            service._synchronise_owner(already_owned)
        save.assert_not_called()

        part = SimpleNamespace(
            key="field",
            prepare_value=mock.Mock(side_effect=lambda value: value),
            update_value=mock.Mock(),
        )
        section_map = {
            "architecture": {
                "sub_sections": {
                    "schemas": {"parts": {"field": part}},
                }
            }
        }
        with (
            mock.patch("dat.importers.sync_dat_sections_if_needed"),
            mock.patch.object(service, "_build_section_map", return_value=section_map),
        ):
            service._import_sections(
                dat,
                [
                    None,
                    {"slug": "unknown", "sub_sections": []},
                    {"slug": "unknown", "sub_sections": []},
                    {
                        "slug": "architecture",
                        "sub_sections": [
                            None,
                            {"slug": "unknown", "parts": []},
                            {"slug": "unknown", "parts": []},
                            {
                                "slug": "schemas",
                                "parts": [
                                    None,
                                    {"key": "unknown", "value": "skip"},
                                    {"key": "unknown", "value": "skip"},
                                    {"key": "field", "value": []},
                                    {"key": "field", "value": {"value": "kept"}},
                                ],
                            },
                        ],
                    },
                ],
            )
        self.assertEqual(part.prepare_value.call_args_list, [mock.call([]), mock.call({"value": "kept"})])
        part.update_value.assert_called_once_with({"value": "kept"})
        self.assertEqual(len(service._warnings), 3)

    def test_section_map_builds_lookup_by_section_subsection_and_part(self):
        part = SimpleNamespace(key="schema")
        sub_section = SimpleNamespace(
            slug="schemas",
            parts=SimpleNamespace(all=lambda: [part]),
        )
        section = SimpleNamespace(
            slug="architecture",
            sub_sections=SimpleNamespace(all=lambda: [sub_section]),
        )
        dat = SimpleNamespace(
            sections=SimpleNamespace(
                select_related=lambda *_args: SimpleNamespace(
                    prefetch_related=lambda *_args: [section]
                )
            )
        )
        mapping = DATImportService()._build_section_map(dat)
        self.assertIs(mapping["architecture"]["section"], section)
        self.assertIs(mapping["architecture"]["sub_sections"]["schemas"]["sub_section"], sub_section)
        self.assertIs(mapping["architecture"]["sub_sections"]["schemas"]["parts"]["schema"], part)
