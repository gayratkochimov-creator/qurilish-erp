"""Serverdagi joy holati (kunlik Telegram xabaridagi bilan bir xil). Hech narsa o'zgartirmaydi.
  python manage.py disk_holati
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Server diski: band/bo'sh (MB, %), katta papkalar."

    def handle(self, *args, **o):
        from projects.kunlik_arxiv import disk_holati
        d = disk_holati()
        self.stdout.write(d["matn"].replace("\\n", "\n"))
        if d["ogoh"]:
            self.stdout.write(self.style.WARNING("DIQQAT: disk 80% dan oshdi — tozalang yoki tarifni oshiring."))
