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


def kunlik_yubor():
    """Fonda: baza zaxirasi + hisobotlar ZIP -> admin (arxiv) chatiga."""
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
                                         f"Har obyekt: umumiy limit ICHI + haftalik so'rovlar + ombor qoldig'i\n{now}")},
                     files={"document": (f"hisobotlar_{stamp}.zip", z)})
        except Exception as e:
            _log_xato("kunlik_arxiv_zip", str(e))
    except Exception as e:
        try:
            _log_xato("kunlik_arxiv", str(e))
        except Exception:
            pass


class KunlikArxivMiddleware:
    """Kunning birinchi so'rovida kunlik arxivni fon oqimida yuboradi."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.DEBUG:  # faqat serverda (lokal sinovda emas)
            bugun = timezone.localtime().strftime("%Y-%m-%d")
            if _oxirgi_sana() != bugun:
                _saqla(bugun)  # poyga bo'lmasin — avval belgilab qo'yamiz
                threading.Thread(target=kunlik_yubor, daemon=True).start()
        return self.get_response(request)
