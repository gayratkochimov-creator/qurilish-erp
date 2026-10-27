class NoHTMLCacheMiddleware:
    """HTML sahifalarni brauzer keshlamasin — o'zgarishlar darhol ko'rinsin.
    (Statik fayllar — CSS/JS/shrift — keshlanaveradi.)"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        ct = response.get("Content-Type", "")
        if ct.startswith("text/html"):
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        return response


class TelegramMajburiyMiddleware:
    """Telegram botga ULANMAGAN (UserProfile.telegram_chat_id bo'sh) foydalanuvchi tizimda qola olmaydi
    (2026-10-27): eski sessiya, parol almashtirish yoki admin ulanishni uzishi — hammasida chiqarib
    yuboriladi va botda ro'yxatdan o'tish yo'riqnomasi chiqadi.
    Istisno: admin «boshqa nomdan ko'rish» (impersonatsiya) paytida; login/logout/webhook sahifalari."""

    MUSTASNO = ("/login/", "/logout/", "/telegram/hook/", "/static/", "/media/")
    TEKSHIRUV_ORALIGI = 300     # soniya — profilga har so'rovda emas, 5 daqiqada bir murojaat

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        u = getattr(request, "user", None)
        if (u is not None and u.is_authenticated and getattr(settings, "TELEGRAM_2FA_MAJBURIY", True)
                and not request.path.startswith(self.MUSTASNO)
                and not request.session.get("asl_admin_id")):
            import time
            hozir = time.time()
            if hozir - request.session.get("tg_tekshirildi", 0) > self.TEKSHIRUV_ORALIGI:
                from projects.models import UserProfile
                chat = (UserProfile.objects.filter(user_id=u.pk)
                        .values_list("telegram_chat_id", flat=True).first() or "").strip()
                if chat:
                    request.session["tg_tekshirildi"] = hozir
                else:
                    from django.contrib import messages
                    from django.contrib.auth import logout
                    from django.shortcuts import redirect
                    logout(request)
                    messages.error(request, "Tizimga faqat Telegram orqali kod bilan kiriladi. "
                                            "Avval botda ro'yxatdan o'ting (/start), keyin qayta kiring.")
                    return redirect("/login/")
        return self.get_response(request)
