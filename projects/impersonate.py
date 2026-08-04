"""Admin xodim sifatida kirishi (impersonatsiya) — jarayonlarni tekshirish uchun.

Maqsad: admin har bir xodimning (PTO, prorab, snabjeniye, direktor) vazifasi
to'g'ri ishlayotganini O'SHA XODIM KO'ZI bilan tekshiradi — parolini
bilmasdan va umumiy «master parol»siz (u xavfli bo'lardi).

Oqim:
  1) Superuser admin paneldagi foydalanuvchi ro'yxatida «Sifatida kirish»
     tugmasini bosadi -> sessiya o'sha xodimga almashadi
  2) Sayt tepasida sariq banner chiqadi: «Siz ... sifatida ko'rmoqdasiz»
  3) «Adminga qaytish» bosilganda o'z hisobiga qaytadi (parolsiz —
     sessiyada asl admin belgisi saqlanadi)
"""
from django.contrib import messages
from django.contrib.auth import get_user_model, login as dj_login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect

ASL_ADMIN_KALIT = "asl_admin_id"


@login_required
def sifatida_kirish(request, pk):
    """Superuser -> tanlangan xodim sifatida kirish."""
    if not request.user.is_superuser:
        raise PermissionDenied("Faqat asosiy admin.")
    U = get_user_model()
    maqsad = get_object_or_404(U, pk=pk, is_active=True)
    if maqsad.pk == request.user.pk:
        return redirect("/")
    admin_id = request.user.pk
    # login() boshqa user uchun sessiyani tozalaydi — belgini KEYIN yozamiz
    dj_login(request, maqsad, backend="django.contrib.auth.backends.ModelBackend")
    request.session[ASL_ADMIN_KALIT] = admin_id
    messages.info(request, f"Siz endi «{maqsad.username}» sifatida ko'rmoqdasiz. "
                           f"Tepadagi «Adminga qaytish» bilan qaytasiz.")
    return redirect("/")


@login_required
def adminga_qaytish(request):
    """Impersonatsiyadan o'z admin hisobiga qaytish."""
    admin_id = request.session.get(ASL_ADMIN_KALIT)
    if not admin_id:
        return redirect("/")
    U = get_user_model()
    admin = U.objects.filter(pk=admin_id, is_active=True, is_superuser=True).first()
    if admin is None:
        request.session.pop(ASL_ADMIN_KALIT, None)
        return redirect("/")
    dj_login(request, admin, backend="django.contrib.auth.backends.ModelBackend")
    request.session.pop(ASL_ADMIN_KALIT, None)
    messages.success(request, "O'z hisobingizga (admin) qaytdingiz.")
    return redirect("/admin/auth/user/")
