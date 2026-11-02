# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
import traceback
from threading import current_thread, main_thread

from django.db import close_old_connections
from django.urls import reverse
from django.utils import timezone

from cintafactory.async_jobs import enqueue_pdf_export_job

from .export_access import can_download
from .models import DAT
from .pdf import generate_dat_pdf
from .utils import format_user_display, store_dat_pdf_export
from workflows.notifications import create_user_notification

logger = logging.getLogger(__name__)


def schedule_dat_pdf_generation(dat: DAT, user, *, base_url: str | None = None):
    """
    Start an asynchronous PDF export for the given DAT if none is running.
    Returns the async job when scheduled, None otherwise.
    """
    requester = user if getattr(user, "is_authenticated", False) else None
    if not can_download(dat, requester, "pdf"):
        logger.warning("Rejected unauthorized PDF generation request for DAT %s", dat.pk)
        return None
    now = timezone.now()
    display = format_user_display(requester) if requester else "Système"
    updated = DAT.objects.filter(pk=dat.pk, pdf_export_in_progress=False).update(
        pdf_export_in_progress=True,
        pdf_export_requested_at=now,
        pdf_export_requested_by=requester,
        pdf_export_requested_by_display=display,
    )
    if not updated:
        print(f"[DAT PDF] generation deja en cours (dat_id={dat.pk}).", flush=True)
        logger.info("Generation PDF DAT %s deja en cours, demande ignoree.", dat.pk)
        return None
    print(f"[DAT PDF] generation planifiee (dat_id={dat.pk}, demandeur={display}).", flush=True)
    logger.info(
        "Generation PDF DAT planifiee (dat_id=%s, demandeur=%s).",
        dat.pk,
        display,
    )
    job = enqueue_pdf_export_job(
        dat.pk,
        requested_by=requester,
        base_url=base_url,
        source="dat_trigger",
    )
    return job


def _run_pdf_generation(dat_id: int, base_url: str | None):
    success = False
    if current_thread() is not main_thread():
        close_old_connections()
    try:
        dat = (
            DAT.objects.select_related("application", "owner", "pdf_export_requested_by")
            .prefetch_related("participants__role", "participants__user")
            .get(pk=dat_id)
        )
    except DAT.DoesNotExist:  # pragma: no cover - concurrent deletion
        print(f"[DAT PDF] dat introuvable pour generation (dat_id={dat_id}).", flush=True)
        logger.warning("DAT %s introuvable pour la génération PDF.", dat_id)
        _mark_export_finished(dat_id)
        return False
    reference = dat.reference or dat.title or f"DAT #{dat.pk}"
    print(f"[DAT PDF] generation lancee ({reference}).", flush=True)
    logger.info("Generation PDF DAT %s lancee.", reference)
    if not can_download(dat, dat.pdf_export_requested_by, "pdf"):
        logger.warning("Rejected PDF generation after export approval expired for DAT %s", dat.pk)
        _mark_export_finished(dat_id)
        return False
    try:
        pdf_content, _payload = generate_dat_pdf(dat, base_url=base_url)
        path = store_dat_pdf_export(dat, pdf_content)
        print(
            f"[DAT PDF] PDF enregistre ({reference}, {len(pdf_content)} octets, {path}).",
            flush=True,
        )
        logger.info("PDF DAT %s enregistre (%s octets) dans %s.", reference, len(pdf_content), path)
        _notify_pdf_export_ready(dat)
        success = True
    except Exception as exc:  # pragma: no cover - log unexpected errors
        print(
            f"[DAT PDF] erreur generation (dat_id={dat_id}, type={type(exc).__name__}, msg={exc}).",
            flush=True,
        )
        print(traceback.format_exc(), flush=True)
        logger.exception("Erreur lors de la génération PDF du DAT %s", dat_id)
    finally:
        _mark_export_finished(dat_id)
        if current_thread() is not main_thread():
            close_old_connections()
    return success


def _mark_export_finished(dat_id: int):
    DAT.objects.filter(pk=dat_id).update(
        pdf_export_in_progress=False,
        pdf_export_requested_at=None,
        pdf_export_requested_by=None,
        pdf_export_requested_by_display="",
    )


def _notify_pdf_export_ready(dat: DAT) -> None:
    requester = getattr(dat, "pdf_export_requested_by", None)
    if requester is None:
        return
    reference = dat.reference or dat.title or f"DAT #{dat.pk}"
    display = dat.pdf_export_requested_by_display or format_user_display(requester)
    target_url = reverse("dat:my_detail", args=[dat.pk])
    message = (
        f"Le document PDF pour {reference} est prêt. "
        "Rendez-vous sur la page du DAT pour le récupérer."
    )
    create_user_notification(
        requester,
        title="Export PDF disponible",
        message=message,
        level="success",
        dat=dat,
        target_url=target_url,
        created_by_display=display,
    )
