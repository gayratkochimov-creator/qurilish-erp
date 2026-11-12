"""Chek/nakladnoy rasmlari xavfsizligi (audit Y3, 2026-11-12).

1) rasm_tayyorla(f) — yuklangan fayl HAQIQATAN rasm ekanini Pillow bilan tekshiradi va
   nomini tasodifiy qiladi (asl nom/kengaytma ishlatilmaydi → .html/.svg yuklab bo'lmaydi,
   manzilni taxmin qilib bo'lmaydi).
2) media_korish — /media/... faqat tizimga KIRGAN va shu omborni ko'ra oladigan foydalanuvchiga.
   (Serverda /media/ statik xaritasi turgan bo'lsa bu view chaqirilmaydi — xaritani olib tashlash kerak.)
"""
import uuid

from django.contrib.auth.decorators import login_required
from django.http import Http404

RUXSAT_FORMAT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "BMP": "bmp", "MPO": "jpg"}


def rasm_tayyorla(f):
    """True — fayl rasm, nomi tasodifiyga almashtirildi. False — rasm emas (saqlanmasin)."""
    from PIL import Image
    try:
        f.seek(0)
        with Image.open(f) as im:
            fmt = (im.format or "").upper()
            im.verify()
    except Exception:
        return False
    finally:
        try:
            f.seek(0)
        except Exception:
            pass
    ext = RUXSAT_FORMAT.get(fmt)
    if not ext:
        return False
    f.name = f"{uuid.uuid4().hex}.{ext}"
    return True


@login_required
def media_korish(request, path):
    from django.conf import settings
    from django.views.static import serve

    from .models import Receipt, ReceiptImage
    from .views import _visible_warehouses

    if not request.user.is_superuser:
        wh = (ReceiptImage.objects.filter(image=path).values_list("receipt__warehouse_id", flat=True).first()
              or Receipt.objects.filter(image=path).values_list("warehouse_id", flat=True).first())
        if not wh or not _visible_warehouses(request.user).filter(pk=wh).exists():
            raise Http404("Fayl topilmadi")
    resp = serve(request, path, document_root=settings.MEDIA_ROOT)
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, max-age=3600"
    return resp
