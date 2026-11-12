"""Limitdagi TAKRORIY qatorlar (bir blokda bir xil nom + tur) — ko'rish va AYNAN bir xillarini o'chirish.

Sabab: Excel ikki marta yuklansa yoki blok nusxalansa, bir xil qatorlar ikki martadan tushadi —
umumiy limit summasi ikki barobar bo'ladi, jadvalda «... блокида 2 марта» xatosi chiqadi.

Ishlatish (avval KO'RISH, keyin QO'LLASH):
  python manage.py limit_dublikat --obyekt OB-013
  python manage.py limit_dublikat --obyekt OB-013 --qolla

Qoidalar:
  * «Aynan bir xil» = bo'lim, nom, tur, birlik, hajm, narx — hammasi teng. Faqat shular o'chiriladi:
    birinchi qator qoladi, qolgan nusxalari o'chadi. Haftalik tarix nom orqali bog'langani uchun
    birinchi qatorda qoladi — «Бажарилган» o'zgarmaydi.
  * Nomi bir xil, lekin hajm/narxi FARQLI qatorlar — bu boshqa ish bo'lishi mumkin: faqat ro'yxatda
    ko'rsatiladi, tegilmaydi (nomini saytda farqlang).
  * To'lov (Moliya) yozilgan nusxa o'chirilmaydi.
"""
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


def _pul(v):
    return f"{v:,.0f}".replace(",", " ")


class Command(BaseCommand):
    help = "Limitdagi takroriy qatorlarni ko'rsatadi; --qolla bilan aynan bir xil nusxalarni o'chiradi."

    def add_arguments(self, parser):
        parser.add_argument("--obyekt", required=True, help="Obyekt kodi (OB-013) yoki nomining bir qismi")
        parser.add_argument("--qolla", action="store_true", help="Aynan bir xil nusxalarni o'chirish (aks holda faqat ko'rsatadi)")

    def handle(self, *args, **o):
        from projects.models import LimitItem, Project, tolovli_limit_qatori

        q = o["obyekt"].strip()
        topildi = list(Project.objects.filter(code=q)) or list(Project.objects.filter(name__icontains=q))
        if not topildi:
            raise CommandError(f"Obyekt topilmadi: {q}")
        if len(topildi) > 1:
            raise CommandError("Bir nechta obyekt mos keldi: " + ", ".join(f"{p.code} ({p.name})" for p in topildi)
                               + " — kodini aniq yozing.")
        p = topildi[0]
        w = self.stdout.write
        w(f"Obyekt: {p.code} — {p.name}")

        guruh = defaultdict(list)
        for li in LimitItem.objects.filter(project=p).order_by("id"):
            kalit = (li.limit_id, (li.bolim or "").strip().lower(), li.kind, (li.name or "").strip().lower())
            guruh[kalit].append(li)
        takror = {k: v for k, v in guruh.items() if len(v) > 1}
        jami_qator = sum(len(v) for v in guruh.values())
        w(f"Jami qator: {jami_qator} | takroriy nom: {len(takror)}")
        if not takror:
            w(self.style.SUCCESS("Takroriy qator yo'q."))
            return

        ochiriladi, farqli, tolovli = [], [], []
        for (lim_id, bolim, kind, nom), qatorlar in takror.items():
            asosiy = qatorlar[0]
            for li in qatorlar[1:]:
                bir_xil = ((li.unit or "").strip().lower() == (asosiy.unit or "").strip().lower()
                           and li.quantity == asosiy.quantity and li.unit_price == asosiy.unit_price)
                if not bir_xil:
                    farqli.append((asosiy, li))
                elif tolovli_limit_qatori(li):
                    tolovli.append(li)
                else:
                    ochiriladi.append(li)

        summa = sum(((li.quantity or Decimal("0")) * (li.unit_price or Decimal("0")) for li in ochiriladi), Decimal("0"))
        w("")
        w(f"AYNAN BIR XIL nusxalar (o'chiriladi): {len(ochiriladi)} ta, summasi {_pul(summa)}")
        for li in ochiriladi[:40]:
            w(f"  - #{li.pk} | {li.bolim or 'Bo`limsiz'} | {li.name} | {float(li.quantity):g} {li.unit} x {_pul(li.unit_price)}")
        if len(ochiriladi) > 40:
            w(f"  ... yana {len(ochiriladi) - 40} ta")
        if farqli:
            w("")
            w(f"NOMI BIR XIL, hajm/narxi FARQLI (tegilmaydi — saytda nomini farqlang): {len(farqli)} ta")
            for a, li in farqli[:40]:
                w(f"  - {li.bolim or 'Bo`limsiz'} | {li.name}: #{a.pk} {float(a.quantity):g} x {_pul(a.unit_price)}  va  "
                  f"#{li.pk} {float(li.quantity):g} x {_pul(li.unit_price)}")
        if tolovli:
            w("")
            w(f"TO'LOV yozilgan nusxalar (o'chirilmaydi): {len(tolovli)} ta")
            for li in tolovli[:40]:
                w(f"  - #{li.pk} | {li.bolim or 'Bo`limsiz'} | {li.name}")

        if not o["qolla"]:
            w("")
            w("Hech narsa o'zgartirilmadi. O'chirish uchun shu buyruqqa --qolla qo'shing (avval zaxira oling).")
            return
        if not ochiriladi:
            w("")
            w("O'chiriladigan aynan bir xil nusxa yo'q.")
            return
        eski = p.budget_total
        with transaction.atomic():
            LimitItem.objects.filter(pk__in=[li.pk for li in ochiriladi]).delete()
            p.recompute_limits()
        p.refresh_from_db()
        w("")
        w(self.style.SUCCESS(f"O'chirildi: {len(ochiriladi)} ta qator. Umumiy limit: {_pul(eski)} -> {_pul(p.budget_total)}"))
