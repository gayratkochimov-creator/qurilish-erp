"""Telegram orqali 2 bosqichli kirish (2FA) va bot webhook.

Kirish oqimi:
  1) Foydalanuvchi login + parol kiritadi (login sahifasi)
  2) Profilida telegram_chat_id BO'LSA — 6 xonali kod Telegramga yuboriladi
     va /login/kod/ sahifasida so'raladi. BO'LMASA — oddiy kirish.
  3) Kod to'g'ri (3 daqiqa ichida, 5 urinishgacha) bo'lsa — tizimga kiradi.

Botda ro'yxatdan o'tish (bog'lash):
  /start -> bot LOGIN so'raydi -> PAROL so'raydi -> tekshiradi ->
  chat_id profilga yoziladi. Parol xabari darhol o'chiriladi.

MUHIM: baza/ZIP zaxiralari FAQAT telegram.json'dagi admin chatiga boradi
(telegram_backup buyrug'i). Xodimlar botdan faqat tasdiqlash kodi oladi —
bot ularga hech qanday hujjat yubormaydi.
"""
import hashlib
import json
import secrets
import urllib.error
import urllib.request

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as dj_login
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt

from .management.commands.telegram_backup import _token_chat

SESSIYA_KALIT = "tg2fa"
KOD_MUDDAT = 180        # soniya — kod 3 daqiqa amal qiladi
MAX_URINISH = 5         # 5 marta noto'g'ri kod -> qaytadan login
QAYTA_YUBORISH = 60     # soniya — qayta yuborish oralig'i
BIND_BLOK_DAQIQA = 10   # botda 5 marta xato parol -> 10 daqiqa blok


# ---------------------------------------------------------------- Telegram API

def _token():
    return _token_chat()[0]


def hook_secret():
    """Webhook URL'idagi maxfiy bo'lak — tokendan hosil qilinadi (sozlash shart emas)."""
    t = _token()
    return hashlib.sha256(("qerp-hook:" + t).encode()).hexdigest()[:40] if t else ""


def admin_chatlar():
    """BARCHA admin chatlari:
    - telegram.json'dagi asosiy (zaxira) chat
    - botga bog'langan HAR BIR superuser chati
    Yangi admin qo'shilsa (superuser + botga bog'lansa) — avtomatik shu
    ro'yxatga tushadi: bog'lanish so'rovlari, kunlik zaxira/arxiv unga ham boradi."""
    chats = set()
    a = str(_token_chat()[1] or "").strip()
    if a:
        chats.add(a)
    try:
        from .models import UserProfile
        for cid in (UserProfile.objects
                    .filter(user__is_superuser=True, user__is_active=True)
                    .exclude(telegram_chat_id="")
                    .values_list("telegram_chat_id", flat=True)):
            chats.add(str(cid))
    except Exception:
        pass
    return chats


def _log_xato(method, izoh):
    """Telegram API xatolarini faylga yozish — serverda muammoni topish oson bo'lsin.
    (Parol yoki kod matni YOZILMAYDI — faqat metod, chat va xato.)"""
    try:
        from django.conf import settings
        with open(settings.BASE_DIR / "telegram_2fa.log", "a", encoding="utf-8") as f:
            f.write(f"{timezone.now():%d.%m.%Y %H:%M:%S} | {method} | {izoh}\n")
    except Exception:
        pass


def _api_json(method, payload, timeout=15, urinish=3):
    """Telegram API chaqiruvi — PythonAnywhere proksisi ba'zan 503 berib
    uzilib turadi, shuning uchun XATODA AVTOMATIK QAYTA URINADI (3 marta)."""
    import time as _time

    t = _token()
    if not t:
        _log_xato(method, "token sozlanmagan")
        return False
    oxirgi_xato = ""
    for k in range(urinish):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{t}/{method}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
                if '"ok":true' in body:
                    return True
                # API rad etdi (masalan chat topilmadi) — qayta urinish foydasiz
                oxirgi_xato = f"javob={body[:200]}"
                break
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                body = str(e)
            oxirgi_xato = f"HTTP {e.code}: {body}"
            if e.code < 500:
                break               # 4xx — qayta urinish foydasiz
        except Exception as e:
            oxirgi_xato = f"xato: {e}"   # proksi/tarmoq uzilishi — qayta urinamiz
        if k < urinish - 1:
            _time.sleep(2)
    _log_xato(method, f"chat={payload.get('chat_id')} {oxirgi_xato} ({urinish} urinishdan keyin)")
    return False


def tg_send(chat_id, text):
    """Matnli xabar. Muvaffaqiyat bo'lsa True."""
    return _api_json("sendMessage", {"chat_id": chat_id, "text": text})


def tg_send_kb(chat_id, text, keyboard):
    """Inline tugmali xabar (admin tasdiqlash/rad etish uchun)."""
    return _api_json("sendMessage", {
        "chat_id": chat_id, "text": text,
        "reply_markup": {"inline_keyboard": keyboard},
    })


def tg_edit(chat_id, message_id, text):
    """Xabar matnini yangilash (tugmalarni olib tashlab natijani yozish)."""
    return _api_json("editMessageText",
                     {"chat_id": chat_id, "message_id": message_id, "text": text})


def tg_answer_cb(cb_id, text=""):
    """Callback tugma bosilganiga javob (soatcha belgisini o'chiradi)."""
    return _api_json("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})


def tg_delete(chat_id, message_id):
    """Xabarni o'chirish (parol xabari uchun) — xato bo'lsa jim."""
    return _api_json("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


# ---------------------------------------------------------------- Kirish (2FA)

def _rol_nomi(user):
    if user.is_superuser:
        return "Admin"
    prof = getattr(user, "profile", None)
    return (prof.get_role_display() if prof and prof.role else "") or "—"


def _next_sahifa(request):
    n = request.POST.get("next") or request.GET.get("next") or ""
    if n and url_has_allowed_host_and_scheme(n, allowed_hosts={request.get_host()}):
        return n
    return "/"


def kirish(request):
    """Login sahifasi. Parol to'g'ri + Telegram bog'langan -> kod bosqichiga."""
    if request.user.is_authenticated:
        return redirect("/")
    xato = ""
    if request.method == "POST":
        user = authenticate(
            request,
            username=(request.POST.get("username") or "").strip(),
            password=request.POST.get("password") or "",
        )
        if user is None:
            xato = "Login yoki parol noto'g'ri. Qaytadan urinib ko'ring."
        else:
            prof = getattr(user, "profile", None)
            chat = (prof.telegram_chat_id or "").strip() if prof else ""
            if not chat:
                # Telegram bog'lanmagan — oddiy kirish (qulflanib qolmasin)
                dj_login(request, user)
                return redirect(_next_sahifa(request))
            kod = f"{secrets.randbelow(1000000):06d}"
            yubordi = tg_send(
                chat,
                f"🔐 Qurilish ERP — kirish kodi: {kod}\n"
                f"Amal qilish muddati: 3 daqiqa.\n"
                f"Agar bu siz bo'lmasangiz — parolingizni o'zgartiring!",
            )
            if not yubordi:
                xato = ("Telegramga kod yuborilmadi (bot yoki internet muammosi). "
                        "Qayta urinib ko'ring yoki admin bilan bog'laning.")
            else:
                request.session[SESSIYA_KALIT] = {
                    "uid": user.pk,
                    "kod": kod,
                    "muddat": timezone.now().timestamp() + KOD_MUDDAT,
                    "urinish": 0,
                    "sent": timezone.now().timestamp(),
                    "next": _next_sahifa(request),
                }
                return redirect("login_kod")
    return render(request, "registration/login.html", {"xato": xato})


def kirish_kod(request):
    """2-bosqich: Telegramga borgan 6 xonali kodni tekshirish."""
    s = request.session.get(SESSIYA_KALIT)
    if not s:
        return redirect("login")
    U = get_user_model()
    user = U.objects.filter(pk=s.get("uid"), is_active=True).first()
    if user is None:
        request.session.pop(SESSIYA_KALIT, None)
        return redirect("login")

    xato, info = "", ""
    now = timezone.now().timestamp()

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            if now - s.get("sent", 0) < QAYTA_YUBORISH:
                xato = f"Kod yaqinda yuborilgan — {QAYTA_YUBORISH} soniyadan keyin qayta so'rang."
            else:
                prof = getattr(user, "profile", None)
                chat = (prof.telegram_chat_id or "").strip() if prof else ""
                kod = f"{secrets.randbelow(1000000):06d}"
                if chat and tg_send(chat, f"🔐 Qurilish ERP — yangi kirish kodi: {kod}\nAmal qilish muddati: 3 daqiqa."):
                    s.update({"kod": kod, "muddat": now + KOD_MUDDAT, "urinish": 0, "sent": now})
                    request.session[SESSIYA_KALIT] = s
                    info = "Yangi kod yuborildi ✓"
                else:
                    xato = "Kod yuborilmadi — qayta urinib ko'ring."
        else:
            kiritilgan = (request.POST.get("kod") or "").strip()
            if now > s.get("muddat", 0):
                request.session.pop(SESSIYA_KALIT, None)
                return render(request, "registration/login_kod.html",
                              {"muddati_otdi": True, "xato": "", "info": ""})
            if s.get("urinish", 0) >= MAX_URINISH:
                request.session.pop(SESSIYA_KALIT, None)
                return redirect("login")
            if kiritilgan and secrets.compare_digest(kiritilgan, s.get("kod", "")):
                request.session.pop(SESSIYA_KALIT, None)
                dj_login(request, user)
                return redirect(s.get("next") or "/")
            s["urinish"] = s.get("urinish", 0) + 1
            request.session[SESSIYA_KALIT] = s
            qoldi = MAX_URINISH - s["urinish"]
            if qoldi <= 0:
                request.session.pop(SESSIYA_KALIT, None)
                return redirect("login")
            xato = f"Kod noto'g'ri. Yana {qoldi} ta urinish qoldi."

    return render(request, "registration/login_kod.html",
                  {"xato": xato, "info": info, "muddati_otdi": False})


# ---------------------------------------------------------------- Bot webhook

BOT_START = ("Assalomu alaykum! 👋 Qurilish ERP tasdiqlash boti.\n\n"
             "Ro'yxatdan o'tish uchun tizimdagi LOGINingizni yuboring.")
BOT_PAROL = ("Endi PAROLingizni yuboring.\n"
             "(Xavfsizlik uchun parol xabaringiz darhol o'chirib tashlanadi.)")
BOT_XATO = "❌ Login yoki parol noto'g'ri.\nQaytadan boshlash: /start"
BOT_BLOK = f"⛔ Juda ko'p xato urinish. {BIND_BLOK_DAQIQA} daqiqadan keyin /start bosing."
BOT_BOR = ("Siz ro'yxatdan o'tgansiz ✅\nKod saytga kirish paytida keladi.\n"
           "📦 Ombor (ostatka/prixod/rasxod): /ombor\nQayta bog'lash: /start")
BOT_YOQ = "Ro'yxatdan o'tish uchun /start bosing."
BOT_KUTISH = ("⏳ So'rovingiz ADMIN tasdig'iga yuborildi.\n"
              "Admin tasdiqlagach shu yerda xabar olasiz — shundan keyin "
              "saytga kirishda kod kela boshlaydi.")
BOT_ADMIN_YOQ = "❌ Admin chati sozlanmagan — admin bilan bog'laning."
BOT_TASDIQ = ("✅ Admin tasdiqladi!\nEndi saytga kirishda 6 xonali kod shu yerga keladi.\n"
              "📦 Ombor ma'lumotlari (ostatka/prixod/rasxod): /ombor")
BOT_RAD = "❌ Admin so'rovingizni rad etdi.\nAdmin bilan bog'laning."


@csrf_exempt
def telegram_hook(request, secret):
    """Telegram webhook — bot suhbati (faqat bog'lash uchun, hech qanday hujjat yubormaydi)."""
    if request.method != "POST" or not secret or secret != hook_secret():
        return HttpResponseForbidden()
    # Qo'shimcha himoya: setWebhook'da berilgan secret_token sarlavhasi
    hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if hdr and hdr != hook_secret():
        return HttpResponseForbidden()
    try:
        update = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponse("ok")

    # --- Tugmalar (callback_query): ombor menyusi yoki admin tasdig'i ---
    cb = update.get("callback_query")
    if cb:
        data = cb.get("data") or ""
        if data[:3] in ("fi:", "ob:", "os:", "pr:", "rx:"):
            from ombor.telegram_arxiv import ombor_callback
            cb_chat = str((((cb.get("message") or {}).get("chat")) or {}).get("id") or "")
            ombor_callback(data, cb_chat, cb.get("id") or "")
        else:
            _bind_callback(cb)
        return HttpResponse("ok")

    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private":
        return HttpResponse("ok")   # guruh/kanal xabarlariga aralashmaymiz
    chat_id = str(chat.get("id") or "")
    text = (msg.get("text") or "").strip()
    message_id = msg.get("message_id")
    if not chat_id or not text:
        return HttpResponse("ok")

    from .models import TelegramBindState, UserProfile

    # --- ADMIN matn buyruqlari: /tasdiq_<id> yoki /rad_<id> (tugma o'rniga) ---
    _adminlar = admin_chatlar()
    if chat_id in _adminlar and (
            text.startswith("/tasdiq_") or text.startswith("/rad_")):
        try:
            sid = int(text.split("_", 1)[1])
        except (ValueError, IndexError):
            tg_send(chat_id, "Buyruq formati: /tasdiq_5 yoki /rad_5")
            return HttpResponse("ok")
        natija = _bind_qaror(sid, tasdiq=text.startswith("/tasdiq_"))
        tg_send(chat_id, natija)
        return HttpResponse("ok")

    # --- Ombor menyusi: /ombor (snabjeniye o'z obyektlarini, admin hammasini) ---
    if text.lower() in ("/ombor", "/ostatka"):
        from ombor.telegram_arxiv import ombor_komanda
        ombor_komanda(chat_id)
        return HttpResponse("ok")

    state, _ = TelegramBindState.objects.get_or_create(chat_id=chat_id)

    # Blok tekshiruvi
    if state.blocked_until and timezone.now() < state.blocked_until:
        tg_send(chat_id, BOT_BLOK)
        return HttpResponse("ok")

    if text == "/start":
        state.step, state.login_tmp = "login", ""
        state.save(update_fields=["step", "login_tmp", "updated_at"])
        tg_send(chat_id, BOT_START)
        return HttpResponse("ok")

    if state.step == "login":
        state.login_tmp = text[:150]
        state.step = "parol"
        state.save(update_fields=["step", "login_tmp", "updated_at"])
        tg_send(chat_id, BOT_PAROL)
        return HttpResponse("ok")

    if state.step == "parol":
        # Parol xabarini darhol o'chiramiz — chat tarixida qolmasin
        if message_id:
            tg_delete(chat_id, message_id)
        user = authenticate(username=state.login_tmp, password=text)
        state.step, state.login_tmp = "", ""
        if user is None:
            state.fails += 1
            if state.fails >= 5:
                state.blocked_until = timezone.now() + timezone.timedelta(minutes=BIND_BLOK_DAQIQA)
                state.fails = 0
                state.save()
                tg_send(chat_id, BOT_BLOK)
            else:
                state.save()
                tg_send(chat_id, BOT_XATO)
            return HttpResponse("ok")
        # Login/parol TO'G'RI — lekin darhol bog'lamaymiz: ADMIN tasdig'i kerak
        _adminlar2 = admin_chatlar()
        if not _adminlar2:
            state.pending_user = None
            state.save()
            tg_send(chat_id, BOT_ADMIN_YOQ)
            return HttpResponse("ok")
        state.fails = 0
        state.pending_user = user
        state.save()
        tg_send(chat_id, BOT_KUTISH)
        kimdan = msg.get("from") or {}
        ism = " ".join(x for x in [kimdan.get("first_name", ""), kimdan.get("last_name", "")] if x).strip()
        nik = ("@" + kimdan["username"]) if kimdan.get("username") else ""
        matn = (f"🔔 2FA bog'lanish so'rovi\n"
                f"👤 Tizimda: {user.username} — {_rol_nomi(user)}\n"
                f"💬 Telegram: {ism} {nik} (chat {chat_id})\n\n"
                f"Shu odamga kirish kodlari yuborilishini tasdiqlaysizmi?\n"
                f"Yoki yozing: /tasdiq_{state.id} yoki /rad_{state.id}")
        for _ac in _adminlar2:
            bordi = tg_send_kb(
                _ac, matn,
                [[{"text": "✅ Tasdiqlash", "callback_data": f"b1:{state.id}"},
                  {"text": "❌ Rad etish", "callback_data": f"b0:{state.id}"}]],
            )
            if not bordi:
                # Tugmali xabar o'tmasa — oddiy matn bilan (buyruqlar ichida bor)
                tg_send(_ac, matn)
        return HttpResponse("ok")

    # Suhbatdan tashqari istalgan matn
    if state.pending_user_id:
        tg_send(chat_id, BOT_KUTISH)
        return HttpResponse("ok")
    bogliq = UserProfile.objects.filter(telegram_chat_id=chat_id).exists()
    tg_send(chat_id, BOT_BOR if bogliq else BOT_YOQ)
    return HttpResponse("ok")


def _bind_qaror(state_id, tasdiq):
    """Bog'lanish so'rovi bo'yicha qaror (tugma yoki matn buyrug'idan chaqiriladi).
    Natija matnini qaytaradi (admin xabari uchun)."""
    from .models import TelegramBindState, UserProfile

    state = TelegramBindState.objects.filter(id=state_id).select_related("pending_user").first()
    if state is None or state.pending_user is None:
        return "⚠️ Bu so'rov topilmadi yoki allaqachon ko'rib chiqilgan."
    user = state.pending_user
    if tasdiq:
        prof, _ = UserProfile.objects.get_or_create(user=user)
        prof.telegram_chat_id = state.chat_id
        prof.save(update_fields=["telegram_chat_id"])
        state.pending_user = None
        state.save(update_fields=["pending_user", "updated_at"])
        tg_send(state.chat_id, BOT_TASDIQ)
        return (f"✅ TASDIQLANDI\n👤 {user.username} — {_rol_nomi(user)}\n"
                f"Endi kirish kodlari shu foydalanuvchining Telegramiga boradi.")
    chat = state.chat_id
    state.pending_user = None
    state.save(update_fields=["pending_user", "updated_at"])
    tg_send(chat, BOT_RAD)
    return f"❌ RAD ETILDI\n👤 {user.username} — bog'lanish bekor qilindi."


def _bind_callback(cb):
    """Admin chatidagi ✅/❌ tugmasi bosilganda: bog'lashni yakunlash yoki rad etish."""
    cb_msg = cb.get("message") or {}
    cb_chat = str(((cb_msg.get("chat")) or {}).get("id") or "")
    cb_mid = cb_msg.get("message_id")
    data = cb.get("data") or ""
    cb_id = cb.get("id") or ""

    # Istalgan ADMIN chatidan kelgan tugma qabul qilinadi
    if cb_chat not in admin_chatlar():
        tg_answer_cb(cb_id, "Ruxsat yo'q")
        return
    if not (data.startswith("b1:") or data.startswith("b0:")):
        tg_answer_cb(cb_id)
        return
    try:
        state_id = int(data.split(":", 1)[1])
    except ValueError:
        tg_answer_cb(cb_id)
        return
    natija = _bind_qaror(state_id, tasdiq=data.startswith("b1:"))
    if cb_mid:
        tg_edit(cb_chat, cb_mid, natija)
    tg_answer_cb(cb_id, "Bajarildi")
