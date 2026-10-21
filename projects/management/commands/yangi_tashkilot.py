"""YANGI TASHKILOT uchun bo'sh nusxani ishga tayyorlash (bitta buyruq).

Nima qiladi (tartib bilan):
  1. Muhitni tekshiradi: DEBUG, SECRET_KEY, ALLOWED_HOSTS, telegram.json (bot tokeni).
  2. Baza BO'SH bo'lsa jadvallarni yaratadi (migrate). Bazada obyekt/foydalanuvchi bo'lsa —
     TO'XTAYDI (mavjud tashkilot bazasini buzmaslik uchun); --majbur bilan davom etadi.
  3. Asosiy adminni yaratadi (login — settings.ASOSIY_ADMIN_LOGIN, odatda «admin»).
  4. Firma yaratadi (--firma).
  5. Statik fayllarni yig'adi (collectstatic).
  6. Keyingi qadamlarni chop etadi.

Ishlatish (yangi serverda, kod klon qilingan papkada):
  python manage.py yangi_tashkilot --tekshir                      # faqat muhit tekshiruvi
  python manage.py yangi_tashkilot --firma "Yangi Qurilish MChJ" --admin-parol "KuchliParol#2026"
"""
import os

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Yangi tashkilot uchun bo'sh nusxani sozlaydi: tekshiruv, migrate, admin, firma, collectstatic."

    def add_arguments(self, parser):
        parser.add_argument("--firma", default="", help="Tashkilot (firma) nomi")
        parser.add_argument("--admin-parol", default="", help="Asosiy admin paroli (kamida 8 belgi)")
        parser.add_argument("--tekshir", action="store_true", help="Faqat muhitni tekshirish, hech narsa o'zgartirmaslik")
        parser.add_argument("--majbur", action="store_true", help="Bazada ma'lumot bo'lsa ham davom etish")
        parser.add_argument("--statiksiz", action="store_true", help="collectstatic qilmaslik")

    # ---------- 1. muhit ----------
    def _tekshir(self):
        out, xato = self.stdout.write, []
        out("== 1. Muhit tekshiruvi ==")
        if settings.DEBUG:
            xato.append("DEBUG=True — serverda DEBUG=False bo'lishi shart (WSGI/env: DEBUG=False).")
        else:
            out("  [OK] DEBUG=False")
        sk = settings.SECRET_KEY or ""
        if len(sk) < 40 or "insecure" in sk or "o'zgartiring" in sk.lower():
            xato.append("SECRET_KEY zaif/sukut — kamida 50 belgili tasodifiy satr bering (env: SECRET_KEY).")
        else:
            out("  [OK] SECRET_KEY o'rnatilgan")
        hosts = [h for h in settings.ALLOWED_HOSTS if h not in ("127.0.0.1", "localhost")]
        if not hosts:
            xato.append("ALLOWED_HOSTS da sayt manzili yo'q (env: ALLOWED_HOSTS=firma.pythonanywhere.com).")
        else:
            out(f"  [OK] ALLOWED_HOSTS: {', '.join(hosts)}")
        tg = settings.BASE_DIR / "telegram.json"
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if token or tg.exists():
            out("  [OK] Telegram bot tokeni bor (telegram.json / env)")
        else:
            xato.append("Telegram bot tokeni yo'q — BotFather'dan yangi bot oling, telegram.json ga yozing "
                        "(2FA kirish va bildirishnomalar botsiz ishlamaydi).")
        for x in xato:
            out(self.style.ERROR("  [XATO] " + x))
        return not xato

    def handle(self, *args, **o):
        out = self.stdout.write
        muhit_ok = self._tekshir()
        if o["tekshir"]:
            out(self.style.SUCCESS("Muhit tayyor.") if muhit_ok else self.style.WARNING("Avval yuqoridagi xatolarni tuzating."))
            return
        if not muhit_ok and not o["majbur"]:
            raise CommandError("Muhit tayyor emas — xatolarni tuzating yoki --majbur bilan davom eting (lokal sinov uchun).")

        # ---------- 2. baza ----------
        out("== 2. Baza ==")
        from django.db import connection
        jadval_bor = "projects_project" in connection.introspection.table_names()
        if jadval_bor:
            from projects.models import Project
            from django.contrib.auth import get_user_model
            if (Project.objects.exists() or get_user_model().objects.filter(is_superuser=False).exists()) and not o["majbur"]:
                raise CommandError("Bazada obyektlar/foydalanuvchilar BOR — bu bo'sh nusxa emas. "
                                   "Yangi tashkilot uchun bo'sh db.sqlite3 bilan ishga tushiring (yoki --majbur).")
        call_command("migrate", interactive=False, verbosity=0)
        out("  [OK] jadvallar tayyor (migrate)")

        # ---------- 3. admin ----------
        out("== 3. Asosiy admin ==")
        from django.contrib.auth import get_user_model
        U = get_user_model()
        login = getattr(settings, "ASOSIY_ADMIN_LOGIN", "admin")
        parol = o["admin_parol"]
        u = U.objects.filter(username=login).first()
        if u is None:
            if len(parol) < 8:
                raise CommandError(f"Admin «{login}» yo'q — --admin-parol bering (kamida 8 belgi).")
            u = U.objects.create_superuser(username=login, password=parol, email="")
            out(f"  [OK] asosiy admin yaratildi: {login}")
        else:
            if parol:
                u.set_password(parol); u.is_superuser = u.is_staff = u.is_active = True; u.save()
                out(f"  [OK] admin «{login}» paroli yangilandi")
            else:
                out(f"  - admin «{login}» allaqachon bor")

        # ---------- 4. firma ----------
        out("== 4. Firma ==")
        if o["firma"].strip():
            from projects.models import Firma
            f, yangi = Firma.objects.get_or_create(name=o["firma"].strip())
            out(f"  [OK] firma {'yaratildi' if yangi else 'bor'}: {f.name}")
        else:
            out("  - firma berilmadi (--firma) — admin panelda qo'shiladi")

        # ---------- 5. statik ----------
        if not o["statiksiz"]:
            out("== 5. Statik fayllar ==")
            call_command("collectstatic", interactive=False, verbosity=0)
            out("  [OK] collectstatic")

        out(self.style.SUCCESS("\nTAYYOR. Keyingi qadamlar:"))
        out("  1) Web → Reload (PythonAnywhere) yoki gunicorn restart.")
        out("  2) Telegram webhook: python manage.py telegram_webhook")
        out(f"  3) Saytga «{login}» bilan kiring → Admin panel → Obyektlar, Foydalanuvchilar va lavozimlar.")
        out("  4) Har xodim botga /start → admin tasdiqlaydi → 2FA bilan kiradi.")
        out("  5) Kunlik arxiv admin chatiga o'zi keladi (telegram.json dagi chat_id).")
