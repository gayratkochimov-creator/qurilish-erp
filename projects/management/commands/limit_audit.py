"""Obyektlar limiti YAGONA QOLIPga (limit jadvali) qanchalik mosligini tekshiradi.

Faqat O'QIYDI — bazaga hech narsa yozmaydi. Ishga tushirish:
    python manage.py limit_audit
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from projects.models import Project


def _kalit(nom, bolim):
    return (nom or "").strip().lower() + "|" + (bolim or "").strip().lower()


class Command(BaseCommand):
    help = "Har obyekt limiti yagona qolipga mosligini ko'rsatadi (faqat o'qiydi)."

    def handle(self, *args, **opts):
        mos, muammoli, bosh = 0, 0, 0
        for p in Project.objects.select_related("firma").order_by("firma__name", "code"):
            qatorlar = list(p.limit_items.all())
            kalitlar = {}
            for li in qatorlar:
                k = _kalit(li.name, li.bolim)
                kalitlar[k] = kalitlar.get(k, 0) + 1
            jami = p.budget_total or Decimal("0")
            belgilar = []
            if jami > 0 and not qatorlar:
                summa = f"{jami:,.0f}".replace(",", " ")
                belgilar.append(f"ESKI USUL: faqat summa ({summa}), qator yo'q")
            bolimsiz = sum(1 for li in qatorlar if not (li.bolim or "").strip())
            if bolimsiz:
                belgilar.append(f"bo'limsiz qator: {bolimsiz}/{len(qatorlar)}")
            dubl = sum(1 for v in kalitlar.values() if v > 1)
            if dubl:
                belgilar.append(f"takror nom+bo'lim: {dubl}")
            narxsiz = sum(1 for li in qatorlar if not li.unit_price)
            if narxsiz:
                belgilar.append(f"narxsiz qator: {narxsiz}")
            mos_emas = 0
            for w in p.weekly_requests.exclude(status="rejected").prefetch_related("items"):
                for it in w.items.all():
                    if _kalit(it.name, it.bolim) not in kalitlar:
                        mos_emas += 1
            if mos_emas:
                belgilar.append(f"limitda yo'q haftalik qator: {mos_emas}")
            for r in p.limit_requests.filter(status__in=["snab", "pto2", "dir", "adm"]):
                if not r.proposed_items.exists():
                    belgilar.append(f"zanjirda QATORSIZ (eski) so'rov #{r.pk} [{r.status}]")
                else:
                    belgilar.append(f"zanjirda so'rov #{r.pk} [{r.status}]")
            firma = p.firma.name if p.firma_id else "—"
            if not belgilar and not qatorlar:
                bosh += 1
                continue
            if belgilar:
                muammoli += 1
                self.stdout.write(f"[!] {firma} / {p.name} (id={p.pk}): " + "; ".join(belgilar))
            else:
                mos += 1
                self.stdout.write(f"[ok] {firma} / {p.name} (id={p.pk}): {len(qatorlar)} qator")
        self.stdout.write("")
        self.stdout.write(f"JAMI: qolipga mos — {mos}, e'tibor kerak — {muammoli}, limitsiz (bo'sh) — {bosh}")
