"""Narx o'zgarishi tahlili: REJA — BAJARILGAN — QOLGAN — NARX FARQI.

python manage.py test projects.tests_narx
"""
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (LimitChangeRequest, LimitItem, Project, WeeklyRequest,
                     WeeklyRequestItem)
from .views import _narx_tahlil, _narx_farq_foiz, NARX_FARQ_FOIZ


def _hafta(p, n, status="approved"):
    ws = datetime.date(2026, 1, 5) + datetime.timedelta(days=7 * n)
    return WeeklyRequest.objects.create(project=p, week_start=ws, week_end=ws + datetime.timedelta(days=6),
                                        status=status)


class NarxTahlilTest(TestCase):
    def setUp(self):
        self.p = Project.objects.create(code="T1", name="Test obyekt")
        # Beton: 100 m3 × 800 000 = 80 mln
        LimitItem.objects.create(project=self.p, kind="material", name="Beton", unit="m3",
                                 quantity=Decimal("100"), unit_price=Decimal("800000"), bolim="Poydevor")
        self.p.recompute_limits()

    def test_narx_ozgarmagan(self):
        w = _hafta(self.p, 0)
        WeeklyRequestItem.objects.create(request=w, kind="material", name="Beton", unit="m3",
                                         quantity=Decimal("30"), unit_price=Decimal("800000"), bolim="Poydevor")
        qat, kat, umum = _narx_tahlil(self.p)
        d = qat["beton|poydevor"]
        self.assertEqual(d["fakt_qty"], Decimal("30"))
        self.assertEqual(d["qolgan_qty"], Decimal("70"))
        self.assertEqual(d["joriy"], Decimal("800000"))
        self.assertEqual(d["prognoz"], Decimal("56000000.00"))
        self.assertEqual(d["farq"], Decimal("0.00"))
        self.assertFalse(d["narx_ozgargan"])
        self.assertEqual(umum["kerak"], Decimal("80000000.00"))

    def test_narx_oshdi_misol(self):
        """Foydalanuvchi misoli: 30×800 000 + 30×900 000 → qolgan 40×900 000, farq +7 mln."""
        w1 = _hafta(self.p, 0)
        WeeklyRequestItem.objects.create(request=w1, kind="material", name="Beton", unit="m3",
                                         quantity=Decimal("30"), unit_price=Decimal("800000"), bolim="Poydevor")
        w2 = _hafta(self.p, 1)
        WeeklyRequestItem.objects.create(request=w2, kind="material", name="Beton", unit="m3",
                                         quantity=Decimal("30"), unit_price=Decimal("900000"), bolim="Poydevor")
        qat, kat, umum = _narx_tahlil(self.p)
        d = qat["beton|poydevor"]
        self.assertEqual(d["fakt_qty"], Decimal("60"))
        self.assertEqual(d["fakt_sum"], Decimal("51000000.00"))
        self.assertEqual(d["fakt_avg"], Decimal("850000.00"))
        self.assertEqual(d["joriy"], Decimal("900000"))
        self.assertEqual(d["qolgan_qty"], Decimal("40"))
        self.assertEqual(d["prognoz"], Decimal("36000000.00"))
        self.assertEqual(d["farq_sarf"], Decimal("3000000.00"))
        self.assertEqual(d["farq_kut"], Decimal("4000000.00"))
        self.assertEqual(d["farq"], Decimal("7000000.00"))
        self.assertEqual(d["farq_foiz"], Decimal("12.5"))
        self.assertTrue(d["narx_ozgargan"])
        self.assertEqual(kat["material"]["farq"], Decimal("7000000.00"))
        self.assertEqual(umum["kerak"], Decimal("87000000.00"))
        self.assertEqual(umum["ozgargan"], 1)

    def test_joriy_narx_oxirgi_hafta(self):
        """Joriy narx — sana bo'yicha ENG OXIRGI tasdiqlangan hafta (yaratilish tartibi emas)."""
        w2 = _hafta(self.p, 1)
        WeeklyRequestItem.objects.create(request=w2, kind="material", name="Beton",
                                         quantity=Decimal("10"), unit_price=Decimal("950000"), bolim="Poydevor")
        w1 = _hafta(self.p, 0)   # keyin yaratilgan, lekin sana bo'yicha oldingi
        WeeklyRequestItem.objects.create(request=w1, kind="material", name="Beton",
                                         quantity=Decimal("10"), unit_price=Decimal("820000"), bolim="Poydevor")
        qat, _, _ = _narx_tahlil(self.p)
        self.assertEqual(qat["beton|poydevor"]["joriy"], Decimal("950000"))

    def test_tasdiqlanmagan_hisobga_olinmaydi(self):
        w = _hafta(self.p, 0, status="dir")
        WeeklyRequestItem.objects.create(request=w, kind="material", name="Beton",
                                         quantity=Decimal("30"), unit_price=Decimal("900000"), bolim="Poydevor")
        qat, _, umum = _narx_tahlil(self.p)
        self.assertEqual(qat["beton|poydevor"]["fakt_qty"], Decimal("0"))
        self.assertEqual(umum["farq"], Decimal("0"))

    def test_narx_tushdi_tejam(self):
        w = _hafta(self.p, 0)
        WeeklyRequestItem.objects.create(request=w, kind="material", name="Beton",
                                         quantity=Decimal("50"), unit_price=Decimal("700000"), bolim="Poydevor")
        qat, _, umum = _narx_tahlil(self.p)
        d = qat["beton|poydevor"]
        # 50×(700−800) + 50×(700−800) = −10 mln
        self.assertEqual(d["farq"], Decimal("-10000000.00"))
        self.assertLess(umum["farq"], 0)

    def test_miqdor_oshsa_narx_farqi_emas(self):
        """Miqdor limitdan oshsa — bu NARX farqi emas (prognoz 0, farq faqat narxdan)."""
        w = _hafta(self.p, 0)
        WeeklyRequestItem.objects.create(request=w, kind="material", name="Beton",
                                         quantity=Decimal("120"), unit_price=Decimal("800000"), bolim="Poydevor")
        qat, _, _ = _narx_tahlil(self.p)
        d = qat["beton|poydevor"]
        self.assertEqual(d["qolgan_qty"], Decimal("-20"))
        self.assertEqual(d["prognoz"], Decimal("0.00"))
        self.assertEqual(d["farq"], Decimal("0.00"))

    def test_limitda_yoq_qator_kerakka_kiradi(self):
        """Limitda yo'q nom (yoki boshqa bo'lim) — «joriy narxda kerak» sarflangandan kam bo'lmasin."""
        w = _hafta(self.p, 0)
        WeeklyRequestItem.objects.create(request=w, kind="material", name="Beton",
                                         quantity=Decimal("10"), unit_price=Decimal("800000"), bolim="Tom")
        _, kat, umum = _narx_tahlil(self.p)
        self.assertEqual(umum["yoq"], Decimal("8000000.00"))
        self.assertEqual(kat["material"]["fakt_sum"], Decimal("8000000.00"))
        self.assertEqual(umum["kerak"], Decimal("88000000.00"))   # 8 mln + 100×800 000
        self.assertGreaterEqual(umum["kerak"], self.p.sarflangan())

    def test_foiz(self):
        self.assertEqual(_narx_farq_foiz(Decimal("800000"), Decimal("900000")), Decimal("12.5"))
        self.assertEqual(_narx_farq_foiz(Decimal("0"), Decimal("900000")), Decimal("0"))
        self.assertEqual(_narx_farq_foiz(Decimal("800000"), Decimal("760000")), Decimal("-5.0"))


class NarxOqimTest(TestCase):
    """weekly_add izoh talabi va limitni joriy narxda yangilash so'rovi."""

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("admin", "a@a.uz", "pw")
        self.pto = User.objects.create_user("pto", "p@p.uz", "pw")
        from .models import UserProfile
        self.p = Project.objects.create(code="T2", name="Oqim")
        LimitItem.objects.create(project=self.p, kind="material", name="Beton", unit="m3",
                                 quantity=Decimal("100"), unit_price=Decimal("800000"), bolim="Poydevor")
        self.p.recompute_limits()
        # PTO faqat o'ziga BIRIKTIRILGAN obyektni ko'radi (multi-tenant izolyatsiya)
        prof, _ = UserProfile.objects.update_or_create(user=self.pto, defaults={"role": "pto"})
        prof.projects.add(self.p)

    def _post_hafta(self, narx, izoh=""):
        return self.client.post(reverse("weekly_add", args=[self.p.id]), {
            "week_start": "2026-02-02", "week_end": "2026-02-08",
            "kind": ["material"], "name": ["Beton"], "unit": ["m3"],
            "quantity": ["10"], "unit_price": [str(narx)], "item_note": [izoh], "item_bolim": ["Poydevor"],
        })

    def test_izohsiz_narx_ozgarishi_rad(self):
        self.client.force_login(self.pto)
        r = self._post_hafta(900000)          # +12.5% > NARX_FARQ_FOIZ
        self.assertEqual(r.status_code, 302)
        self.assertEqual(WeeklyRequest.objects.count(), 0)

    def test_izoh_bilan_otadi(self):
        self.client.force_login(self.pto)
        self._post_hafta(900000, izoh="Zavod narxni oshirdi")
        self.assertEqual(WeeklyRequest.objects.count(), 1)

    def test_kichik_farq_izohsiz_otadi(self):
        self.client.force_login(self.pto)
        self._post_hafta(820000)              # +2.5% ≤ chegara
        self.assertEqual(WeeklyRequest.objects.count(), 1)
        self.assertGreater(NARX_FARQ_FOIZ, Decimal("2.5"))

    def test_bosh_sorov_yaratilmaydi(self):
        self.client.force_login(self.pto)
        self.client.post(reverse("weekly_add", args=[self.p.id]), {
            "week_start": "2026-02-02", "week_end": "2026-02-08",
            "kind": ["material"], "name": ["Beton"], "unit": ["m3"],
            "quantity": [""], "unit_price": [""], "item_note": [""], "item_bolim": [""],
        })
        self.assertEqual(WeeklyRequest.objects.count(), 0)

    def test_excel_narx_farqi_formulasi(self):
        from .views import _obj_limit_wb
        ws = _obj_limit_wb(self.p)["Limit (Reja-Fakt)"]
        self.assertEqual(ws.cell(7, 15).value, "=K7-I7*G7+MAX(L7,0)*(M7-G7)")

    def test_limit_narx_yangilash_sorov(self):
        w = _hafta(self.p, 0)
        WeeklyRequestItem.objects.create(request=w, kind="material", name="Beton",
                                         quantity=Decimal("30"), unit_price=Decimal("900000"), bolim="Poydevor")
        self.client.force_login(self.pto)
        r = self.client.post(reverse("limit_narx_yangilash", args=[self.p.id]))
        self.assertEqual(r.status_code, 302)
        req = LimitChangeRequest.objects.get()
        self.assertEqual(req.status, "dir")   # snab yo'q → to'g'ridan-to'g'ri direktorga
        self.assertEqual(req.new_material, Decimal("90000000.00"))   # 100 × 900 000
        it = req.proposed_items.get()
        self.assertEqual(it.unit_price, Decimal("900000"))
        self.assertEqual(it.quantity, Decimal("100"))               # miqdor o'zgarmaydi
        self.assertIn("800 000 → 900 000", req.reason)
        # Limit hali o'zgarmagan — tasdiq zanjiri kerak
        self.p.refresh_from_db()
        self.assertEqual(self.p.limit_material, Decimal("80000000.00"))

    def test_limit_narx_yangilash_admin_togridan(self):
        w = _hafta(self.p, 0)
        WeeklyRequestItem.objects.create(request=w, kind="material", name="Beton",
                                         quantity=Decimal("30"), unit_price=Decimal("900000"), bolim="Poydevor")
        self.client.force_login(self.admin)
        self.client.post(reverse("limit_narx_yangilash", args=[self.p.id]))
        self.p.refresh_from_db()
        self.assertEqual(self.p.limit_material, Decimal("90000000.00"))
        self.assertEqual(LimitChangeRequest.objects.count(), 0)

    def test_ozgarish_yoq_sorov_yaratilmaydi(self):
        self.client.force_login(self.pto)
        self.client.post(reverse("limit_narx_yangilash", args=[self.p.id]))
        self.assertEqual(LimitChangeRequest.objects.count(), 0)

    def test_detail_sahifa_ochiladi(self):
        w = _hafta(self.p, 0)
        WeeklyRequestItem.objects.create(request=w, kind="material", name="Beton",
                                         quantity=Decimal("30"), unit_price=Decimal("900000"), bolim="Poydevor")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("project_detail", args=[self.p.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Narx farqi")
        self.assertContains(r, "narx-yangilash")   # yangilash tugmasi (farq > 0)
        # 30×(900−800) = 3 mln sarflangan + 70×(900−800) = 7 mln kutilayotgan → +10 mln
        self.assertContains(r, "+10 000 000")
        self.assertContains(r, "narx o'zgardi")
        # Sirdaryo qolipi (o'qish jadvali — direktor ko'radi): 4 guruh sarlavhasi +
        # oxirgi hafta ustuni + blok yakuni + JAMI + eski narx ustidan chizilgan
        from .models import Firma, UserProfile
        f = Firma.objects.create(name="F")
        self.p.firma = f
        self.p.save(update_fields=["firma"])
        d = get_user_model().objects.create_user("dir", "d@d.uz", "pw")
        UserProfile.objects.update_or_create(user=d, defaults={"role": "director", "firma": f})
        self.client.force_login(d)
        r = self.client.get(reverse("project_detail", args=[self.p.id]))
        self.assertEqual(r.status_code, 200)
        for s in ("Bajarilgan ishlar", "Qolgan ishlar", "1-haftalik ish uchun material",
                  "Blok yakuni:", "JAMI:", "narx-eski"):
            self.assertContains(r, s)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("limit_export_obj", args=[self.p.id]))
        self.assertEqual(r.status_code, 200)
        # Tasdiqlar: direktor navbatidagi haftalikda narx farqi ko'rinadi (10 × 100 000)
        w2 = _hafta(self.p, 1, status="dir")
        WeeklyRequestItem.objects.create(request=w2, kind="material", name="Beton",
                                         quantity=Decimal("10"), unit_price=Decimal("900000"), bolim="Poydevor")
        r = self.client.get(reverse("tasdiqlar"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Narx farqi: +1 000 000")
        # Dashboard «Tasdiqlar» tabi (_tas_wk_card): qator chipi + so'rov jami
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "BOSHQA NARX +12.5%")
        self.assertContains(r, "Narx farqi: +1 000 000")
