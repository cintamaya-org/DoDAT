# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from collections import Counter

from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordChangeDoneView, PasswordChangeView
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView

from .forms import AccountPasswordChangeForm
from users.oauth_providers import list_oauth_providers


class AccountProfileView(LoginRequiredMixin, TemplateView):
    template_name = "account/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        icon_map = {
            "google": "imgs/google_logo.svg",
            "microsoft": "imgs/microsoft_logo.svg",
            "amazon": "imgs/amazon_logo.svg",
            "okta": "imgs/okta_logo.svg",
        }
        connected_counts = Counter(user.oauth_accounts.values_list("provider", flat=True))
        providers = []
        for provider in list_oauth_providers():
            connected_count = connected_counts.get(provider.slug, 0)
            providers.append(
                {
                    "slug": provider.slug,
                    "label": provider.label,
                    "enabled": provider.enabled,
                    "connected": connected_count > 0,
                    "connected_count": connected_count,
                    "icon": icon_map.get(provider.slug, ""),
                }
            )
        context.update(
            {
                "user_obj": user,
                "role": getattr(user, "role", None),
                "oauth_providers": providers,
            }
        )
        return context


class AccountPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    template_name = "account/password_change.html"
    success_url = reverse_lazy("account:password_change_done")
    form_class = AccountPasswordChangeForm


class AccountPasswordChangeDoneView(LoginRequiredMixin, PasswordChangeDoneView):
    template_name = "account/password_change_done.html"


class AccountLogoutView(TemplateView):
    template_name = "registration/logged_out.html"
    http_method_names = [method.lower() for method in View.http_method_names]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            logout(request)
        handler = getattr(self, request.method.lower(), None)
        if handler is None:
            handler = self.get
        response = handler(request, *args, **kwargs)
        if request.method.upper() == "HEAD":
            response.content = b""
        return response

    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)
