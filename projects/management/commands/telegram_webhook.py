"""Telegram bot webhook'ini sozlash (2FA bog'lash suhbati uchun).

Ishlatish:
  python manage.py telegram_webhook --set https://qurilisherp.pythonanywhere.com
      -> webhook o'rnatiladi (bot xabarlari saytga kela boshlaydi)
  python manage.py telegram_webhook
      -> joriy holatni ko'rsatadi (getWebhookInfo)
  python manage.py telegram_webhook --off
      -> webhook o'chiriladi
"""
import json
import urllib.parse
import urllib.request

from django.core.management.base import BaseCommand, CommandError

from projects.auth2fa import hook_secret, _token


def _get(token, method, params=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


class Command(BaseCommand):
    help = "Telegram webhook'ini o'rnatish/ko'rish/o'chirish (2FA bot uchun)"

    def add_arguments(self, parser):
        parser.add_argument("--set", dest="baza_url", default="",
                            help="Sayt manzili, masalan https://qurilisherp.pythonanywhere.com")
        parser.add_argument("--off", action="store_true", help="Webhook'ni o'chirish")

    def handle(self, *args, **opts):
        token = _token()
        if not token:
            raise CommandError("telegram.json topilmadi yoki token yo'q.")
        secret = hook_secret()

        if opts["off"]:
            res = _get(token, "deleteWebhook")
            self.stdout.write(self.style.SUCCESS(f"Webhook o'chirildi: {res.get('ok')}"))
            return

        if opts["baza_url"]:
            baza = opts["baza_url"].rstrip("/")
            url = f"{baza}/telegram/hook/{secret}/"
            res = _get(token, "setWebhook", {
                "url": url,
                "secret_token": secret,
                "allowed_updates": '["message"]',
            })
            if not res.get("ok"):
                raise CommandError(f"Xato: {res}")
            self.stdout.write(self.style.SUCCESS(f"Webhook o'rnatildi ✓\n{url}"))
            return

        info = _get(token, "getWebhookInfo").get("result", {})
        self.stdout.write(f"URL: {info.get('url') or '(o`rnatilmagan)'}")
        self.stdout.write(f"Kutilayotgan xabarlar: {info.get('pending_update_count', 0)}")
        if info.get("last_error_message"):
            self.stdout.write(self.style.WARNING(f"Oxirgi xato: {info['last_error_message']}"))
