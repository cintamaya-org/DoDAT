# SPDX-License-Identifier: AGPL-3.0-only
"""Assign DAT versions in the same transaction as the DAT write."""

from django.core.exceptions import ValidationError

from .models import DAT, DATReferenceSequence


class DATReferenceError(ValidationError):
    """A version cannot be assigned to the selected application."""


def numeric_value(reference: str) -> int | None:
    return int(reference) if reference.isascii() and reference.isdecimal() else None


def next_reference(sequence: DATReferenceSequence) -> str:
    reference = f"{int(sequence.last_value) + 1:03d}"
    if len(reference) > DAT._meta.get_field("reference").max_length:
        raise DATReferenceError("La numérotation automatique de cette application est épuisée.")
    return reference


def validate_reference(application_id, value, *, original: DAT | None = None, using=None) -> str:
    """Return the stored spelling. An empty result on creation requests allocation."""
    if value is None:
        value = ""
    elif type(value) is int:
        value = str(value)
    same_value = original is not None and value == original.reference
    if same_value and application_id == original.application_id:
        return value
    if not isinstance(value, str):
        raise DATReferenceError("La référence du DAT doit être un nombre entier ou du texte.")
    reference = value if same_value else value.strip()
    if not reference and original is None:
        return ""
    if not reference and not same_value:
        raise DATReferenceError("La référence d’un DAT existant est obligatoire.")
    max_length = DAT._meta.get_field("reference").max_length
    if len(reference) > max_length:
        raise DATReferenceError(f"La référence doit contenir au plus {max_length} caractères.")
    if "\x00" in reference:
        raise DATReferenceError("La référence contient un caractère nul.")

    matches = DAT.objects.using(using).filter(application_id=application_id)
    if original is not None:
        matches = matches.exclude(pk=original.pk)
    number = numeric_value(reference)
    if number is None:
        matches = matches.filter(reference=reference)
    else:
        # Exact lookups use the composite index for legacy spellings such as 7 or 0007.
        digits = str(number)
        spellings = [digits.zfill(width) for width in range(len(digits), max_length + 1)]
        matches = matches.filter(reference__in=spellings)
        if not same_value:
            reference = f"{number:03d}"
    if matches.exists():
        raise DATReferenceError(f"Un DAT avec la référence « {reference} » existe déjà pour cette application.")
    return reference


def assign_reference(dat: DAT, original: DAT | None, update_fields, using) -> None:
    application_id = dat.application_id
    value = dat.reference
    if original is not None:
        if update_fields is not None:
            if not {"application", "application_id"}.intersection(update_fields):
                application_id = original.application_id
            if "reference" not in update_fields:
                value = original.reference
        if application_id == original.application_id and value == original.reference:
            return

    sequence, _ = DATReferenceSequence.objects.using(using).select_for_update().get_or_create(
        application_id=application_id,
    )
    reference = validate_reference(application_id, value, original=original, using=using)
    dat.reference = next_reference(sequence) if original is None and not reference else reference
    number = numeric_value(dat.reference)
    if number is not None and number > sequence.last_value:
        sequence.last_value = number
        sequence.save(using=using, update_fields=["last_value"])
