"""Bir xil nomli limit qatorlari (masalan «Прожектор ремонт қилиш» ×3) tufayli haftalik
qatorlar BIRINCHI qatorga va UNING narxida yozilib ketgan holatni tuzatadi.

Mantiq (obyekt + nom boshlanishi bo'yicha):
  1. Limit qatorlari topiladi (nom shu so'z bilan boshlanadi), ularning ichida
     «asosiy» (eng katta summa) va «qo'shimcha» qatorlar (izohi bilan farqlanadi).
  2. Haftalik qatorlar (rad etilmaganlar) ichida shu nomli qatorlar ko'rib chiqiladi:
     miqdori qo'shimcha limit qatorining miqdoriga teng bo'lganlari o'sha qatorga
     o'tkaziladi (nom = limit qatorining YANGI nomi, narx = limit qatori narxi).
     Bir haftada asosiy miqdor ikki marta bo'lsa — ortiqchasi o'chiriladi.
  3. Qo'shimcha limit qatorlari nomi «<nom> — <izoh>» ko'rinishiga keltiriladi
     (agar hali bir xil nomda bo'lsa) — endi har qator o'z haftaligini oladi.

Ishlatish (avval KO'RISH, keyin QO'LLASH):
  python manage.py dubl_nom_tuzat --obyekt ОБ-005 --nom "Прожектор"
  python manage.py dubl_nom_tuzat --obyekt ОБ-005 --nom "Прожектор" --qolla
To'lov (Moliya) yozilgan haftalik qatorlar o'zgartirilmaydi — ro'yxatda ko'rsatiladi.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Bir xil nomli limit qatorlari tufayli xato yozilgan haftalik qatorlarni tuzatadi (avval dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--obyekt", required=True, help="Obyekt kodi, masalan ОБ-005")
        parser.add_argument("--nom", required=True, help="Qator nomi boshlanishi, masalan «Прожектор»")
        parser.add_argument("--qolla", action="store_true", help="O'zgarishlarni bazaga yozish (aks holda faqat ko'rsatadi)")

    def handle(self, *args, **o):
        from projects.models import LimitItem, Project, WeeklyRequestItem
        from projects.views import _lj_key

        p = Project.objects.filter(code=o["obyekt"]).first()
        if p is None:
            raise CommandError(f"Obyekt topilmadi: {o['obyekt']}")
        nom = o["nom"].strip()
        qolla = o["qolla"]
        out = self.stdout.write

        lims = list(LimitItem.objects.filter(project=p, name__istartswith=nom).order_by("id"))
        if len(lims) < 2:
            raise CommandError(f"«{nom}» bilan boshlanadigan 2+ limit qatori topilmadi ({len(lims)} ta).")
        out(f"== {p.code} — {p.name}: «{nom}…» limit qatorlari ==")
        for li in lims:
            out(f"  #{li.id} | {li.name} | izoh: {li.note or '—'} | {li.quantity} {li.unit} × {li.unit_price} = {li.total} | bo'lim: {li.bolim or '—'}")
        # Bir xil bo'limdagilar bitta guruh
        bolim = (lims[0].bolim or "").strip().lower()
        if any((li.bolim or "").strip().lower() != bolim for li in lims):
            raise CommandError("Qatorlar turli bo'limlarda — buyruq faqat bitta bo'lim uchun.")
        asosiy = max(lims, key=lambda x: x.total)
        qoshimcha = [li for li in lims if li.pk != asosiy.pk]
        out(f"  asosiy: #{asosiy.id} «{asosiy.name}» ({asosiy.quantity} × {asosiy.unit_price})")

        # Qo'shimcha qatorlarning YANGI nomlari
        yangi_nom = {}
        for li in qoshimcha:
            if li.name.strip().lower() == asosiy.name.strip().lower() or li.name.strip().lower() == nom.lower() \
                    or any(li.name.strip().lower() == x.name.strip().lower() for x in lims if x.pk != li.pk):
                izoh = (li.note or "").strip()
                if not izoh:
                    raise CommandError(f"#{li.id} qatorining izohi bo'sh — nomini qo'lda farqlang, keyin qayta ishga tushiring.")
                yangi_nom[li.pk] = f"{li.name.strip()} — {izoh}"
            else:
                yangi_nom[li.pk] = li.name.strip()   # allaqachon farqli
        for li in qoshimcha:
            if yangi_nom[li.pk] != li.name:
                out(f"  nom: #{li.id} «{li.name}» → «{yangi_nom[li.pk]}»")

        # Haftalik qatorlar: eski nomlar (hozirgi limit qatorlari nomlari) bilan, rad etilmagan
        nomlar = {li.name.strip().lower() for li in lims} | {asosiy.name.strip().lower()}
        witems = list(WeeklyRequestItem.objects.filter(request__project=p)
                      .exclude(request__status="rejected")
                      .select_related("request").prefetch_related("moliyalar").order_by("request__week_start", "id"))
        witems = [it for it in witems if it.name.strip().lower() in nomlar
                  and (it.bolim or "").strip().lower() == bolim]
        out(f"== Haftalik qatorlar ({len(witems)} ta) ==")
        ozgar, ochir, tolovli = [], [], []
        korilgan_asosiy = {}
        qty_map = {Decimal(li.quantity): li for li in qoshimcha}
        for it in witems:
            w = it.request
            tag = f"hafta {w.week_start:%d.%m}–{w.week_end:%d.%m.%Y} [{w.status}] #{it.id}: «{it.name}» {it.quantity} × {it.unit_price}"
            tolov = any(not m.bekor for m in it.moliyalar.all())
            q = Decimal(it.quantity)
            li = qty_map.get(q)
            if li is not None and (it.name.strip().lower() != yangi_nom[li.pk].lower() or Decimal(it.unit_price) != Decimal(li.unit_price)):
                if tolov:
                    tolovli.append(tag); out(f"  TO'LOVLI (o'zgarmaydi): {tag}"); continue
                ozgar.append((it, yangi_nom[li.pk], li.unit_price))
                out(f"  O'TKAZISH: {tag} → «{yangi_nom[li.pk]}» × {li.unit_price}")
            elif q == Decimal(asosiy.quantity):
                k = w.pk
                if k in korilgan_asosiy:
                    if tolov:
                        tolovli.append(tag); out(f"  TO'LOVLI dublikat (o'zgarmaydi): {tag}"); continue
                    ochir.append(it); out(f"  O'CHIRISH (bir haftada ikkinchi {asosiy.quantity}): {tag}")
                else:
                    korilgan_asosiy[k] = it; out(f"  qoladi: {tag}")
            else:
                out(f"  qoladi: {tag}")

        if not qolla:
            out(self.style.WARNING(f"\nDRY-RUN: {len(ozgar)} ta o'tkazish, {len(ochir)} ta o'chirish, "
                                   f"{sum(1 for li in qoshimcha if yangi_nom[li.pk] != li.name)} ta nom. "
                                   "Qo'llash uchun --qolla qo'shing."))
            return
        with transaction.atomic():
            for li in qoshimcha:
                if yangi_nom[li.pk] != li.name:
                    li.name = yangi_nom[li.pk]; li.save(update_fields=["name"])
            for it, nm, narx in ozgar:
                it.name = nm; it.unit_price = narx; it.save(update_fields=["name", "unit_price"])
            for it in ochir:
                it.delete()
            p.recompute_limits()
        out(self.style.SUCCESS(f"QO'LLANDI: {len(ozgar)} ta o'tkazildi, {len(ochir)} ta o'chirildi. "
                               f"{len(tolovli)} ta to'lovli qator tegilmadi."))
