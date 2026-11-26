# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib import messages
from material.frontend.views import DeleteModelView


class FrenchCreateMessageMixin:
    """Use French success text for Material model create views."""

    def message_user(self):
        messages.success(self.request, "Enregistrement créé avec succès.")


class FrenchUpdateMessageMixin:
    """Use French success text for Material model update views."""

    def message_user(self):
        messages.success(self.request, "Enregistrement mis à jour avec succès.")


class FrenchDeleteMessageMixin:
    """Use French success text for Material model delete views."""

    def message_user(self):
        messages.success(
            self.request,
            f"Suppression effectuée : « {self.object} ».",
            fail_silently=True,
        )


class FrenchDeleteView(FrenchDeleteMessageMixin, DeleteModelView):
    """Show French success text after deleting a Material model."""
