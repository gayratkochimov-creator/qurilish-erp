"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from projects import auth2fa, impersonate

urlpatterns = [
    # Django admin'ning o'z login sahifasi 2FA'ni chetlab o'tmasin — umumiy loginga
    path('admin/login/', RedirectView.as_view(url='/login/?next=/admin/', permanent=False)),
    path('admin/', admin.site.urls),
    # Admin xodim sifatida kirib jarayonni tekshiradi (parolsiz, faqat superuser)
    path('sifatida-kirish/<int:pk>/', impersonate.sifatida_kirish, name='sifatida_kirish'),
    path('adminga-qaytish/', impersonate.adminga_qaytish, name='adminga_qaytish'),
    # Kirish — Telegram 2FA bilan (profilda chat_id bo'lsa kod so'raladi)
    path('login/', auth2fa.kirish, name='login'),
    path('login/kod/', auth2fa.kirish_kod, name='login_kod'),
    path('telegram/hook/<str:secret>/', auth2fa.telegram_hook, name='telegram_hook'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('ombor/', include('ombor.urls')),
    path('', include('projects.urls')),
]

# /media/ (chek, nakladnoy rasmlari) — faqat tizimga kirgan va shu omborni ko'ra oladiganlarga.
# Serverda Web → Static files ro'yxatida /media/ turgan bo'lsa, bu yo'l ishlamaydi (fayl ochiq
# beriladi) — himoya uchun o'sha qatorni o'chirish kerak.
from django.urls import re_path  # noqa: E402
from ombor.media import media_korish  # noqa: E402

urlpatterns += [re_path(r"^media/(?P<path>.+)$", media_korish, name="media_korish")]
