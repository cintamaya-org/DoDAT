# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.urls import include, path
from django.views.generic import RedirectView
from . import views

app_name = "dat"

urlpatterns = [
    # Redirect base index
    path("", RedirectView.as_view(url="my/", permanent=False), name="index"),

    # Main "My DAT" list
    path("my/", views.DatList.as_view(), name="my_list"),
    path("my/application-options/", views.my_application_options, name="my_application_options"),
    path("my/applications/", views.MyApplicationListView.as_view(), name="my_applications"),
    path("my/<uuid:pk>/", views.DatDetail.as_view(), name="my_detail"),
    path(
        "my/<uuid:pk>/section-responsibles/update/",
        views.update_overview_section_responsibles,
        name="my_section_responsibles_update",
    ),
    path(
        "my/<uuid:pk>/section-participants/update/",
        views.update_overview_section_participants,
        name="my_section_participants_update",
    ),
    path(
        "my/<uuid:pk>/admins/add/",
        views.add_dat_admin_from_overview,
        name="my_dat_admin_add",
    ),
    path(
        "my/<uuid:pk>/admins/<uuid:user_id>/remove/",
        views.remove_dat_admin_from_overview,
        name="my_dat_admin_remove",
    ),
    path("my/<uuid:pk>/validation/decision/", views.submit_validation_decision, name="my_validation_decision"),
    path("my/<uuid:pk>/export/json/", views.DatExportJSONView.as_view(), name="my_export_json"),
    path(
        "my/<uuid:pk>/export/pdf/trigger/",
        views.DatTriggerPDFExportView.as_view(),
        name="my_export_pdf_trigger",
    ),
    path(
        "my/<uuid:pk>/export/secure/request/",
        views.DatRequestSecureExportAccessView.as_view(),
        name="my_export_secure_request",
    ),
    path(
        "my/<uuid:pk>/export/secure/approve/",
        views.DatApproveSecureExportAccessView.as_view(),
        name="my_export_secure_approve",
    ),
    path(
        "my/<uuid:pk>/export/pdf/status/",
        views.DatExportStatusView.as_view(),
        name="my_export_pdf_status",
    ),
    path(
        "my/<uuid:pk>/export/pdf/generate/",
        views.DatGeneratePDFExportView.as_view(),
        name="my_export_pdf_generate",
    ),
    path(
        "my/<uuid:pk>/export/pdf/download/",
        views.DatDownloadCachedPDFView.as_view(),
        name="my_export_pdf_download",
    ),
    path(  # Backward compatibility alias
        "my/<uuid:pk>/export/pdf/",
        views.DatGeneratePDFExportView.as_view(),
        name="my_export_pdf",
    ),
    path(
        "my/<uuid:dat_pk>/sections/<slug:section_slug>/<slug:sub_section_slug>/edit/",
        views.DatSubSectionUpdateView.as_view(),
        name="sub_section_edit",
    ),
    path(
        "my/<uuid:dat_pk>/sections/<slug:section_slug>/status/",
        views.update_section_status,
        name="section_status",
    ),
    path(
        "my/<uuid:dat_pk>/sections/<slug:section_slug>/responsible-status/",
        views.update_section_responsible_status,
        name="section_responsible_status",
    ),
    path(
        "my/<uuid:dat_pk>/sections/<slug:section_slug>/reserve/",
        views.update_section_reserve,
        name="section_reserve",
    ),
    path(
        "my/<uuid:dat_pk>/sections/<slug:section_slug>/reserve/clear/",
        views.clear_section_reserve,
        name="section_reserve_clear",
    ),
    path(
        "my/<uuid:dat_pk>/sections/<slug:section_slug>/attachments/upload/",
        views.upload_section_attachment,
        name="section_attachment_upload",
    ),
    path(
        "my/<uuid:dat_pk>/attachments/<uuid:attachment_pk>/download/",
        views.download_section_attachment,
        name="section_attachment_download",
    ),
    path(
        "my/<uuid:dat_pk>/attachments/<uuid:attachment_pk>/delete/",
        views.remove_section_attachment,
        name="section_attachment_delete",
    ),
    path(
        "my/<uuid:dat_pk>/schemas/create-diagram/",
        views.create_schema_diagram,
        name="schema_create_diagram",
    ),
    path(
        "my/<uuid:dat_pk>/schemas/parse-diagram/",
        views.parse_schema_diagram,
        name="schema_parse_diagram",
    ),

    # Dashboard
    path("manage/dats/dashboard/", views.DatDashboardView.as_view(), name="dashboard"),
    path("manage/dats/import/", views.DatImportView.as_view(), name="import"),
    path("search/", views.DatSearchPageView.as_view(), name="search_page"),
    path("search/options/<str:field>/", views.search_filter_options, name="search_filter_options"),

    # CRUD
    path("manage/dats/", views.DatAdminList.as_view(), name="admin_list"),
    path("manage/dats/crud/<uuid:pk>/detail/", views.dat_crud_detail_unavailable, name="dat_crud_detail_unavailable"),
    path("manage/dats/crud/", include(views.DATViewSet().urls)),
    path("manage/applications/options/", views.application_options, name="application_options"),
    path("search/topbar/", views.topbar_search, name="topbar_search"),
    path("manage/applications/crud/", include(views.ApplicationViewSet().urls)),
]
