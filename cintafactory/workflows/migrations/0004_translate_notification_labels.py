# SPDX-FileCopyrightText: 2026 Cintamaya <contact@cintamaya.com>
# SPDX-FileCopyrightText: 2026 Baptiste COQUELET <github.com/BaptisteCoquelet>
# SPDX-License-Identifier: AGPL-3.0-only

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0003_versioned_workflow_engine"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="notificationmessage",
            options={
                "ordering": ["pk"],
                "verbose_name": "Message de notification",
                "verbose_name_plural": "Messages de notification",
            },
        ),
        migrations.AlterModelOptions(
            name="notificationtype",
            options={
                "ordering": ["title", "level", "pk"],
                "verbose_name": "Type de notification",
                "verbose_name_plural": "Types de notification",
            },
        ),
        migrations.AlterModelOptions(
            name="usernotification",
            options={
                "ordering": ["-created_at", "-pk"],
                "verbose_name": "Notification utilisateur",
                "verbose_name_plural": "Notifications utilisateur",
            },
        ),
        migrations.AlterModelOptions(
            name="historynotificationseen",
            options={
                "ordering": ["-seen_at", "-pk"],
                "verbose_name": "Consultation d’historique de workflow",
                "verbose_name_plural": "Consultations d’historique de workflow",
            },
        ),
    ]
