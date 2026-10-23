"""Serverdagi joy holati (kunlik Telegram xabaridagi bilan bir xil). Hech narsa o'zgartirmaydi.
  python manage.py disk_holati
"""
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Server diski: band/bo'sh (MB, %), katta papkalar."

    def handle(self, *args, **o):
        from projects.kunlik_arxiv import disk_holati
        d = disk_holati()
        matn = d["matn"]
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            matn.encode(enc)
        except UnicodeEncodeError:   # Windows konsoli (cp1251) — emoji'siz
            matn = matn.encode(enc, "replace").decode(enc)
        self.stdout.write(matn)
        if d["ogoh"]:
            self.stdout.write(self.style.WARNING("DIQQAT: disk 80% dan oshdi — tozalang yoki tarifni oshiring."))
