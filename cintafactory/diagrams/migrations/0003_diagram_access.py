# SPDX-License-Identifier: AGPL-3.0-only

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("diagrams", "0002_initial"),
        ("dat", "0006_dat_board_order_index"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="drawiodiagram", name="dat",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name="%(class)s_diagrams", to="dat.dat"),
        ),
        migrations.AddField(
            model_name="drawiodiagram", name="sections",
            field=models.ManyToManyField(blank=True, related_name="%(class)s_diagrams", to="dat.datsection"),
        ),
        migrations.AddField(
            model_name="likec4diagram", name="dat",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name="%(class)s_diagrams", to="dat.dat"),
        ),
        migrations.AddField(
            model_name="likec4diagram", name="sections",
            field=models.ManyToManyField(blank=True, related_name="%(class)s_diagrams", to="dat.datsection"),
        ),
        migrations.AddField(
            model_name="likec4diagram", name="owner",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="likec4_diagrams", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="likec4diagram", name="revision",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
