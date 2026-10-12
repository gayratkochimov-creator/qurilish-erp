# Mavjud ma'lumot: har obyektning limiti/haftaligi/so'rovi bo'lsa — 1-лимит yaratilib,
# hammasi unga biriktiriladi (obyekt darajasidagi ruxsat/sana ham ko'chadi).
from django.db import migrations


def oldinga(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    Limit = apps.get_model("projects", "Limit")
    LimitItem = apps.get_model("projects", "LimitItem")
    WeeklyRequest = apps.get_model("projects", "WeeklyRequest")
    LimitChangeRequest = apps.get_model("projects", "LimitChangeRequest")
    for p in Project.objects.all().order_by('id'):
        bor = (LimitItem.objects.filter(project=p).exists()
               or WeeklyRequest.objects.filter(project=p).exists()
               or LimitChangeRequest.objects.filter(project=p).exists()
               or (p.limit_material or 0) + (p.limit_labor or 0) + (p.limit_machinery or 0) + (p.limit_other or 0) > 0)
        if not bor:
            continue
        lim, _ = Limit.objects.get_or_create(
            project=p, raqam=1,
            defaults={"tahrir_ruxsat": p.limit_tahrir_ruxsat, "start": p.limit_start, "end": p.limit_end})
        LimitItem.objects.filter(project=p, limit__isnull=True).update(limit=lim)
        WeeklyRequest.objects.filter(project=p, limit__isnull=True).update(limit=lim)
        LimitChangeRequest.objects.filter(project=p, limit__isnull=True).update(limit=lim)


def orqaga(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("projects", "0056_raqamli_limit")]
    operations = [migrations.RunPython(oldinga, orqaga)]
