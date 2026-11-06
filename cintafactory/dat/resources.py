# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from typing import Any, Dict, List


RESOURCE_SECTION_SLUG = "exploitation"
RESOURCE_SUB_SECTION_SLUG = "ressources-solution"
RESOURCE_ENTRY_KEY = "ressources"
RESOURCE_TYPE_KEY = "type_ressource"
LEGACY_RESOURCE_TYPES = {
    "ressources_existantes": "sans_modification",
    "ressources_a_ajouter": "a_ajouter",
    "ressources_a_supprimer": "a_supprimer",
}


def group_resource_rows(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Merge resource tables without mutating their original rows."""
    rows = []
    for key in (RESOURCE_ENTRY_KEY, *LEGACY_RESOURCE_TYPES):
        value = values.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise ValueError("Les ressources doivent être une liste de lignes.")
        for row in value:
            resource = dict(row)
            if key in LEGACY_RESOURCE_TYPES:
                resource[RESOURCE_TYPE_KEY] = LEGACY_RESOURCE_TYPES[key]
            rows.append(resource)
    return rows
