# SPDX-License-Identifier: AGPL-3.0-only

import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import TestCase

from dat.models import (
    Application,
    DAT,
    DATAdmin,
    DATParticipant,
    DATSection,
    DATSectionMetadata,
    DATSectionParticipant,
    DATSectionResponsible,
    DATSubSection,
    DATPart,
    DATPartEntryType,
)
from diagrams.access import (
    bind_diagram,
    diagram_queryset_for_user,
    get_diagram_for_user,
    require_diagram_write,
)
from diagrams.models import DrawIODiagram, LikeC4Diagram
from diagrams.references import backfill_legacy_diagrams, prepare_diagram_references
from users.models import BusinessDirection, Role


class DiagramAuthorizationSecurityTests(TestCase):
    def setUp(self):
        self.workflow_check = mock.patch("diagrams.access.workflow_has_capability", return_value=False)
        self.workflow_check.start()
        self.addCleanup(self.workflow_check.stop)
        User = get_user_model()
        roles = {
            name: Role.objects.create(name=f"Diagram {name} role", slug=f"diagram-{name}-role")
            for name in ("owner", "viewer", "assignee", "dat-admin", "unrelated", "global-admin")
        }
        self.owner = User.objects.create_user(username="diagram-owner", role=roles["owner"])
        self.viewer = User.objects.create_user(username="diagram-viewer", role=roles["viewer"])
        self.assignee = User.objects.create_user(username="diagram-assignee", role=roles["assignee"])
        self.dat_admin = User.objects.create_user(username="diagram-dat-admin", role=roles["dat-admin"])
        self.unrelated = User.objects.create_user(username="diagram-unrelated", role=roles["unrelated"])
        self.global_admin = User.objects.create_superuser(
            username="diagram-global-admin", email="diagram-global@example.test", password="pwd",
            role=roles["global-admin"],
        )
        direction = BusinessDirection.objects.create(name="Diagram Direction", slug="diagram-direction")
        application = Application.objects.create(
            code="diagram-access-test", name="Diagram Access Test", business_direction=direction,
        )
        self.source_dat = DAT.objects.create(
            reference="DIAGRAM-SOURCE-DAT", title="Source DAT", application=application, owner=self.owner,
        )
        self.target_dat = DAT.objects.create(
            reference="DIAGRAM-TARGET-DAT", title="Target DAT", application=application, owner=self.assignee,
        )
        self.source_section = self._section(self.source_dat, "Source")
        self.target_section = self._section(self.target_dat, "Target")
        DATParticipant.objects.create(dat=self.source_dat, user=self.viewer, role=roles["viewer"])
        DATParticipant.objects.create(dat=self.source_dat, user=self.assignee, role=roles["assignee"])
        DATParticipant.objects.create(dat=self.target_dat, user=self.viewer, role=roles["viewer"])
        DATSectionResponsible.objects.create(dat=self.source_dat, section=self.source_section, user=self.assignee)
        DATSectionResponsible.objects.create(dat=self.target_dat, section=self.target_section, user=self.viewer)
        DATSectionParticipant.objects.create(dat=self.target_dat, section=self.target_section, user=self.viewer)
        DATAdmin.objects.create(dat=self.source_dat, user=self.dat_admin)
        self.linked = LikeC4Diagram.objects.create(
            title="Linked diagram", owner=self.owner, dat=self.source_dat,
            storage_path=f"diagrams/likec4/{uuid.uuid4()}/source.c4",
        )
        self.linked.sections.add(self.source_section)
        self.private = LikeC4Diagram.objects.create(
            title="Private diagram", owner=self.owner,
            storage_path=f"diagrams/likec4/{uuid.uuid4()}/source.c4",
        )

    def _section(self, dat, title):
        metadata = DATSectionMetadata.objects.create(
            title=title, slug=title.lower().replace(" ", "-"),
        )
        return DATSection.objects.create(dat=dat, metadata=metadata)

    def test_unlinked_c4_and_drawio_sources_are_owner_private(self):
        drawio = DrawIODiagram.objects.create(title="Private Draw.io", owner=self.owner)
        self.assertEqual(get_diagram_for_user(LikeC4Diagram, self.owner, pk=self.private.pk), self.private)
        self.assertEqual(get_diagram_for_user(DrawIODiagram, self.owner, pk=drawio.pk), drawio)
        for model, diagram in ((LikeC4Diagram, self.private), (DrawIODiagram, drawio)):
            with self.subTest(model=model.__name__):
                with self.assertRaises(Http404):
                    get_diagram_for_user(model, self.viewer, pk=diagram.pk)

    def test_dat_viewer_can_read_linked_diagram_but_cannot_edit_or_attach_it(self):
        self.assertTrue(diagram_queryset_for_user(LikeC4Diagram, self.viewer).filter(pk=self.linked.pk).exists())
        self.assertEqual(get_diagram_for_user(LikeC4Diagram, self.viewer, pk=self.linked.pk), self.linked)
        with self.assertRaises(PermissionDenied):
            require_diagram_write(self.linked, self.viewer)
        with self.assertRaises(PermissionDenied):
            bind_diagram(self.linked, self.target_section, self.viewer)

    def test_section_assignee_dat_admin_and_global_admin_can_edit(self):
        for user in (self.assignee, self.dat_admin, self.global_admin):
            with self.subTest(user=user.username):
                require_diagram_write(self.linked, user)

    def test_revoked_section_assignment_removes_write_access(self):
        require_diagram_write(self.linked, self.assignee)
        DATSectionResponsible.objects.filter(section=self.source_section).delete()
        with self.assertRaises(PermissionDenied):
            require_diagram_write(self.linked, self.assignee)

    def test_terminal_dat_blocks_edits_even_for_global_admin(self):
        with mock.patch("diagrams.access.workflow_has_capability", return_value=True):
            with self.assertRaises(PermissionDenied):
                require_diagram_write(self.linked, self.global_admin)

    def test_reading_from_another_dat_does_not_authorize_cross_dat_copy(self):
        sub_section = DATSubSection.objects.create(
            section=self.target_section, title="Schemas", slug="schemas",
        )
        part = DATPart.objects.create(
            sub_section=sub_section, key="schemas", label="Schemas",
            data_type=DATPartEntryType.REPEATER,
            config={
                "columns": [
                    {"key": "schema_systeme", "diagram_tool_key": "schema_systeme"},
                    {"key": "schema_reference", "diagram_reference_key": "schema_reference"},
                ],
            },
        )
        value = [{"schema_systeme": "LikeC4", "schema_reference": self.linked.storage_path}]
        with self.assertRaises(ValidationError):
            prepare_diagram_references(part, value, self.viewer, attach=True)


class DiagramAccessBackfillTests(TestCase):
    def test_dry_run_reports_unique_legacy_bindings_before_idempotent_apply(self):
        user = get_user_model().objects.create_user(username="backfill-owner")
        direction = BusinessDirection.objects.create(name="Backfill Direction", slug="backfill-direction")
        application = Application.objects.create(
            code="backfill-app", name="Backfill App", business_direction=direction,
        )
        dat = DAT.objects.create(
            reference="DIAGRAM-BACKFILL", title="Backfill", application=application, owner=user,
        )
        metadata = DATSectionMetadata.objects.create(
            title="Backfill Architecture", slug="backfill-architecture",
        )
        section = DATSection.objects.create(dat=dat, metadata=metadata)
        sub_section = DATSubSection.objects.create(section=section, title="Schemas", slug="schemas")
        drawio = DrawIODiagram.objects.create(title="Legacy Draw.io", owner=user)
        likec4_id = uuid.uuid4()
        likec4_path = f"diagrams/likec4/{likec4_id}/source.c4"
        likec4 = LikeC4Diagram.objects.create(
            id=likec4_id, title="Legacy C4", storage_path=likec4_path,
        )
        part = DATPart.objects.create(
            sub_section=sub_section,
            key="schemas",
            label="Schemas",
            data_type=DATPartEntryType.REPEATER,
            config={
                "columns": [
                    {"key": "diagram_id", "drawio": True},
                    {"diagram_tool_key": "engine", "diagram_reference_key": "path"},
                ],
            },
        )
        part.update_value([{
            "diagram_id": str(drawio.pk),
            "engine": "likec4",
            "path": likec4_path,
        }])

        report = backfill_legacy_diagrams(dry_run=True)

        self.assertEqual(report["bound"], 2)
        self.assertEqual(report["section_bindings"], 2)
        self.assertEqual(report["unresolved"], 0)
        drawio.refresh_from_db()
        likec4.refresh_from_db()
        self.assertIsNone(drawio.dat_id)
        self.assertFalse(drawio.sections.exists())
        self.assertIsNone(likec4.dat_id)
        self.assertFalse(likec4.sections.exists())

        applied = backfill_legacy_diagrams()
        self.assertEqual(applied["bound"], 2)
        self.assertEqual(applied["section_bindings"], 2)
        self.assertEqual(applied["unresolved"], 0)
        drawio.refresh_from_db()
        likec4.refresh_from_db()
        self.assertEqual(drawio.dat_id, dat.pk)
        self.assertEqual(likec4.dat_id, dat.pk)
        self.assertEqual(list(drawio.sections.values_list("pk", flat=True)), [section.pk])
        self.assertEqual(list(likec4.sections.values_list("pk", flat=True)), [section.pk])
        self.assertEqual(backfill_legacy_diagrams()["bound"], 0)
