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


def _disk_usage(quota_mb):
    """Home papkadagi jami hajm (MB) va foizini qaytaradi (hech narsa o'chirmaydi).
       PythonAnywhere bepul kvota 512 MB — quota_mb shu bilan taqqoslaydi."""
    home = os.path.expanduser("~")
    total = 0
    for root, _dirs, files in os.walk(home):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    used_mb = total / (1024 * 1024)
    pct = (used_mb / quota_mb * 100) if quota_mb else 0
    return used_mb, pct


class Command(BaseCommand):
    help = "db.sqlite3 zaxirasini Telegram botga yuboradi"

    def add_arguments(self, parser):
        parser.add_argument("--test", action="store_true", help="Faqat test xabari yuboradi")
        parser.add_argument("--faqat-baza", action="store_true", dest="faqat_baza",
                            help="Faqat db.sqlite3 yuboradi (hisobotlar ZIP'siz)")
        parser.add_argument("--disk-quota-mb", type=int, default=512, dest="disk_quota_mb",
                            help="Disk kvotasi (MB), foiz shu bilan hisoblanadi. Default 512 (PythonAnywhere bepul)")
        parser.add_argument("--disk-warn", type=int, default=90, dest="disk_warn",
                            help="Necha foizda ogohlantirish yuborilsin. Default 90")

    def handle(self, *args, **opts):
        token, chat = _token_chat()
        if not token or not chat:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN va TELEGRAM_CHAT_ID sozlanmagan. "
                "telegram.json fayliga yoki muhit o'zgaruvchisiga kiriting."
            )

        now = timezone.localtime().strftime("%d.%m.%Y %H:%M")

        # Disk holati (hech narsa o'chirilmaydi — faqat hisoblash + ogohlantirish)
        quota_mb = opts.get("disk_quota_mb") or 512
        warn_pct = opts.get("disk_warn") or 90
        try:
            used_mb, disk_pct = _disk_usage(quota_mb)
            disk_str = f"💽 Disk: {used_mb:.0f}/{quota_mb} MB ({disk_pct:.0f}%)"
        except Exception:
            used_mb, disk_pct, disk_str = 0, 0, "💽 Disk: —"

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
                               "caption": f"🗄 Qurilish ERP zaxirasi\n{now} · {size_mb:.1f} MB\n{disk_str}"},
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

        # Disk 90% (yoki belgilangan chegara) ga yetsa — ogohlantirish (hech narsa o'chirilmaydi)
        if disk_pct >= warn_pct:
            try:
                _api(token, "sendMessage", fields={
                    "chat_id": chat,
                    "text": (f"⚠️ DIQQAT — Disk {disk_pct:.0f}% to'ldi ({used_mb:.0f}/{quota_mb} MB)\n"
                             f"Zaxira yuqorida saqlandi. Joy bo'shatish kerak — eski rasm/log/backup fayllarni "
                             f"tekshiring. Biznes ma'lumot (obyekt/limit) O'CHIRILMADI."),
                })
                self.stdout.write(self.style.WARNING(f"Disk {disk_pct:.0f}% — ogohlantirish yuborildi"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Ogohlantirish yuborilmadi: {e}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Disk holati: {disk_pct:.0f}% (chegara {warn_pct}%)"))
