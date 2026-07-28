"""Telegram chat_id'ni topish yordamchisi.

1. Botni Telegramda oching, unga biror xabar yozing (masalan «salom»).
   (Kanal bo'lsa — botni kanalga admin qilib qo'shing va biror post yuboring.)
2. python manage.py telegram_chatid
   — bot ko'rgan chatlarning id'sini chiqaradi.

telegram.json faqat token bilan ham ishlaydi (chat_id shu buyruq orqali topiladi).
"""
import json
import os
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _token():
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        cfg = settings.BASE_DIR / "telegram.json"
        if cfg.exists():
            try:
                token = json.loads(cfg.read_text(encoding="utf-8")).get("token", "")
            except Exception:
                pass
    return token.strip()


class Command(BaseCommand):
    help = "Bot ko'rgan chatlarning chat_id'sini chiqaradi (getUpdates)"

    def handle(self, *args, **opts):
        token = _token()
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN sozlanmagan (telegram.json yoki muhit o'zgaruvchisi).")
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok"):
            raise CommandError(f"Telegram xatosi: {data}")
        seen = {}
        for upd in data.get("result", []):
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            if chat.get("id") is not None:
                seen[chat["id"]] = (chat.get("title") or chat.get("username")
                                    or chat.get("first_name") or chat.get("type"))
        if not seen:
            self.stdout.write(self.style.WARNING(
                "Hech qanday chat topilmadi. Avval botga Telegramda biror xabar yozing, so'ng qayta urinib ko'ring."))
            return
        self.stdout.write(self.style.SUCCESS("Topilgan chatlar:"))
        for cid, nom in seen.items():
            self.stdout.write(f"  chat_id = {cid}   ({nom})")
        self.stdout.write("\nShu chat_id'ni telegram.json fayliga yozing.")
