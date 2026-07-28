"""db.sqlite3 zaxirasini Telegram botga yuboradi.

Sozlash (token/chat_id) — settings orqali (muhit o'zgaruvchisi yoki telegram.json):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Ishlatish:
  python manage.py telegram_backup           # bazani yuboradi
  python manage.py telegram_backup --test     # faqat test xabari (token/chat tekshirish)

Faqat standart kutubxona (urllib, sqlite3) — qo'shimcha paket kerak emas.
"""
import io
import mimetypes
import os
import sqlite3
import tempfile
import urllib.request
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


def _token_chat():
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = getattr(settings, "TELEGRAM_CHAT_ID", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
    # Ixtiyoriy: BASE_DIR/telegram.json dan o'qish
    if not (token and chat):
        import json
        cfg = settings.BASE_DIR / "telegram.json"
        if cfg.exists():
            try:
                d = json.loads(cfg.read_text(encoding="utf-8"))
                token = token or d.get("token", "")
                chat = chat or str(d.get("chat_id", ""))
            except Exception:
                pass
    return token.strip(), str(chat).strip()


def _api(token, method, fields=None, files=None):
    """Telegram Bot API'ga multipart POST (urllib bilan)."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    boundary = uuid.uuid4().hex
    body = io.BytesIO()

    def w(s):
        body.write(s.encode("utf-8") if isinstance(s, str) else s)

    for k, v in (fields or {}).items():
        w(f"--{boundary}\r\n")
        w(f'Content-Disposition: form-data; name="{k}"\r\n\r\n')
        w(f"{v}\r\n")
    for k, (fname, data) in (files or {}).items():
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        w(f"--{boundary}\r\n")
        w(f'Content-Disposition: form-data; name="{k}"; filename="{fname}"\r\n')
        w(f"Content-Type: {ctype}\r\n\r\n")
        w(data)
        w("\r\n")
    w(f"--{boundary}--\r\n")

    req = urllib.request.Request(
        url, data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8")


def _sqlite_snapshot(db_path):
    """Server ishlab turgan holatda ham izchil (consistent) zaxira olamiz."""
    tmp = os.path.join(tempfile.gettempdir(), f"qerp_backup_{uuid.uuid4().hex}.sqlite3")
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    return tmp


class Command(BaseCommand):
    help = "db.sqlite3 zaxirasini Telegram botga yuboradi"

    def add_arguments(self, parser):
        parser.add_argument("--test", action="store_true", help="Faqat test xabari yuboradi")
        parser.add_argument("--faqat-baza", action="store_true", dest="faqat_baza",
                            help="Faqat db.sqlite3 yuboradi (hisobotlar ZIP'siz)")

    def handle(self, *args, **opts):
        token, chat = _token_chat()
        if not token or not chat:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN va TELEGRAM_CHAT_ID sozlanmagan. "
                "telegram.json fayliga yoki muhit o'zgaruvchisiga kiriting."
            )

        now = timezone.localtime().strftime("%d.%m.%Y %H:%M")

        if opts["test"]:
            res = _api(token, "sendMessage", fields={
                "chat_id": chat,
                "text": f"✅ Qurilish ERP — test xabari\nVaqt: {now}\nZaxira boti ishlayapti.",
            })
            if '"ok":true' not in res:
                raise CommandError(f"Telegram xatosi: {res[:300]}")
            self.stdout.write(self.style.SUCCESS("Test xabari yuborildi ✓"))
            return

        db_path = settings.DATABASES["default"]["NAME"]
        snap = _sqlite_snapshot(db_path)
        try:
            size_mb = os.path.getsize(snap) / (1024 * 1024)
            with open(snap, "rb") as f:
                data = f.read()
            fname = f"qurilish_erp_{timezone.localtime().strftime('%Y%m%d_%H%M')}.sqlite3"
            res = _api(token, "sendDocument",
                       fields={"chat_id": chat,
                               "caption": f"🗄 Qurilish ERP zaxirasi\n{now} · {size_mb:.1f} MB"},
                       files={"document": (fname, data)})
            if '"ok":true' not in res:
                raise CommandError(f"Telegram xatosi: {res[:300]}")
            self.stdout.write(self.style.SUCCESS(f"Zaxira yuborildi ✓ ({fname}, {size_mb:.1f} MB)"))
        finally:
            try:
                os.remove(snap)
            except OSError:
                pass

        # Hisobotlar ZIP (Excel) — ixtiyoriy, xato bo'lsa zaxira baribir yuborilgan
        if not opts.get("faqat_baza"):
            try:
                from projects.views import build_hisobotlar_zip
                zdata = build_hisobotlar_zip()
                nb = len(zdata)
                zsize = f"{nb / (1024 * 1024):.1f} MB" if nb >= 1024 * 1024 else f"{nb / 1024:.0f} KB"
                zname = f"hisobotlar_{timezone.localtime().strftime('%Y%m%d_%H%M')}.zip"
                res = _api(token, "sendDocument",
                           fields={"chat_id": chat,
                                   "caption": f"📊 Qurilish ERP hisobotlari (ZIP)\n{now} · {zsize}"},
                           files={"document": (zname, zdata)})
                if '"ok":true' not in res:
                    raise CommandError(f"Telegram xatosi (ZIP): {res[:300]}")
                self.stdout.write(self.style.SUCCESS(f"Hisobotlar ZIP yuborildi ✓ ({zname}, {zsize})"))
            except CommandError:
                raise
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Hisobotlar ZIP yuborilmadi: {e}"))
