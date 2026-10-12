# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

"""
URL configuration for cintafactory project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import URLResolver, include, path
from django.views.generic import RedirectView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from material.frontend import modules as material_modules

from account.views import AccountLogoutView
from cintafactory.admin_config import load_admin_config
from cintafactory.operations import views_health
from users import oauth_views

_admin_cipher = load_admin_config().get("cipher_url", "3b63cbd5-52d0-4af9-9a61-6a79d41a7b09")

urlpatterns = [
    path("", RedirectView.as_view(url="/accounts/login", permanent=False)),
    path("health/live", views_health.health_live, name="health_live"),
    path("health/ready", views_health.health_ready, name="health_ready"),
    path("metrics", views_health.metrics, name="metrics"),
    path(f"{_admin_cipher}/admin/", admin.site.urls),      # Django admin
    path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="api-schema"), name="api-redoc"),
    path("api/", include(("cintafactory.api.urls", "api"), namespace="api")),
    path("users/", include(("users.urls", "users"), namespace="users")),
    path("configuration/", include(("configuration.urls", "configuration"), namespace="configuration")),
    path("dat/", include(("dat.urls", "dat"), namespace="dat")),
    path("workflows/", include(("workflows.urls", "workflows"), namespace="workflows")),
    path("diagrams/", include(("diagrams.urls", "diagrams"), namespace="diagrams")),
    path("accounts/login/", oauth_views.LoginViewWithProviders.as_view(), name="login"),
    path("accounts/logout/", AccountLogoutView.as_view(), name="logout"),
    path("accounts/oauth/<slug:provider>/", oauth_views.oauth_login, name="oauth_login"),
    path("accounts/oauth/<slug:provider>/callback/", oauth_views.oauth_callback, name="oauth_callback"),
    path(
        "accounts/password_change/",
        auth_views.PasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "accounts/password_change/done/",
        auth_views.PasswordChangeDoneView.as_view(),
        name="password_change_done",
    ),
    path(
        "accounts/password_reset/",
        auth_views.PasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "accounts/password_reset/done/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "accounts/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "accounts/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("oauth/", include("oauth2_provider.urls", namespace="oauth2_provider")),
]

# Keep explicit app routes authoritative; Material also registers these modules.
_explicit_namespaces = {
    pattern.namespace for pattern in urlpatterns if isinstance(pattern, URLResolver)
}
_material_patterns = [
    pattern for pattern in material_modules.urls if pattern.namespace not in _explicit_namespaces
]
urlpatterns += [
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include(_material_patterns)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
