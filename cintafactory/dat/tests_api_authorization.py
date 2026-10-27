# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from users.models import BusinessDirection, Role

from .models import Application, DAT, DATAdmin, DATParticipant


class ResourceApiAuthorizationTests(TestCase):
    def setUp(self):
        self.porteur_role, _ = Role.objects.get_or_create(
            slug="porteur-demande", defaults={"name": "API authorization porteur"},
        )
        self.member_role = Role.objects.create(name="API authorization member", slug="api-authorization-member")
        self.admin_role, _ = Role.objects.get_or_create(
            slug="admin", defaults={"name": "API authorization administrator", "is_admin_role": True},
        )
        self.reader = get_user_model().objects.create_user(
            username="api-authorization-reader", password="pwd", role=self.member_role, can_use_api=True,
        )
        self.outsider = get_user_model().objects.create_user(
            username="api-authorization-outsider", password="pwd", role=self.member_role,
        )
        self.porteur = get_user_model().objects.create_user(
            username="api-authorization-porteur", password="pwd", role=self.porteur_role, can_use_api=True,
        )
        self.manager = get_user_model().objects.create_user(
            username="api-authorization-manager", password="pwd", role=self.admin_role, can_use_api=True,
        )
        self.business_direction = BusinessDirection.objects.create(
            name="API Authorization Direction", slug="api-authorization-direction",
        )
        self.application = Application.objects.create(
            code="api-authorization", name="API Authorization",
            business_direction=self.business_direction,
        )
        self.own_dat = DAT.objects.create(
            reference="API-AUTH-OWN", title="Owned DAT", application=self.application, owner=self.reader,
        )
        self.participant_dat = DAT.objects.create(
            reference="API-AUTH-PARTICIPANT", title="Participant DAT",
            application=self.application, owner=self.outsider,
        )
        self.hidden_dat = DAT.objects.create(
            reference="API-AUTH-HIDDEN", title="Hidden DAT", application=self.application, owner=self.outsider,
        )
        DATParticipant.objects.create(dat=self.participant_dat, role=self.member_role, user=self.reader)
        self.reader_api = APIClient()
        self.reader_api.force_authenticate(self.reader)

    def test_dat_reads_match_owner_and_participant_visibility(self):
        response = self.reader_api.get(reverse("api:dat-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item["id"] for item in response.data},
            {str(self.own_dat.pk), str(self.participant_dat.pk)},
        )
        self.assertNotIn(str(self.hidden_dat.pk), {item["id"] for item in response.data})

    def test_anonymous_user_cannot_read_resource_api(self):
        response = APIClient().get(reverse("api:dat-list"))

        self.assertEqual(response.status_code, 403)

    def test_api_opt_in_is_required_before_app_permissions_apply(self):
        disabled_user = get_user_model().objects.create_user(
            username="api-authorization-disabled", password="pwd", role=self.member_role,
        )
        api = APIClient()
        api.force_authenticate(disabled_user)

        self.assertEqual(api.get(reverse("api:application-list")).status_code, 403)

        disabled_user.can_use_api = True
        disabled_user.save(update_fields=["can_use_api"])
        self.assertEqual(api.get(reverse("api:application-list")).status_code, 200)

    def test_unrelated_user_cannot_read_or_change_another_dat_by_id(self):
        self.reader.user_permissions.add(
            Permission.objects.get(content_type__app_label="dat", codename="change_dat"),
        )
        detail_url = reverse("api:dat-detail", args=[self.hidden_dat.pk])

        self.assertEqual(self.reader_api.get(detail_url).status_code, 404)
        response = self.reader_api.patch(detail_url, {"title": "Changed"}, format="json")
        self.assertEqual(response.status_code, 404)
        self.hidden_dat.refresh_from_db()
        self.assertEqual(self.hidden_dat.title, "Hidden DAT")

    def test_application_reads_match_authenticated_crud_view_access(self):
        response = self.reader_api.get(reverse("api:application-list"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(str(self.application.pk), {item["id"] for item in response.data})

    def test_model_permissions_do_not_expand_create_access(self):
        self.reader.user_permissions.add(Permission.objects.get(content_type__app_label="dat", codename="add_dat"))

        response = self.reader_api.post(
            reverse("api:dat-list"),
            {"reference": "API-AUTH-DENIED", "title": "Denied", "application": str(self.application.pk)},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(DAT.objects.filter(reference="API-AUTH-DENIED").exists())

    def test_model_permissions_do_not_expand_application_create_access(self):
        self.reader.user_permissions.add(
            Permission.objects.get(content_type__app_label="dat", codename="add_application"),
        )

        response = self.reader_api.post(
            reverse("api:application-list"),
            {"code": "api-auth-denied", "name": "Denied"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Application.objects.filter(code="api-auth-denied").exists())

    def test_porteur_create_cannot_spoof_owner_or_workflow_status(self):
        api = APIClient()
        api.force_authenticate(self.porteur)

        response = api.post(
            reverse("api:dat-list"),
            {
                "reference": "API-AUTH-CREATED", "title": "Created DAT",
                "application": str(self.application.pk), "owner": str(self.outsider.pk),
                "status": "refuse",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        created = DAT.objects.get(reference="API-AUTH-CREATED")
        self.assertEqual(created.owner, self.porteur)
        self.assertEqual(created.status, DAT._meta.get_field("status").default)

    def test_manager_can_edit_content_but_not_owner_or_workflow_status(self):
        api = APIClient()
        api.force_authenticate(self.manager)

        response = api.patch(
            reverse("api:dat-detail", args=[self.own_dat.pk]),
            {"title": "Manager update", "owner": str(self.outsider.pk), "status": "refuse"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.own_dat.refresh_from_db()
        self.assertEqual(self.own_dat.title, "Manager update")
        self.assertEqual(self.own_dat.owner, self.reader)
        self.assertEqual(self.own_dat.status, DAT._meta.get_field("status").default)

    def test_porteur_can_create_application_without_model_permission(self):
        api = APIClient()
        api.force_authenticate(self.porteur)

        response = api.post(
            reverse("api:application-list"),
            {
                "code": "api-authorization-new", "name": "New application",
                "business_direction": str(self.business_direction.pk),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Application.objects.filter(code="api-authorization-new").exists())

    def test_non_manager_cannot_edit_application_even_with_model_permission(self):
        self.reader.user_permissions.add(
            Permission.objects.get(content_type__app_label="dat", codename="change_application"),
        )

        response = self.reader_api.patch(
            reverse("api:application-detail", args=[self.application.pk]), {"name": "Changed"}, format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.application.refresh_from_db()
        self.assertEqual(self.application.name, "API Authorization")

    def test_dat_admin_is_included_in_dat_read_visibility(self):
        DATAdmin.objects.create(dat=self.hidden_dat, user=self.reader)

        response = self.reader_api.get(reverse("api:dat-detail", args=[self.hidden_dat.pk]))

        self.assertEqual(response.status_code, 200)
