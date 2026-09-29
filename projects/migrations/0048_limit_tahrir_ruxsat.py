from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0047_eski_zanjir_xabarlari_tozalash"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="limit_tahrir_ruxsat",
            field=models.BooleanField(
                default=False, verbose_name="Limit tahririga admin ruxsati"),
        ),
    ]
