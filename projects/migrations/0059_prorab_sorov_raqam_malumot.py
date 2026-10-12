# Mavjud prorab so'rovlariga obyekt bo'yicha tartib raqami (yaratilish tartibida)
from django.db import migrations


def oldinga(apps, schema_editor):
    MaterialRequest = apps.get_model("projects", "MaterialRequest")
    hisob = {}
    for r in MaterialRequest.objects.order_by("project_id", "created_at", "id"):
        hisob[r.project_id] = hisob.get(r.project_id, 0) + 1
        MaterialRequest.objects.filter(pk=r.pk).update(raqam=hisob[r.project_id])


class Migration(migrations.Migration):
    dependencies = [("projects", "0058_prorab_sorov_raqam")]
    operations = [migrations.RunPython(oldinga, migrations.RunPython.noop)]
