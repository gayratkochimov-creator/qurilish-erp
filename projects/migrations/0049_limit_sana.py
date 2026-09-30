from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0048_limit_tahrir_ruxsat"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="limit_start",
            field=models.DateField(blank=True, null=True,
                                   verbose_name="Umumiy limit boshlanishi"),
        ),
        migrations.AddField(
            model_name="project",
            name="limit_end",
            field=models.DateField(blank=True, null=True,
                                   verbose_name="Umumiy limit tugashi"),
        ),
    ]
