"""Tasdiqlash zanjiri (PTO → Direktor → Admin) uchun ma'lumot ko'chirish:
- «Direktor» guruhini yaratish
- Eski so'rov statuslarini yangi zanjirga moslash
  · LimitChangeRequest 'pending'  → 'dir'  (direktor navbatiga)
  · WeeklyRequest      'submitted' → 'dir'  (direktor navbatiga)
Tasdiqlangan/rad etilganlar tegilmaydi.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="Direktor")

    LimitChangeRequest = apps.get_model("projects", "LimitChangeRequest")
    LimitChangeRequest.objects.filter(status="pending").update(status="dir")

    WeeklyRequest = apps.get_model("projects", "WeeklyRequest")
    WeeklyRequest.objects.filter(status="submitted").update(status="dir")


def backwards(apps, schema_editor):
    LimitChangeRequest = apps.get_model("projects", "LimitChangeRequest")
    LimitChangeRequest.objects.filter(status__in=["dir", "adm"]).update(status="pending")
    WeeklyRequest = apps.get_model("projects", "WeeklyRequest")
    WeeklyRequest.objects.filter(status="dir").update(status="submitted")


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0023_limitchangerequest_director_at_and_more"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
