"""`manage.py yangi_tashkilot` — bo'sh nusxani sozlash buyrug'i testlari."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import Firma, Project


class YangiTashkilotTest(TestCase):
    def test_bosh_bazada_admin_va_firma(self):
        out = StringIO()
        # test muhitida DEBUG=True bo'lgani uchun --majbur (lokal sinov rejimi)
        call_command("yangi_tashkilot", firma="Sinov MChJ", admin_parol="KuchliParol#1", majbur=True,
                     statiksiz=True, stdout=out)
        U = get_user_model()
        adm = U.objects.get(username="admin")
        self.assertTrue(adm.is_superuser and adm.check_password("KuchliParol#1"))
        self.assertTrue(Firma.objects.filter(name="Sinov MChJ").exists())
        self.assertIn("TAYYOR", out.getvalue())

    def test_malumotli_bazada_toxtaydi(self):
        f = Firma.objects.create(name="Bor Firma")
        Project.objects.create(code="X-1", name="Bor obyekt", firma=f)
        with self.assertRaises(CommandError):
            call_command("yangi_tashkilot", admin_parol="KuchliParol#1", majbur=False, statiksiz=True, stdout=StringIO())

    def test_parolsiz_admin_yaratilmaydi(self):
        with self.assertRaises(CommandError):
            call_command("yangi_tashkilot", majbur=True, statiksiz=True, stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(username="admin").exists())

    def test_tekshir_hech_narsa_ozgartirmaydi(self):
        out = StringIO()
        call_command("yangi_tashkilot", tekshir=True, stdout=out)
        self.assertFalse(get_user_model().objects.filter(username="admin").exists())
        self.assertIn("Muhit", out.getvalue())
