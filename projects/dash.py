"""Boshqaruv paneli («Umumiy ko'rinish») ma'lumotlari.

Hammasi shu yerda va obyektlar soniga BOG'LIQ BO'LMAGAN sondagi so'rov bilan
hisoblanadi (oldin har obyekt uchun alohida so'rov ketardi).
Ko'rinadigan obyektlar doirasi tashqaridan (visible_projects) beriladi — firma
izolyatsiyasi shu yerda qayta hal qilinmaydi.
"""
import datetime
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import F, Sum
from django.db.models.functions import Coalesce, Greatest
from django.urls import reverse
from django.utils import timezone

from .models import (_LINE_TOTAL, LimitChangeRequest, WeeklyRequest,
                     WeeklyRequestItem)

D0 = Decimal("0.00")
KINDS = ("material", "labor", "machinery", "other")
KIND_NOM = {"material": "Material", "labor": "Ish haqi",
            "machinery": "Mashina chasti", "other": "Ko'zda tutilmagan"}
# Limit zanjiri bosqichlari (views.LIM_JARAYON bilan bir xil tartib) → qisqa yorliq
BOSQICH = {"snab": "Snabjeniyeda", "pto2": "PTO xulosasida", "dir": "Direktorda",
           "prov": "Proverchikda", "adm": "Adminda"}
WK_BOSQICH = {"dir": "Direktorda", "submitted": "Adminda"}

YAQIN_FOIZ = 90        # shu foizdan boshlab «limitga yaqin»
TURIB_QOLGAN_KUN = 3   # boshqa bosqichda shuncha kun tursa — panelda eslatiladi
QORALAMA_KUN = 2       # yuborilmagan qoralama shuncha kundan keyin eslatiladi
ORTACHA_HAFTA = 8      # o'rtacha haftalik sarf shu oxirgi haftalar bo'yicha
ROYXAT_CHEGARA = 60    # so'nggi harakatlar uchun ko'riladigan so'rovlar soni


def pul(v):
    return f"{(v or 0):,.0f}".replace(",", " ")


def qisqa(v):
    """Katta summani ixcham ko'rsatish: 456,1 mln / 1,25 mlrd (aniq qiymat — title'da)."""
    v = Decimal(v or 0)
    a = abs(v)
    if a >= 10 ** 9:
        return f"{v / Decimal(10 ** 9):.2f}".replace(".", ",") + " mlrd"
    if a >= 10 ** 6:
        return f"{v / Decimal(10 ** 6):.1f}".replace(".", ",") + " mln"
    return pul(v)


def foiz(qism, butun):
    return round(float(qism) / float(butun) * 100) if butun else 0


def _kun(dt, now):
    return max((now - dt).days, 0) if dt else 0


def _kun_matn(k):
    return "bugun" if k <= 0 else f"{k} kun"


def _qachon(dt, now):
    s = (now - dt).total_seconds()
    if s < 90:
        return "hozirgina"
    if s < 3600:
        return f"{int(s // 60)} daqiqa oldin"
    mahalliy = timezone.localtime(dt)
    bugun = timezone.localtime(now).date()
    if mahalliy.date() == bugun:
        return f"{int(s // 3600)} soat oldin"
    if mahalliy.date() == bugun - datetime.timedelta(days=1):
        return f"kecha, {mahalliy:%H:%M}"
    return f"{mahalliy:%d.%m.%Y, %H:%M}"


def _oxirgi(*vaqtlar):
    """So'rovning oxirgi harakati vaqti (bo'sh bo'lmaganlarining eng kattasi)."""
    v = [t for t in vaqtlar if t]
    return max(v) if v else None


def _ism(u):
    return u.get_username() if u else "—"


def panel(user, qs, *, lim_bosq=(), wk_bosq=(), tas_show=False, admin=False):
    """Panel konteksti. `qs` — foydalanuvchiga ko'rinadigan (firma filtri qo'llangan)
    obyektlar queryset'i; `lim_bosq`/`wk_bosq` — shu foydalanuvchi javob beradigan
    bosqichlar (uning navbati)."""
    now = timezone.now()
    bugun = timezone.localdate()
    obyektlar = list(qs.select_related("firma").order_by("code"))
    tas_url = reverse("dashboard") + "?tab=tasdiqlar"

    # ---- sarf: obyekt × tur (bitta so'rov) ----
    sarf = defaultdict(lambda: dict.fromkeys(KINDS, D0))
    for r in (WeeklyRequestItem.objects
              .filter(request__project__in=qs, request__status=WeeklyRequest.Status.APPROVED)
              .values("request__project_id", "kind").annotate(s=Sum(_LINE_TOTAL))):
        if r["kind"] in KINDS:
            sarf[r["request__project_id"]][r["kind"]] = r["s"] or D0

    # ---- jarayondagi so'rovlar ----
    lreqs = list(LimitChangeRequest.objects
                 .filter(project__in=qs, status__in=[*BOSQICH, "draft"])
                 .select_related("project", "requested_by"))
    wreqs = list(WeeklyRequest.objects
                 .filter(project__in=qs, status__in=list(WK_BOSQICH))
                 .select_related("project", "created_by"))
    zanjir = {}                      # obyekt id → bosqich yorlig'i (qoralamadan ustun)
    for r in lreqs:
        if r.status in BOSQICH:
            zanjir[r.project_id] = BOSQICH[r.status]
    for r in lreqs:
        if r.status == "draft":
            zanjir.setdefault(r.project_id, "Qoralama")
    for w in wreqs:
        zanjir.setdefault(w.project_id, "Haftalik — " + WK_BOSQICH[w.status])

    # ---- obyektlar qatorlari + jami ----
    qatorlar, diqqat = [], []
    jami_l = dict.fromkeys(KINDS, D0)
    jami_s = dict.fromkeys(KINDS, D0)
    firmalar = {}
    son = {"limitli": 0, "yaqin": 0, "oshgan": 0, "zanjir": 0, "limitsiz": 0}
    for p in obyektlar:
        lim = {"material": p.limit_material or D0, "labor": p.limit_labor or D0,
               "machinery": p.limit_machinery or D0, "other": p.limit_other or D0}
        srf = sarf[p.id]
        limit = sum(lim.values(), D0)
        s_jami = sum(srf.values(), D0)
        for k in KINDS:
            jami_l[k] += lim[k]
            jami_s[k] += srf[k]
        fz = foiz(s_jami, limit)
        jadval_url = reverse("limit_jadval", args=[p.id])
        # limiti bor obyektda biror TUR o'z limitidan oshganmi
        osh_tur = [KIND_NOM[k] for k in KINDS if lim[k] > 0 and srf[k] > lim[k]]
        if limit <= 0:
            holat, holat_nom = "none", "Limitsiz"
            son["limitsiz"] += 1
        else:
            son["limitli"] += 1
            if s_jami > limit:
                holat, holat_nom = "bad", "Limitdan oshgan"
            elif osh_tur:
                holat, holat_nom = "bad", "Tur bo'yicha oshgan"
            elif fz >= YAQIN_FOIZ:
                holat, holat_nom = "warn", "Limitga yaqin"
            else:
                holat, holat_nom = "ok", "Me'yorda"
            if holat == "bad":
                son["oshgan"] += 1
            elif holat == "warn":
                son["yaqin"] += 1
        if p.id in zanjir:
            son["zanjir"] += 1
        f = p.firma
        qatorlar.append({
            "id": p.id, "kod": p.code, "nom": p.name, "firma": f.name if f else "",
            "limit": limit, "sarf": s_jami,
            "limit_str": pul(limit), "sarf_str": pul(s_jami), "qoldiq_str": pul(limit - s_jami),
            "manfiy": s_jami > limit,
            "foiz": fz, "bar": min(fz, 100), "holat": holat, "holat_nom": holat_nom,
            "zanjir": zanjir.get(p.id, ""),
            "url": jadval_url, "obyekt_url": reverse("project_detail", args=[p.id]),
        })
        if f:
            fd = firmalar.setdefault(f.id, {"id": f.id, "nom": f.name, "limit": D0, "sarf": D0, "soni": 0})
            fd["limit"] += limit
            fd["sarf"] += s_jami
            fd["soni"] += 1
        # --- diqqat: limit holati ---
        if limit > 0 and s_jami > limit:
            diqqat.append({"tur": "bad", "ikon": "alert", "ogir": 0, "tartib": -float(s_jami - limit),
                           "sarlavha": f"{p.name} — limitdan oshgan",
                           "izoh": f"Sarf {pul(s_jami)} / limit {pul(limit)} so'm",
                           "belgi": f"+{qisqa(s_jami - limit)}", "url": jadval_url})
        elif osh_tur:
            diqqat.append({"tur": "bad", "ikon": "alert", "ogir": 0, "tartib": 0,
                           "sarlavha": f"{p.name} — {', '.join(osh_tur)} limitdan oshgan",
                           "izoh": "Shu tur bo'yicha sarf o'z limitidan ko'p",
                           "belgi": f"{fz}%", "url": jadval_url})
        elif limit > 0 and fz >= YAQIN_FOIZ:
            diqqat.append({"tur": "warn", "ikon": "gauge", "ogir": 2, "tartib": -fz,
                           "sarlavha": f"{p.name} — limitning {fz}% sarflandi",
                           "izoh": f"Qolgan: {pul(limit - s_jami)} so'm",
                           "belgi": f"{fz}%", "url": jadval_url})

    # ---- diqqat: navbat, turib qolgan so'rovlar, qoralamalar ----
    kutmoqda = sizda = 0
    eng_eski = 0
    for r in lreqs:
        vaqt = _oxirgi(r.created_at, r.snab_at, r.pto2_at, r.director_at, r.prov_at, r.edited_at)
        k = _kun(vaqt, now)
        jadval_url = reverse("limit_jadval", args=[r.project_id])
        if r.status == "draft":
            if k >= QORALAMA_KUN:
                diqqat.append({"tur": "info", "ikon": "draft", "ogir": 4, "tartib": -k,
                               "sarlavha": f"{r.project.name} — limit qoralamasi yuborilmagan",
                               "izoh": f"Saqlagan: {_ism(r.requested_by)}",
                               "belgi": _kun_matn(k), "url": jadval_url})
            continue
        kutmoqda += 1
        eng_eski = max(eng_eski, k)
        if r.status in lim_bosq:
            sizda += 1
            diqqat.append({"tur": "warn", "ikon": "clock", "ogir": 1, "tartib": -k,
                           "sarlavha": f"{r.project.name} — limit so'rovi sizning navbatingizda",
                           "izoh": f"Yubordi: {_ism(r.requested_by)} · {BOSQICH[r.status]}",
                           "belgi": _kun_matn(k), "url": tas_url if tas_show else jadval_url})
        elif k >= TURIB_QOLGAN_KUN:
            diqqat.append({"tur": "info", "ikon": "clock", "ogir": 3, "tartib": -k,
                           "sarlavha": f"{r.project.name} — limit so'rovi {k} kundan beri {BOSQICH[r.status].lower()}",
                           "izoh": f"Yubordi: {_ism(r.requested_by)}",
                           "belgi": _kun_matn(k), "url": jadval_url})
    for w in wreqs:
        vaqt = _oxirgi(w.created_at, w.director_at, w.edited_at)
        k = _kun(vaqt, now)
        kutmoqda += 1
        eng_eski = max(eng_eski, k)
        hafta_nom = f"{w.week_start:%d.%m}–{w.week_end:%d.%m}"
        jadval_url = reverse("limit_jadval", args=[w.project_id])
        if w.status in wk_bosq:
            sizda += 1
            diqqat.append({"tur": "warn", "ikon": "clock", "ogir": 1, "tartib": -k,
                           "sarlavha": f"{w.project.name} — haftalik so'rov ({hafta_nom}) sizning navbatingizda",
                           "izoh": f"Kiritdi: {_ism(w.created_by)}",
                           "belgi": _kun_matn(k), "url": tas_url if tas_show else jadval_url})
        elif k >= TURIB_QOLGAN_KUN:
            diqqat.append({"tur": "info", "ikon": "clock", "ogir": 3, "tartib": -k,
                           "sarlavha": f"{w.project.name} — haftalik so'rov ({hafta_nom}) {k} kundan beri {WK_BOSQICH[w.status].lower()}",
                           "izoh": f"Kiritdi: {_ism(w.created_by)}",
                           "belgi": _kun_matn(k), "url": jadval_url})
    if admin:
        from .auth2fa import tg_majburiy
        U = get_user_model()
        tg_yoq = U.objects.filter(is_active=True).exclude(profile__telegram_chat_id__gt="").count()
        if tg_yoq:
            diqqat.append({"tur": "gray", "ikon": "user", "ogir": 5, "tartib": 0,
                           "sarlavha": f"{tg_yoq} ta xodim Telegram botga ulanmagan",
                           "izoh": ("Ular tizimga kira olmaydi" if tg_majburiy()
                                    else "Ular kodsiz, faqat parol bilan kiradi"),
                           "belgi": "", "url": "/admin/auth/user/"})
    diqqat.sort(key=lambda d: (d["ogir"], d["tartib"]))

    # ---- umumiy ko'rsatkichlar ----
    limit_j = sum(jami_l.values(), D0)
    sarf_j = sum(jami_s.values(), D0)
    qoldiq_j = limit_j - sarf_j
    foiz_j = foiz(sarf_j, limit_j)

    # ---- haftalik sarf qatori (tasdiqlangan) ----
    hafta = [(r["request__week_start"], r["s"] or D0) for r in
             (WeeklyRequestItem.objects
              .filter(request__project__in=qs, request__status=WeeklyRequest.Status.APPROVED)
              .values("request__week_start").annotate(s=Sum(_LINE_TOTAL))
              .order_by("request__week_start"))]
    # Oxirgi 4 hafta va undan oldingi 4 hafta — teng davrlar solishtiriladi
    # (kalendar oyi bo'yicha solishtirish oy boshida doim «−100%» ko'rsatardi)
    d4 = bugun - datetime.timedelta(weeks=4)
    d8 = bugun - datetime.timedelta(weeks=8)
    songgi4 = sum((s for d, s in hafta if d > d4), D0)
    oldingi4 = sum((s for d, s in hafta if d8 < d <= d4), D0)
    trend = None
    if oldingi4 > 0:
        trend = round(float(songgi4 - oldingi4) / float(oldingi4) * 100)
    chegara = bugun - datetime.timedelta(weeks=ORTACHA_HAFTA)
    ortacha = sum((s for d, s in hafta if d > chegara), D0) / ORTACHA_HAFTA
    yetadi = None
    if ortacha > 0 and qoldiq_j > 0:
        yetadi = int(qoldiq_j / ortacha)

    kategoriyalar = []
    for k, rang in zip(KINDS, ("#2563eb", "#0f766e", "#7c3aed", "#c2410c")):
        pz = foiz(jami_s[k], jami_l[k])
        kategoriyalar.append({
            "nom": KIND_NOM[k], "rang": rang, "pct": pz, "bar": min(pz, 100),
            "bor": jami_l[k] > 0 or jami_s[k] > 0,
            "sarf_q": qisqa(jami_s[k]), "limit_q": qisqa(jami_l[k]),
            "aniq": f"{pul(jami_s[k])} / {pul(jami_l[k])} so'm",
            "daraja": "bad" if (jami_s[k] > jami_l[k] and jami_l[k] > 0) else ("warn" if pz >= 80 else ""),
        })

    firma_kartalar = []
    for fd in sorted(firmalar.values(), key=lambda x: (-x["limit"], x["nom"])):
        pz = foiz(fd["sarf"], fd["limit"])
        firma_kartalar.append({
            "id": fd["id"], "nom": fd["nom"], "soni": fd["soni"],
            "bosh": "".join(s[0] for s in fd["nom"].split()[:2]).upper(),
            "limit_q": qisqa(fd["limit"]), "aniq": f"{pul(fd['sarf'])} / {pul(fd['limit'])} so'm",
            "pct": pz, "bar": min(pz, 100),
            "daraja": "bad" if fd["sarf"] > fd["limit"] > 0 else ("warn" if pz >= YAQIN_FOIZ else ""),
        })

    return {
        "dp_qatorlar": qatorlar,
        "dp_son": son,
        "dp_jami": len(qatorlar),
        "dp_diqqat": diqqat,
        "dp_harakatlar": _harakatlar(qs, now),
        "dp_kategoriyalar": kategoriyalar,
        "dp_firmalar": firma_kartalar,
        "dp_hafta": [{"d": d.isoformat(), "v": float(s)} for d, s in hafta],
        "dp_kpi": {
            "limit_q": qisqa(limit_j), "limit_aniq": pul(limit_j),
            "sarf_q": qisqa(sarf_j), "sarf_aniq": pul(sarf_j),
            "qoldiq_q": qisqa(qoldiq_j), "qoldiq_aniq": pul(qoldiq_j),
            "qoldiq_manfiy": qoldiq_j < 0,
            "foiz": foiz_j, "bar": min(foiz_j, 100),
            "qolgan_foiz": max(0, 100 - foiz_j) if limit_j > 0 else 0,
            "limitli": son["limitli"],
            "songgi4_q": qisqa(songgi4), "trend": trend,
            "trend_abs": abs(trend) if trend is not None else None,
            "ortacha_q": qisqa(ortacha), "yetadi": yetadi, "ortacha_hafta": ORTACHA_HAFTA,
            "kutmoqda": kutmoqda, "sizda": sizda,
            "eng_eski": _kun_matn(eng_eski) if kutmoqda else "",
        },
    }


def _harakatlar(qs, now, soni=8):
    """So'nggi harakatlar lentasi — so'rovlardagi imzo vaqtlaridan yig'iladi
    (alohida jurnal jadvali yo'q)."""
    ev = []

    def qosh(vaqt, kim, matn, obyekt, tur, url):
        if vaqt:
            ev.append({"vaqt": vaqt, "kim": _ism(kim), "matn": matn, "obyekt": obyekt,
                       "tur": tur, "url": url})

    def _songgi(*maydonlar):
        return Greatest(*[Coalesce(F(m), F("created_at")) for m in maydonlar], F("created_at"))

    for r in (LimitChangeRequest.objects.filter(project__in=qs)
              .select_related("project", "requested_by", "snab_by", "pto2_by",
                              "director_by", "prov_by", "decided_by")
              .annotate(_songgi=_songgi("snab_at", "pto2_at", "director_at", "prov_at", "decided_at"))
              .order_by("-_songgi")[:ROYXAT_CHEGARA]):
        nom, url = r.project.name, reverse("limit_jadval", args=[r.project_id])
        qosh(r.created_at, r.requested_by,
             "limit qoralamasini saqladi" if r.status == "draft" else "limit so'rovini kiritdi",
             nom, "send", url)
        qosh(r.snab_at, r.snab_by, "limitni narxladi (snabjeniye)", nom, "step", url)
        qosh(r.pto2_at, r.pto2_by, "PTO xulosasini berdi", nom, "step", url)
        qosh(r.director_at, r.director_by, "limitni tasdiqladi (direktor)", nom, "ok", url)
        qosh(r.prov_at, r.prov_by, "limitni tekshirdi (proverchik)", nom, "ok", url)
        if r.status == "approved":
            qosh(r.decided_at, r.decided_by, "limitni yakuniy tasdiqladi", nom, "ok", url)
        elif r.status == "rejected":
            qosh(r.decided_at, r.decided_by, "limit so'rovini rad etdi", nom, "bad", url)
    for w in (WeeklyRequest.objects.filter(project__in=qs)
              .select_related("project", "created_by", "director_by", "approved_by")
              .annotate(_songgi=_songgi("director_at", "approved_at"))
              .order_by("-_songgi")[:ROYXAT_CHEGARA]):
        nom, url = w.project.name, reverse("limit_jadval", args=[w.project_id])
        hafta_nom = f"{w.week_start:%d.%m}–{w.week_end:%d.%m}"
        qosh(w.created_at, w.created_by, f"haftalik so'rov kiritdi ({hafta_nom})", nom, "send", url)
        qosh(w.director_at, w.director_by, f"haftalikni tasdiqladi — direktor ({hafta_nom})", nom, "ok", url)
        if w.status == "approved":
            qosh(w.approved_at, w.approved_by, f"haftalikni yakuniy tasdiqladi ({hafta_nom})", nom, "ok", url)
    ev.sort(key=lambda e: e["vaqt"], reverse=True)
    ev = ev[:soni]
    for e in ev:
        e["qachon"] = _qachon(e["vaqt"], now)
    return ev
