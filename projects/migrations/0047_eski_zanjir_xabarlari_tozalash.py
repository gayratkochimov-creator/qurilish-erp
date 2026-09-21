# Eski (banner-orqali) limit zanjiri xabarlarini bazadan tozalash.
# Yangi tizimda zanjir xabari Xabar/banner sifatida YARATILMAYDI (faqat Telegram),
# shu sabab eski qolganlari foydalanuvchilar oynasida osilib turmasin.
from django.db import migrations
from django.db.models import Q


def tozalash(apps, schema_editor):
    Xabar = apps.get_model("projects", "Xabar")
    Xabar.objects.filter(
        Q(matn__contains="-bosqich.")               # "2/3-bosqich. ..." zanjir xabarlari
        | Q(matn__contains="zanjiri YAKUNLANDI")    # yakun xabarlari
    ).delete()


def orqaga(apps, schema_editor):
    pass   # o'chirilganlarni tiklab bo'lmaydi (ular kerak emas)


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0046_limitnavbat"),
    ]

    operations = [
        migrations.RunPython(tozalash, orqaga),
    ]
