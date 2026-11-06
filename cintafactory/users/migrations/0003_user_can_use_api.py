# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0002_remove_cintamaya_oauth_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="can_use_api",
            field=models.BooleanField(
                default=False,
                help_text="Autorise cet utilisateur à appeler l'API REST avec OAuth.",
            ),
        ),
    ]
