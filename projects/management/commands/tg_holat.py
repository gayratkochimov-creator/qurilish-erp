"""Telegram botga ulanmagan FAOL foydalanuvchilar (faqat o'qiydi, hech narsa o'zgartirmaydi).
Telegram majburiy bo'lgani uchun ular saytga kira olmaydi — deploydan oldin shuni ko'ring:
  python manage.py tg_holat
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Botga ulanmagan faol foydalanuvchilar ro'yxati (ular kira olmaydi)."

    def handle(self, *args, **o):
        U = get_user_model()
        ulangan = bosh = 0
        for u in U.objects.filter(is_active=True).select_related("profile").order_by("username"):
            chat = (getattr(getattr(u, "profile", None), "telegram_chat_id", "") or "").strip()
            if chat:
                ulangan += 1
            else:
                bosh += 1
                rol = "ADMIN" if u.is_superuser else (getattr(getattr(u, "profile", None), "role", "") or "-")
                self.stdout.write(f"  ULANMAGAN: {u.username} ({rol})")
        self.stdout.write(f"Botga ulangan: {ulangan} | ulanmagan (kira olmaydi): {bosh}")
