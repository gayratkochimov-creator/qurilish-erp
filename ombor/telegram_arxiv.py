"""Ombor ARXIV boti — snabjeniye bo'limi uchun.

1) Har tasdiqlangan PRIXOD / RASXOD avtomatik Telegramga boradi:
   - admin (zaxira) chatiga
   - shu obyektga biriktirilgan, botdan ro'yxatdan o'tgan xodimlarga
   Hujjat rasmi (prixodda) ham yuboriladi. Har qator ostida yangi qoldiq.

2) Bot menyusi — /ombor:
   - snabjeniye xodimi FAQAT o'ziga biriktirilgan obyektlarni ko'radi
   - admin (superuser yoki zaxira chati) HAMMA obyektni tanlab ko'radi
   - obyekt ichida: Ostatka | Prixodlar | Rasxodlar

Ro'yxatdan o'tish — mavjud oqim: /start -> login -> parol -> admin tasdig'i.
"""
import os
from decimal import Decimal

from projects.auth2fa import tg_answer_cb, tg_send, tg_send_kb
from projects.management.commands.telegram_backup import _api, _token_chat


# ------------------------------------------------------------- format helperlar

def _pul(v):
    try:
        return f"{int(Decimal(v)):,}".replace(",", " ")
    except Exception:
        return str(v)


def _son(v):
    try:
        d = Decimal(v)
        s = f"{d:.3f}".rstrip("0").rstrip(".")
        return s.replace(".", ",")
    except Exception:
        return str(v)


def _admin_chat():
    return str(_token_chat()[1] or "")


# ------------------------------------------------------------- kim ko'ra oladi

def _chat_user(chat_id):
    from projects.models import UserProfile
    prof = (UserProfile.objects.filter(telegram_chat_id=str(chat_id))
            .select_related("user").first())
    return prof.user if prof else None


def _korinadigan_obyektlar(chat_id):
    """Shu chat ko'ra oladigan obyektlar. None = ro'yxatdan o'tmagan."""
    from projects.models import Project
    from projects.roles import visible_projects
    if str(chat_id) == _admin_chat():
        return Project.objects.all()
    u = _chat_user(chat_id)
    if u is None:
        return None
    return visible_projects(u)


def _qabul_qiluvchilar(proj):
    """Hujjat arxivi boradigan chatlar: admin + obyektga biriktirilgan bog'langanlar."""
    from projects.models import UserProfile
    chats = set()
    a = _admin_chat()
    if a:
        chats.add(a)
    if proj is not None:
        for cid in (UserProfile.objects.filter(projects=proj)
                    .exclude(telegram_chat_id="")
                    .values_list("telegram_chat_id", flat=True)):
            chats.add(str(cid))
    return chats


# ------------------------------------------------------------- hujjat arxivi

def _qoldiq(warehouse, material):
    from .models import StockBalance
    b = StockBalance.objects.filter(warehouse=warehouse, material=material).first()
    return b.quantity if b else Decimal("0")


def _bitta_rasm(chat, path, caption):
    try:
        if not os.path.exists(path):
            return
        with open(path, "rb") as f:
            data = f.read()
        token = _token_chat()[0]
        _api(token, "sendPhoto",
             fields={"chat_id": chat, "caption": caption},
             files={"photo": (os.path.basename(path), data)})
    except Exception:
        pass


def _rasm_yubor(chat, receipt):
    """Prixod rasmlarini yuborish: mahsulot rasmlari + nakladnoylar (itogo bilan)."""
    try:
        if receipt.image:  # eski yozuvlardagi yakka rasm
            _bitta_rasm(chat, receipt.image.path, f"📷 Prixod #{receipt.id} — mahsulot rasmi")
        for im in receipt.images.all():
            if im.turi == "nakladnoy":
                itogo = f" — itogo {_pul(im.summa)} so'm" if im.summa else ""
                caption = f"🧾 Prixod #{receipt.id} — NAKLADNOY{itogo}"
            else:
                caption = f"📷 Prixod #{receipt.id} — mahsulot rasmi"
            _bitta_rasm(chat, im.image.path, caption)
    except Exception:
        pass


def arxiv_prixod(r):
    """Tasdiqlangan prixodni arxiv botga yuborish (matn + rasm + qoldiq)."""
    proj = r.warehouse.project if r.warehouse_id else None
    vaqt = f" · {r.created_at:%H:%M}" if r.created_at else ""
    lines = [f"⬇️ PRIXOD #{r.id} · {r.date:%d.%m.%Y}{vaqt}"]
    if proj:
        lines.append(f"🏗 {proj.code} — {proj.name}")
    lines.append(f"🏬 Ombor: {r.warehouse.kod_nom}")
    if r.supplier_id:
        lines.append(f"🚚 Yetkazib beruvchi: {r.supplier.name}")
    if r.doc_number:
        lines.append(f"📄 Nakladnoy: {r.doc_number}")
    lines.append("")
    for it in r.items.select_related("material"):
        m = it.material
        lines.append(f"• {m.name}: {_son(it.quantity)} {m.unit} × {_pul(it.unit_price)} = {_pul(it.total)}")
        lines.append(f"   ↳ qoldiq: {_son(_qoldiq(r.warehouse, m))} {m.unit}")
    lines.append("")
    jami = r.total
    nak_jami = sum((im.summa or Decimal("0")) for im in r.images.all() if im.turi == "nakladnoy")
    if jami and nak_jami:
        lines.append(f"📦 Materiallar jami: {_pul(jami)} so'm")
        lines.append(f"🧾 Nakladnoylar itogosi: {_pul(nak_jami)} so'm")
        lines.append(f"💰 UMUMIY: {_pul(jami + nak_jami)} so'm")
    elif nak_jami:
        lines.append(f"🧾 NAKLADNOYLAR JAMI: {_pul(nak_jami)} so'm")
    elif jami:
        lines.append(f"💰 JAMI: {_pul(jami)} so'm")
    if r.note:
        lines.append(f"📝 {r.note}")
    text = "\n".join(lines)[:3800]
    for chat in _qabul_qiluvchilar(proj):
        tg_send(chat, text)
        _rasm_yubor(chat, r)


def arxiv_rasxod(x):
    """Tasdiqlangan rasxodni arxiv botga yuborish (matn + qoldiq)."""
    proj = x.warehouse.project if x.warehouse_id else None
    vaqt = f" · {x.created_at:%H:%M}" if x.created_at else ""
    lines = [f"⬆️ RASXOD #{x.id} · {x.date:%d.%m.%Y}{vaqt}"]
    if proj:
        lines.append(f"🏗 {proj.code} — {proj.name}")
    lines.append(f"🏬 Ombor: {x.warehouse.kod_nom}")
    if x.work_section_id:
        lines.append(f"🔧 Ish bo'limi: {x.work_section.name}")
    if x.recipient:
        lines.append(f"👤 Oldi: {x.recipient}")
    if x.doc_number:
        lines.append(f"📄 Hujjat: {x.doc_number}")
    lines.append("")
    for it in x.items.select_related("material"):
        m = it.material
        lines.append(f"• {m.name}: {_son(it.quantity)} {m.unit} × {_pul(it.unit_cost)} = {_pul(it.total)}")
        lines.append(f"   ↳ qoldiq: {_son(_qoldiq(x.warehouse, m))} {m.unit}")
    lines.append("")
    lines.append(f"💰 JAMI: {_pul(x.total)} so'm")
    if x.note:
        lines.append(f"📝 {x.note}")
    text = "\n".join(lines)[:3800]
    for chat in _qabul_qiluvchilar(proj):
        tg_send(chat, text)


# ------------------------------------------------------------- bot menyusi

def _admin_mi(chat_id):
    """Admin: zaxira chati yoki superuser sifatida bog'langan chat."""
    if str(chat_id) == _admin_chat():
        return True
    u = _chat_user(chat_id)
    return bool(u is not None and u.is_superuser)


def ombor_komanda(chat_id):
    """/ombor — admin: avval FIRMA tanlaydi; xodim: o'z obyektlari ro'yxati."""
    projs = _korinadigan_obyektlar(chat_id)
    if projs is None:
        tg_send(chat_id, "Avval ro'yxatdan o'ting: /start")
        return
    if _admin_mi(chat_id):
        from projects.models import Firma
        firmalar = list(Firma.objects.order_by("name")[:30])
        if firmalar:
            kb = [[{"text": f.name[:48], "callback_data": f"fi:{f.id}"}] for f in firmalar]
            tg_send_kb(chat_id, "🏢 Firmani tanlang:", kb)
            return
    projs = list(projs.order_by("code")[:30])
    if not projs:
        tg_send(chat_id, "Sizga obyekt biriktirilmagan — admin bilan bog'laning.")
        return
    kb = [[{"text": f"{p.code} — {p.name}"[:48], "callback_data": f"ob:{p.id}"}] for p in projs]
    tg_send_kb(chat_id, "🏗 Obyektni tanlang:", kb)


def _ostatka_matn(p):
    from .models import StockBalance
    qs = (StockBalance.objects.filter(warehouse__project=p)
          .select_related("material").order_by("material__name")[:60])
    lines = [f"📦 OSTATKA — {p.code} {p.name}", ""]
    bor = False
    for b in qs:
        if b.quantity == 0:
            continue
        bor = True
        lines.append(f"• {b.material.name}: {_son(b.quantity)} {b.material.unit}")
    if not bor:
        lines.append("Ombor bo'sh — qoldiq yo'q.")
    return "\n".join(lines)[:3900]


def _prixodlar_matn(p):
    from .models import Receipt
    docs = list(Receipt.objects.filter(warehouse__project=p, is_posted=True)
                .select_related("supplier").prefetch_related("items__material")[:5])
    if not docs:
        return f"⬇️ {p.code} — tasdiqlangan prixod yo'q."
    lines = [f"⬇️ OXIRGI PRIXODLAR — {p.code}", ""]
    for r in docs:
        yb = f" · {r.supplier.name}" if r.supplier_id else ""
        lines.append(f"#{r.id} · {r.date:%d.%m.%Y}{yb} · {_pul(r.total)} so'm")
        for it in list(r.items.all())[:6]:
            lines.append(f"   • {it.material.name}: {_son(it.quantity)} {it.material.unit}")
        lines.append("")
    return "\n".join(lines)[:3900]


def _rasxodlar_matn(p):
    from .models import Issue
    docs = list(Issue.objects.filter(warehouse__project=p, is_posted=True)
                .select_related("work_section").prefetch_related("items__material")[:5])
    if not docs:
        return f"⬆️ {p.code} — tasdiqlangan rasxod yo'q."
    lines = [f"⬆️ OXIRGI RASXODLAR — {p.code}", ""]
    for x in docs:
        kim = f" · {x.recipient}" if x.recipient else ""
        lines.append(f"#{x.id} · {x.date:%d.%m.%Y}{kim} · {_pul(x.total)} so'm")
        for it in list(x.items.all())[:6]:
            lines.append(f"   • {it.material.name}: {_son(it.quantity)} {it.material.unit}")
        lines.append("")
    return "\n".join(lines)[:3900]


def ombor_callback(data, chat_id, cb_id):
    """fi:/ob:/os:/pr:/rx: tugmalari — ruxsat ICHKARIDA tekshiriladi."""
    projs = _korinadigan_obyektlar(chat_id)
    if projs is None:
        tg_answer_cb(cb_id, "Avval /start bilan ro'yxatdan o'ting")
        return
    try:
        pid = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        tg_answer_cb(cb_id)
        return

    # Firma tanlandi (faqat admin) -> shu firmaning obyektlari
    if data.startswith("fi:"):
        if not _admin_mi(chat_id):
            tg_answer_cb(cb_id, "Ruxsat yo'q")
            return
        plist = list(projs.filter(firma_id=pid).order_by("code")[:30])
        if not plist:
            tg_send(chat_id, "Bu firmada obyekt yo'q.")
        else:
            kb = [[{"text": f"{p.code} — {p.name}"[:48], "callback_data": f"ob:{p.id}"}] for p in plist]
            tg_send_kb(chat_id, "🏗 Obyektni tanlang:", kb)
        tg_answer_cb(cb_id)
        return

    p = projs.filter(pk=pid).first()
    if p is None:
        tg_answer_cb(cb_id, "Bu obyekt sizga tegishli emas")
        return

    if data.startswith("ob:"):
        kb = [
            [{"text": "📦 Ostatka", "callback_data": f"os:{p.id}"}],
            [{"text": "⬇️ Prixodlar", "callback_data": f"pr:{p.id}"}],
            [{"text": "⬆️ Rasxodlar", "callback_data": f"rx:{p.id}"}],
        ]
        tg_send_kb(chat_id, f"🏗 {p.code} — {p.name}\nBo'limni tanlang:", kb)
    elif data.startswith("os:"):
        tg_send(chat_id, _ostatka_matn(p))
    elif data.startswith("pr:"):
        tg_send(chat_id, _prixodlar_matn(p))
    elif data.startswith("rx:"):
        tg_send(chat_id, _rasxodlar_matn(p))
    tg_answer_cb(cb_id)
