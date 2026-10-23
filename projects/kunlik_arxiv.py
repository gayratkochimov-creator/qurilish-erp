"""Kunlik AVTO arxiv — har kuni arxiv botga o'zi yuboradi.

PythonAnywhere bepul tarifida rejalashtirilgan vazifalar yo'q, shuning
uchun «birinchi tashrif» usuli: saytga KUNNING BIRINCHI so'rovi kelganda
fon oqimida (threadda) admin chatiga yuboriladi:
  1) Baza zaxirasi (db.sqlite3 nusxasi)
  2) Hisobotlar ZIP — har obyekt: UMUMIY LIMIT ICHI (tarkibi) +
     HAFTALIK SO'ROVLAR + ombor qoldig'i

Oxirgi yuborilgan sana BASE_DIR/.kunlik_arxiv.txt da saqlanadi.
Lokal (DEBUG) rejimda ishlamaydi — faqat serverda.
"""
import threading

from django.conf import settings
from django.utils import timezone

_BELGI = None  # jarayon ichida keshlangan oxirgi sana


def _fayl():
    return settings.BASE_DIR / ".kunlik_arxiv.txt"


def _oxirgi_sana():
    global _BELGI
    if _BELGI is not None:
        return _BELGI
    try:
        _BELGI = _fayl().read_text().strip()
    except OSError:
        _BELGI = ""
    return _BELGI


def _saqla(sana):
    global _BELGI
    _BELGI = sana
    try:
        _fayl().write_text(sana)
    except OSError:
        pass


def _hajm(yol):
    """Papka hajmi (bayt) — simlinklar sanalmaydi (PythonAnywhere umumiy paketlari)."""
    import os
    jami = 0
    for root, dirs, files in os.walk(yol):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for f in files:
            fp = os.path.join(root, f)
            try:
                if not os.path.islink(fp):
                    jami += os.path.getsize(fp)
            except OSError:
                pass
    return jami


def disk_holati():
    """Serverdagi joy: {'used_mb','quota_mb','pct','free_mb','matn','ogoh'} — kunlik xabar uchun
    (foydalanuvchi talabi, 2026-10-23). Kvota: env DISK_QUOTA_MB (sukut 512 — PythonAnywhere bepul)."""
    import os
    from pathlib import Path
    MB = 1024 * 1024
    quota = int(os.environ.get("DISK_QUOTA_MB") or getattr(settings, "DISK_QUOTA_MB", 512) or 512)
    base = Path(settings.BASE_DIR)
    home = Path.home()
    # Loyiha papkasi + foydalanuvchi keshlari (PythonAnywhere kvotasiga kiradi); butun home emas —
    # lokal kompyuterda home o'nlab GB bo'ladi
    used = _hajm(base)
    for q in (home / ".cache", home / ".local"):
        if q.exists() and not str(base).startswith(str(q)):
            used += _hajm(q)
    qism = []
    for nom, yol in (("baza", base / "db.sqlite3"), ("media", base / "media"), ("venv", base / "venv"),
                     ("git", base / ".git"), ("pip kesh", home / ".cache")):
        try:
            h = yol.stat().st_size if yol.is_file() else (_hajm(yol) if yol.exists() else 0)
        except OSError:
            h = 0
        if h >= MB:
            qism.append(f"{nom} {h / MB:.0f}")
    used_mb = used / MB
    pct = used_mb / quota * 100 if quota else 0
    free_mb = max(0.0, quota - used_mb)
    belgi = "🔴" if pct >= 90 else ("🟡" if pct >= 75 else "🟢")
    matn = (f"{belgi} Disk: {used_mb:.0f} / {quota} MB ({pct:.0f}%) · bo'sh {free_mb:.0f} MB"
            + (f"\n   ({', '.join(qism)} MB)" if qism else ""))
    return {"used_mb": used_mb, "quota_mb": quota, "pct": pct, "free_mb": free_mb, "matn": matn, "ogoh": pct >= 80}


def kunlik_yubor():
    """Fonda: baza zaxirasi + hisobotlar ZIP -> admin (arxiv) chatiga. Xabarda server diski ham."""
    import os

    from projects.auth2fa import _log_xato
    from projects.management.commands.telegram_backup import (
        _api, _sqlite_snapshot, _token_chat,
    )
    try:
        from projects.auth2fa import admin_chatlar
        token = _token_chat()[0]
        chatlar = admin_chatlar()
        if not (token and chatlar):
            return
        now = timezone.localtime().strftime("%d.%m.%Y %H:%M")
        stamp = timezone.localtime().strftime("%Y%m%d")
        try:
            disk = disk_holati()
        except Exception as e:
            _log_xato("kunlik_arxiv_disk", str(e))
            disk = {"matn": "💽 Disk: o'lchab bo'lmadi", "ogoh": False, "pct": 0, "free_mb": 0}

        # 1) Baza zaxirasi
        try:
            snap = _sqlite_snapshot(settings.DATABASES["default"]["NAME"])
            try:
                with open(snap, "rb") as f:
                    data = f.read()
                for chat in chatlar:
                    _api(token, "sendDocument",
                         fields={"chat_id": chat,
                                 "caption": f"🗄 Kunlik AVTO zaxira (baza)\n{now}"},
                         files={"document": (f"qurilish_erp_{stamp}.sqlite3", data)})
            finally:
                try:
                    os.remove(snap)
                except OSError:
                    pass
        except Exception as e:
            _log_xato("kunlik_arxiv_baza", str(e))

        # 2) Hisobotlar ZIP: har obyekt umumiy limiti ICHI bilan + haftaliklar + ombor
        try:
            from projects.views import build_hisobotlar_zip
            z = build_hisobotlar_zip()
            for chat in chatlar:
                _api(token, "sendDocument",
                     fields={"chat_id": chat,
                             "caption": (f"📚 Kunlik AVTO hisobot\n"
                                         f"Har obyekt: umumiy limit ICHI + haftalik so'rovlar + ombor qoldig'i\n{now}\n"
                                         f"{disk['matn']}")},
                     files={"document": (f"hisobotlar_{stamp}.zip", z)})
        except Exception as e:
            _log_xato("kunlik_arxiv_zip", str(e))
        # 3) Disk to'lib borayotgan bo'lsa — alohida ogohlantirish
        try:
            if disk.get("ogoh"):
                for chat in chatlar:
                    _api(token, "sendMessage",
                         fields={"chat_id": chat,
                                 "text": (f"⚠️ SERVER DISKI {disk['pct']:.0f}% to'ldi — bo'sh {disk['free_mb']:.0f} MB.\n"
                                          f"Tozalash: pip kesh, __pycache__, eski zip/log; yoki tarifni oshirish.\n{now}")})
        except Exception as e:
            _log_xato("kunlik_arxiv_disk_ogoh", str(e))
    except Exception as e:
        try:
            _log_xato("kunlik_arxiv", str(e))
        except Exception:
            pass


# Ish vaqti (08:00–19:00) ichida arxiv YIG'ILMAYDI — bitta worker sekinlashib qolmasin:
# birinchi tashrif ish vaqtida kelsa, fon oqimi 19:00 gacha kutib turadi (2026-10-13 — tezlik)
ISH_BOSHI, ISH_OXIRI = 8, 19
_KUTMOQDA = False


def _kutib_yubor(bugun):
    """19:00 gacha kutadi (har daqiqada tekshiradi), so'ng yuboradi. Worker qayta
    ishga tushsa — sana belgilanmagani uchun keyingi tashrif yana navbatga qo'yadi."""
    import time as _t
    global _KUTMOQDA
    try:
        while True:
            now = timezone.localtime()
            if now.strftime("%Y-%m-%d") != bugun:      # kun o'tib ketdi — bugungisi keyingi tashrifda
                return
            if not (ISH_BOSHI <= now.hour < ISH_OXIRI):
                break
            _t.sleep(60)
        if _oxirgi_sana() == bugun:                    # boshqa oqim allaqachon yubordi
            return
        _saqla(bugun)
        kunlik_yubor()
    finally:
        _KUTMOQDA = False


class KunlikArxivMiddleware:
    """Kunning birinchi so'rovida kunlik arxivni fon oqimida yuboradi — ish vaqtidan tashqarida."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _KUTMOQDA
        if not settings.DEBUG:  # faqat serverda (lokal sinovda emas)
            now = timezone.localtime()
            bugun = now.strftime("%Y-%m-%d")
            if _oxirgi_sana() != bugun and not _KUTMOQDA:
                if ISH_BOSHI <= now.hour < ISH_OXIRI:
                    _KUTMOQDA = True
                    threading.Thread(target=_kutib_yubor, args=(bugun,), daemon=True).start()
                else:
                    _saqla(bugun)  # poyga bo'lmasin — avval belgilab qo'yamiz
                    threading.Thread(target=kunlik_yubor, daemon=True).start()
        return self.get_response(request)
