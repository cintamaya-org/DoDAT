# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from django.urls import path
from . import graph_views

from .views import (
    MyTasksBoardView,
    WorkflowBoardView,
    WorkflowNotificationsView,
    WorkflowOverviewView,
)

app_name = "workflows"

urlpatterns = [
    path("approbations/", graph_views.my_approvals, name="my_approvals"),
    path("dat/<uuid:dat_pk>/review/", graph_views.graph_detail, name="graph_detail"),
    path("dat/<uuid:dat_pk>/review/<slug:node_id>/", graph_views.node_action, name="node_action"),
    path("dat/<uuid:dat_pk>/snapshots/<uuid:snapshot_pk>/", graph_views.review_snapshot, name="review_snapshot"),
    path("dat/<uuid:dat_pk>/snapshots/<uuid:snapshot_pk>/assets/<slug:checksum>/", graph_views.review_asset, name="review_asset"),
    path("", WorkflowBoardView.as_view(), name="index"),
    # path("overview/", WorkflowOverviewView.as_view(), name="overview"),
    path("board/", WorkflowBoardView.as_view(), name="board"),
    path("mes-taches/", MyTasksBoardView.as_view(), name="my_tasks"),
    path("notifications/", WorkflowNotificationsView.as_view(), name="notifications"),
]
