# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.db import migrations


def remove_cintamaya_oauth_accounts(apps, schema_editor):
    OAuthAccount = apps.get_model("users", "OAuthAccount")
    OAuthAccount.objects.using(schema_editor.connection.alias).filter(
        provider="cintamaya"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        # Token and profile data cannot be reconstructed after deletion.
        migrations.RunPython(remove_cintamaya_oauth_accounts),
    ]
