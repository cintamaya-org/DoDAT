# SPDX-License-Identifier: AGPL-3.0-only

"""Bounded PNG decoding and normalization for diagram previews."""

import warnings
from io import BytesIO

from django.conf import settings
from PIL import Image, UnidentifiedImageError


def normalize_png_bytes(raw: bytes) -> bytes | None:
    byte_limit = int(getattr(settings, "DIAGRAM_PNG_MAX_BYTES", 5 * 1024 * 1024))
    pixel_limit = int(getattr(settings, "DIAGRAM_PNG_MAX_PIXELS", 16 * 1024 * 1024))
    if not isinstance(raw, bytes) or not raw or len(raw) > byte_limit:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG" or image.width * image.height > pixel_limit:
                    return None
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
                mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
                output = BytesIO()
                image.convert(mode).save(output, format="PNG")
                normalized = output.getvalue()
                return normalized if len(normalized) <= byte_limit else None
    except (
        ValueError,
        TypeError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return None
