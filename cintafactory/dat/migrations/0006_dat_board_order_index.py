# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("dat", "0005_dat_secure_export_approval"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="dat",
            index=models.Index(
                fields=["-updated_at", "-id"],
                name="dat_updated_pk_desc_idx",
            ),
        ),
    ]
