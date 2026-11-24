# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from typing import Tuple

DAT_OWNER_EDITABLE_SECTION_SLUGS = frozenset({"informations-generales", "besoins"})

# Ordered list of required DAT participant roles (slug, fallback label)
DAT_REQUIRED_PARTICIPANT_ROLES: Tuple[Tuple[str, str], ...] = (
    ("porteur-demande", "Porteur de la demande"),
    ("architecte-referent", "Architecte referent"),
    ("architecte-technique", "Architecte technique"),
    ("urbaniste", "Urbaniste"),
    ("analyste-secu", "Analyste securite"),
    ("rssi", "RSSI"),
    ("comite-validation", "Comite de validation"),
    ("infra-exploitation", "Infra / Exploitation"),
)

DAT_PORTEUR_ROLE_SLUG = DAT_REQUIRED_PARTICIPANT_ROLES[0][0]
DAT_REQUIRED_PARTICIPANT_ROLE_SLUGS: Tuple[str, ...] = tuple(
    slug for slug, _ in DAT_REQUIRED_PARTICIPANT_ROLES
)
DAT_REQUIRED_PARTICIPANT_ROLE_LABELS = {
    slug: label for slug, label in DAT_REQUIRED_PARTICIPANT_ROLES
}
