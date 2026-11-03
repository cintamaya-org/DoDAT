# SPDX-License-Identifier: AGPL-3.0-only

from unittest import mock

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase

from .models import DrawIODiagram, LikeC4Diagram


class DiagramAdminPermissionTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = mock.Mock(spec=["has_perm"])
        self.request.user.has_perm.return_value = True
        self.authorization = mock.patch("diagrams.admin.require_diagram_write")
        self.require_write = self.authorization.start()
        self.addCleanup(self.authorization.stop)

    def test_change_requires_django_permission_before_object_authorization(self):
        self.request.user.has_perm.return_value = False
        for model in (DrawIODiagram, LikeC4Diagram):
            with self.subTest(model=model.__name__):
                self.assertFalse(admin.site._registry[model].has_change_permission(self.request, model()))
        self.require_write.assert_not_called()

    def test_change_without_object_allows_authorized_admin(self):
        for model in (DrawIODiagram, LikeC4Diagram):
            with self.subTest(model=model.__name__):
                self.assertTrue(admin.site._registry[model].has_change_permission(self.request))
        self.require_write.assert_not_called()

    def test_change_checks_diagram_write_access(self):
        for model in (DrawIODiagram, LikeC4Diagram):
            with self.subTest(model=model.__name__):
                diagram = model()
                self.assertTrue(admin.site._registry[model].has_change_permission(self.request, diagram))
                self.require_write.assert_called_with(diagram, self.request.user)

    def test_change_denies_forbidden_and_hidden_diagrams(self):
        for model in (DrawIODiagram, LikeC4Diagram):
            for error in (PermissionDenied, Http404):
                with self.subTest(model=model.__name__, error=error.__name__):
                    self.require_write.side_effect = error
                    self.assertFalse(admin.site._registry[model].has_change_permission(self.request, model()))

    def test_delete_requires_both_django_permissions_and_diagram_write_access(self):
        for model in (DrawIODiagram, LikeC4Diagram):
            for can_delete, can_change, can_write in (
                (False, True, True), (True, False, True),
                (True, True, False), (True, True, True),
            ):
                with self.subTest(model=model.__name__, permissions=(can_delete, can_change, can_write)):
                    permissions = {
                        f"diagrams.delete_{model._meta.model_name}": can_delete,
                        f"diagrams.change_{model._meta.model_name}": can_change,
                    }
                    self.request.user.has_perm.side_effect = lambda permission: permissions.get(permission, False)
                    self.require_write.side_effect = None if can_write else PermissionDenied
                    self.assertEqual(
                        admin.site._registry[model].has_delete_permission(self.request, model()),
                        can_delete and can_change and can_write,
                    )

    def test_add_is_disabled_even_for_authorized_admins(self):
        for model in (DrawIODiagram, LikeC4Diagram):
            with self.subTest(model=model.__name__):
                self.assertFalse(admin.site._registry[model].has_add_permission(self.request))
