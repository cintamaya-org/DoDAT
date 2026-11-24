# SPDX-License-Identifier: AGPL-3.0-only

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dat", "0006_dat_board_order_index")]

    operations = [
        migrations.CreateModel(
            name="DATReferenceSequence",
            fields=[
                ("application", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, primary_key=True,
                    related_name="+", serialize=False, to="dat.application",
                )),
                ("last_value", models.DecimalField(decimal_places=0, default=0, max_digits=64)),
            ],
            options={"db_table": "dat_reference_sequence"},
        ),
        migrations.AddConstraint(
            model_name="dat",
            constraint=models.UniqueConstraint(fields=["application", "reference"], name="dat_reference_per_application"),
        ),
        migrations.AlterField(model_name="dat", name="reference", field=models.CharField(blank=True, max_length=64)),
        migrations.RunSQL(
            """
            INSERT INTO dat_reference_sequence (application_id, last_value)
            SELECT application_id, MAX(reference::numeric)
            FROM dat_dat
            WHERE reference ~ '^[0-9]+$'
            GROUP BY application_id
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
