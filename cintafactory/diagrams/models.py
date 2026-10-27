# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from pathlib import Path
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone

from cintafactory.storage.seaweedfs_storage import SeaweedFSStorage

from .validation import sanitize_diagram_title


def get_diagram_storage() -> SeaweedFSStorage:
    return SeaweedFSStorage()


def get_diagram_thumbnail_storage() -> SeaweedFSStorage:
    return SeaweedFSStorage(public_url=settings.SEAWEEDFS_PUBLIC_URL_PP)


def thumbnail_upload_to(instance, filename):
    return f"diagrams/{instance.id}/views/thumb.png"


def likec4_png_path_for(storage_path: str) -> str:
    path = Path(storage_path)
    parts = path.parts
    if len(parts) >= 3 and parts[0] == "diagrams":
        return f"diagrams/{parts[1]}/views/thumb.png"
    base = path.name
    if base.lower().endswith(".c4"):
        base = base[:-3]
    return f"diagrams/likec4/{base}/views/thumb.png"


def drawio_upload_to(instance, filename):
    return f"diagrams/{instance.id}/diagram.drawio"


def valid_drawio_png_path(diagram, storage_path: str) -> bool:
    prefix = f"diagrams/{diagram.pk}/views/"
    if (
        not isinstance(storage_path, str)
        or not storage_path.startswith(prefix)
        or not storage_path.lower().endswith(".png")
    ):
        return False
    return all(
        part and part not in {".", ".."}
        and not any(character in part for character in ("\\", "\x00", "%", ":"))
        for part in storage_path.split("/")
    )


def likec4_source_path_for(diagram_id) -> str:
    return f"diagrams/likec4/{diagram_id}/source.c4"


def likec4_views_prefix_for(diagram) -> str:
    return f"diagrams/likec4/{diagram.pk}/views/{diagram.revision}/"


class Diagram(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200, blank=True, default="")
    png_paths = models.JSONField(default=list, blank=True, null=True)
    updated_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    dat = models.ForeignKey(
        "dat.DAT", on_delete=models.CASCADE, null=True, blank=True,
        related_name="%(class)s_diagrams",
    )
    sections = models.ManyToManyField(
        "dat.DATSection", blank=True, related_name="%(class)s_diagrams",
    )

    class Meta:
        abstract = True
        ordering = ["-updated_at"]

    def clean(self):
        super().clean()
        if self.title:
            self.title = sanitize_diagram_title(self.title)

    def __str__(self) -> str:
        if self.title:
            return self.title
        storage_path = getattr(self, "storage_path", "") or ""
        if storage_path:
            return storage_path
        return f"Diagram #{self.pk}"


class DrawIODiagram(Diagram):
    title = models.CharField(max_length=200)
    xml_file = models.FileField(
        upload_to=drawio_upload_to,
        storage=get_diagram_storage(),
        blank=True,
        null=True,
    )
    xml_content_type = models.CharField(max_length=120, blank=True, default="application/xml")
    xml_size = models.PositiveIntegerField(default=0)
    thumbnail = models.ImageField(
        upload_to=thumbnail_upload_to,
        storage=get_diagram_thumbnail_storage(),
        blank=True,
        null=True,
    )
    thumbnail_content_type = models.CharField(max_length=120, blank=True, default="image/png")
    thumbnail_size = models.PositiveIntegerField(default=0)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="diagrams",
    )

    class Meta:
        db_table = "diagrams_diagram"
        ordering = ["-updated_at"]

    def read_xml(self) -> str:
        field = getattr(self, "xml_file", None)
        if not field or not getattr(field, "name", None):
            return ""
        try:
            field.open("rb")
            raw = field.read()
        except FileNotFoundError:
            return ""
        except Exception:
            return ""
        finally:
            try:
                field.close()
            except Exception:
                pass
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="ignore")

    def write_xml(self, xml_payload: str, *, content_type: str = "application/xml") -> None:
        payload = xml_payload or ""
        raw = payload.encode("utf-8")
        self.xml_file.save("diagram.drawio", ContentFile(raw), save=False)
        self.xml_size = len(raw)
        self.xml_content_type = content_type
        self.updated_at = timezone.now()
        self.save(update_fields=["xml_file", "xml_size", "xml_content_type", "updated_at"])


class LikeC4Diagram(Diagram):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="likec4_diagrams",
    )
    revision = models.CharField(max_length=64, blank=True, default="")
    storage_path = models.CharField(max_length=500, unique=True)
    content_type = models.CharField(max_length=200, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    png_path = models.CharField(max_length=500, blank=True, default="")
    png_content_type = models.CharField(max_length=200, blank=True, default="image/png")
    png_size = models.PositiveBigIntegerField(default=0)
    png_updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "diagrams_likec4file"
        ordering = ["-updated_at"]
