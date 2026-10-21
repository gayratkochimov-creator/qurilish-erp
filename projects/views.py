import datetime
import io
import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from django.db.models import Sum

from .models import (_LINE_TOTAL, Firma, GrafikRow, LimitChangeItem,
                     LimitChangeRequest, LimitItem, Project, WeeklyRequest,
                     WeeklyRequestItem, sync_limit_items, hafta_qatorlarini_qayta_nomla)
from .roles import (
    can_access_project, is_admin, is_director, is_prorab, is_pto,
    visible_firmas, visible_projects,
)


def _to_dec(v):
    """Excel katagidan Decimal (bo'sh/xato bo'lsa None)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _money(value):
    return f"{value:,.0f}".replace(",", " ")


def _firma_yoki_403(request, project):
    """Obyekt foydalanuvchining firmasiga tegishli bo'lmasa 403 (firma izolyatsiyasi)."""
    if not can_access_project(request.user, project):
        raise PermissionDenied("Bu obyekt sizning firmangizga tegishli emas.")
    return project

# eslatma: _qty pastda (bitta joyda) aniqlangan — oldin shu yerda dublikat bor edi


KIND_NOMI = {"material": "Material", "labor": "Ish haqi", "machinery": "Mashina chasti",
             "other": "Ko'zda tutilmagan xarajatlar"}
KINDS = ("material", "labor", "machinery", "other")

# Tasdiqlash zanjirida «jarayonda» statuslar:
# snab (snabjeniye ko'rigida) -> pto2 (PTO xulosasida) -> dir -> adm
LIM_JARAYON = ["snab", "pto2", "dir", "prov", "adm"]


def _provlar():
    """Barcha faol proverchiklar (firmalararo — har qanday firma obyektini tekshiradi)."""
    from django.contrib.auth import get_user_model
    return list(get_user_model().objects.filter(is_active=True, is_superuser=False, profile__role="prov")
                .order_by("username"))


def _prov_bor(firma=None):
    """Tizimda proverchik bormi? Bo'lsa zanjir: direktor -> proverchik -> admin
    (proverchik firmaga bog'lanmagan — har qanday obyektni tekshiradi)."""
    return bool(_provlar())


def _provlarga_xabar(request, matn):
    """Hamma proverchikka tizim banneri + Telegram (yuboruvchining o'ziga emas).
    Qaytaradi: xabar matni qo'shimchasi."""
    nomlar, tg_n = [], 0
    for u in _provlar():
        if u.pk == request.user.pk:
            continue
        _, tg = _lj_xabar_yubor(matn, u, request.user)
        nomlar.append(u.username)
        tg_n += 1 if tg else 0
    if not nomlar:
        return ""
    return f" Proverchik(lar)ga xabar yuborildi: {', '.join(nomlar)}" + (f" (Telegram: {tg_n})." if tg_n else " (tizimda).")


def _snab_bor(p):
    """Obyektga snabjeniye xodimi biriktirilganmi?"""
    from .models import UserProfile
    return UserProfile.objects.filter(role="snab", projects=p).exists()


def _limit_boshlangich(p):
    """Yangi limit so'rovining boshlang'ich bosqichi:
    snabjeniye bo'lsa — unga; bo'lmasa to'g'ridan-to'g'ri direktorga."""
    return "snab" if _snab_bor(p) else "dir"


def _limit_yubor_xabar(status):
    return ("Limit snabjeniye ko'rigiga yuborildi. Zanjir: snabjeniye → PTO xulosasi → direktor → admin."
            if status == "snab" else
            "Limit direktor tasdig'iga yuborildi. Direktor, so'ng admin tasdiqlagach kuchga kiradi.")


def _lj_url(lim, **params):
    """Limit jadvali manzili — RAQAMLI limit bilan (?limit=N&...)."""
    url = reverse("limit_jadval", args=[lim.project_id]) + f"?limit={lim.raqam}"
    for k, v in params.items():
        url += f"&{k}={v}"
    return url


def _lj_joriy(p, request):
    """?limit=<raqam> ko'rsatilgan limit; bo'lmasa obyektning joriy (oxirgi) limiti."""
    r = (request.GET.get("limit") or "").strip()
    if r.isdigit():
        lim = p.limitlar.filter(raqam=int(r)).first()
        if lim is not None:
            return lim
    return p.joriy_limit()


def _items_by_kind(items):
    """Qatorlarni kategoriya bo'yicha yig'ish: {kind: summa}."""
    d = {k: Decimal("0.00") for k in KINDS}
    for it in items:
        k = it.kind if it.kind in d else "material"
        d[k] += (it.quantity or Decimal("0")) * (it.unit_price or Decimal("0"))
    return d


def _limit_oshish(lim, qoshimcha, exclude_request_id=None):
    """Berilgan qatorlar limitdan oshadimi?

    `qoshimcha` — {kind: summa}. Tasdiqlangan sarf hisobga olinadi
    (`exclude_request_id` berilsa, o'sha so'rov sarfdan chiqariladi — qayta
    tasdiqlashda o'zini ikki marta sanamaslik uchun).
    Ro'yxat qaytaradi: har bir oshgan kategoriya bo'yicha tushuntirish.
    Limiti 0 bo'lgan (belgilanmagan) kategoriya tekshirilmaydi.
    """
    qs = WeeklyRequestItem.objects.filter(
        request__limit=lim,
        request__status=WeeklyRequest.Status.APPROVED,
    )
    if exclude_request_id:
        qs = qs.exclude(request_id=exclude_request_id)
    sarf = {k: Decimal("0.00") for k in KINDS}
    for r in qs.values("kind").annotate(s=Sum(_LINE_TOTAL)):
        if r["kind"] in sarf:
            sarf[r["kind"]] = r["s"] or Decimal("0.00")

    # Shu RAQAMLI limitning tur bo'yicha summasi (qatorlardan); qatorsiz eski limitda —
    # obyekt darajasidagi raqam
    chek = {k: Decimal("0.00") for k in KINDS}
    for r in lim.items.values("kind").annotate(s=Sum(_LINE_TOTAL)):
        if r["kind"] in chek:
            chek[r["kind"]] = r["s"] or Decimal("0.00")
    if not any(chek.values()) and lim.raqam == 1:
        project = lim.project
        chek = {"material": project.limit_material or Decimal("0.00"),
                "labor": project.limit_labor or Decimal("0.00"),
                "machinery": project.limit_machinery or Decimal("0.00"),
                "other": project.limit_other or Decimal("0.00")}
    oshgan = []
    for k in KINDS:
        so_ralgan = qoshimcha.get(k, Decimal("0.00"))
        if so_ralgan <= 0 or chek[k] <= 0:
            continue
        qoldiq = chek[k] - sarf[k]
        if so_ralgan > qoldiq:
            oshgan.append(
                f"{KIND_NOMI[k]}: qoldiq {_money(max(qoldiq, Decimal('0')))}, "
                f"so'ralmoqda {_money(so_ralgan)} "
                f"(oshadi: {_money(so_ralgan - qoldiq)})"
            )
    return oshgan


def _hafta_sana_xatosi(lim, ws, we, exclude_id=None):
    """Haftalik so'rov sanalarini tekshiradi. Xato bo'lsa matn, bo'lmasa None qaytaradi."""
    if we < ws:
        return (f"Hafta oxiri ({we:%d.%m.%Y}) hafta boshidan ({ws:%d.%m.%Y}) oldin bo'lishi mumkin emas.")
    if (we - ws).days > 31:
        return (f"Davr juda uzun ({(we - ws).days + 1} kun). Haftalik so'rov 31 kundan oshmasin.")
    qs = lim.weekly_requests.filter(week_start=ws, week_end=we)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if qs.exists():
        return (f"Bu obyektda {ws:%d.%m.%Y} — {we:%d.%m.%Y} davri uchun so'rov allaqachon mavjud. "
                f"Mavjudini tahrirlang yoki boshqa davr tanlang.")
    return None


def _limitga_yangi_materiallar(req):
    """Haftalik so'rovdagi, umumiy limitda YO'Q materiallarni umumiy limitga qo'shadi.

    Miqdor = 0 → limit SUMMASI oshmaydi, material faqat loyiha ro'yxatiga kiradi.
    Shu sabab `recompute_limits()` CHAQIRILMAYDI: tarkibi bo'sh, lekin limiti
    raqamli kiritilgan obyektlarda u limitni nolga tushirib yuborardi.
    Qo'shilgan nomlar ro'yxatini qaytaradi.
    """
    lim = req.limit or req.project.joriy_limit()
    p = lim.project
    mavjud = {(li.kind, (li.name or "").strip().lower()) for li in lim.items.all()}
    yangi, korilgan = [], set()
    for it in req.items.all():
        nom = (it.name or "").strip()
        kalit = (it.kind, nom.lower())
        if not nom or kalit in mavjud or kalit in korilgan:
            continue
        korilgan.add(kalit)
        yangi.append(LimitItem(
            project=p, limit=lim, kind=it.kind, name=nom, unit=it.unit or "",
            quantity=Decimal("0"), unit_price=it.unit_price or Decimal("0"),
            bolim=(it.bolim or "").strip()[:200],   # bo'lim qoldig'i to'g'ri yurishi uchun
        ))
    if yangi:
        LimitItem.objects.bulk_create(yangi)
    return [li.name for li in yangi]


RANGLAR = {
    "limitsiz": "#8a8d90",
    "normal": "#107e3e",
    "limitga yaqin": "#e9730c",
    "RUXSAT KERAK": "#bb0000",
}

HOLAT_CLASS = {
    "normal": "ok",
    "limitga yaqin": "warn",
    "RUXSAT KERAK": "bad",
    "limitsiz": "",
}


def _holat(limit, sarf):
    """Limit holatini bir joyda aniqlash (model bilan bir xil mantiq)."""
    if limit <= 0:
        return "limitsiz"
    if sarf > limit:
        return "RUXSAT KERAK"
    if sarf / limit >= Decimal("0.9"):
        return "limitga yaqin"
    return "normal"


# Haftalik narx umumiy limit narxidan shuncha FOIZdan ko'p farq qilsa — qator
# «narx o'zgargan» sanaladi: PTO izoh yozishi shart, tasdiqlovchiga farq ko'rinadi
NARX_FARQ_FOIZ = Decimal("5")


def _narx_farq_foiz(limit_narx, narx):
    """Narx farqi foizda (limit narxiga nisbatan). Limit narxi 0 bo'lsa — 0."""
    if not limit_narx or limit_narx <= 0:
        return Decimal("0")
    return ((narx - limit_narx) / limit_narx * 100).quantize(Decimal("0.1"))


def _nom_kalit(kind, name):
    return (kind, (name or "").strip().lower())


def _joriy_narxlar(p):
    """Har nom uchun JORIY narx — shu nomdagi ENG OXIRGI tasdiqlangan haftalik narxi.
    {(kind, nom): narx}. Hali so'ralmagan nomlar ro'yxatda bo'lmaydi (limit narxi qoladi)."""
    joriy = {}
    qs = (WeeklyRequestItem.objects
          .filter(request__project=p, request__status=WeeklyRequest.Status.APPROVED)
          .order_by("request__week_start", "request_id", "id")
          .values_list("kind", "name", "unit_price"))
    for kind, name, narx in qs:
        joriy[_nom_kalit(kind, name)] = narx     # oxirgisi g'olib
    return joriy


def _narx_tahlil(p):
    """REJA — BAJARILGAN — QOLGAN — NARX FARQI (nom+bo'lim kesimida).

    Limit MIQDOR bilan nazorat qilinadi, pul JORIY narx bilan hisoblanadi:
      bajarilgan  = tasdiqlangan haftaliklar (haqiqiy summa → o'rtacha narx)
      qolgan      = (limit miqdori − bajarilgan miqdor) × JORIY narx  (prognoz)
      joriy narx  = shu nomdagi eng oxirgi tasdiqlangan haftalik narxi,
                    hali so'ralmagan bo'lsa — limit narxi
      narx farqi  = bajarilgan + qolgan prognozi − limit summasi
                    (faqat NARX ta'siri; miqdor oshishi bunga kirmaydi —
                     u avvalgidek «qoldi: manfiy» / «oshgan» bilan ko'rsatiladi)
    Qaytaradi: (qatorlar {kalit: {...}}, toifa jami {kind: {...}}, umumiy {...})
    kalit = nom|bo'lim (kichik harf) — bolim_qoldiq bilan bir xil.
    """
    joriy_map = _joriy_narxlar(p)
    qat = {}
    # Limitda YO'Q (nom+bo'lim mos kelmagan) tasdiqlangan qatorlar — toifa jamiga
    # kiradi, aks holda «joriy narxda kerak» sarflangandan kam chiqib qoladi
    yoq = {k: Decimal("0") for k in KINDS}
    for li in p.limit_items.all().order_by("id"):
        k = (li.name or "").strip().lower() + "|" + (li.bolim or "").strip().lower()
        d = qat.get(k)
        if d is None:
            d = qat[k] = {
                "kind": li.kind, "name": li.name, "bolim": li.bolim, "unit": li.unit,
                "limit_qty": Decimal("0"), "limit_sum": Decimal("0"),
                "limit_price": li.unit_price or Decimal("0"),
                "fakt_qty": Decimal("0"), "fakt_sum": Decimal("0"),
            }
        d["limit_qty"] += li.quantity
        d["limit_sum"] += li.total
        d["fakt_qty"] += li.boshlangich_qty or 0
        d["fakt_sum"] += li.boshlangich_sum or 0
    for wi in (WeeklyRequestItem.objects
               .filter(request__project=p, request__status=WeeklyRequest.Status.APPROVED)):
        k = (wi.name or "").strip().lower() + "|" + (wi.bolim or "").strip().lower()
        d = qat.get(k)
        if d is None:
            yoq[wi.kind if wi.kind in yoq else "material"] += wi.total
            continue
        d["fakt_qty"] += wi.quantity
        d["fakt_sum"] += wi.total

    kat = {k: {"limit_sum": Decimal("0"), "fakt_sum": yoq[k],
               "prognoz": Decimal("0"), "farq": Decimal("0"), "ozgargan": 0}
           for k in KINDS}
    for d in qat.values():
        # bir nom bir necha qatorda turli narxda bo'lsa — o'rtacha limit narxi
        # (tiyingacha yaxlitlangan BITTA qiymat hamma hisobda ishlatiladi)
        if d["limit_qty"] > 0:
            d["limit_price"] = (d["limit_sum"] / d["limit_qty"]).quantize(Decimal("0.01"))
        lp = d["limit_price"]
        d["joriy"] = joriy_map.get(_nom_kalit(d["kind"], d["name"]), lp)
        d["fakt_avg"] = ((d["fakt_sum"] / d["fakt_qty"]).quantize(Decimal("0.01"))
                         if d["fakt_qty"] > 0 else Decimal("0"))
        d["qolgan_qty"] = d["limit_qty"] - d["fakt_qty"]
        qolgan = max(d["qolgan_qty"], Decimal("0"))
        # prognoz: qolgan (manfiy bo'lsa 0) × joriy narx
        d["prognoz"] = (qolgan * d["joriy"]).quantize(Decimal("0.01"))
        # narx farqi: sarflangan qism (haqiqiy − limit narxida) + kutilayotgan qism
        d["farq_sarf"] = (d["fakt_sum"] - d["fakt_qty"] * lp).quantize(Decimal("0.01"))
        d["farq_kut"] = (qolgan * (d["joriy"] - lp)).quantize(Decimal("0.01"))
        d["farq"] = d["farq_sarf"] + d["farq_kut"]
        d["farq_foiz"] = _narx_farq_foiz(lp, d["joriy"])
        d["narx_ozgargan"] = d["joriy"] != lp
        kk = kat[d["kind"]] if d["kind"] in kat else kat["material"]
        kk["limit_sum"] += d["limit_sum"]
        kk["fakt_sum"] += d["fakt_sum"]
        kk["prognoz"] += d["prognoz"]
        kk["farq"] += d["farq"]
        if d["narx_ozgargan"]:
            kk["ozgargan"] += 1
    umum = {n: sum((kat[k][n] for k in KINDS), Decimal("0"))
            for n in ("limit_sum", "fakt_sum", "prognoz", "farq")}
    umum["ozgargan"] = sum(kat[k]["ozgargan"] for k in KINDS)
    umum["yoq"] = sum(yoq.values(), Decimal("0"))   # limitda yo'q qatorlar summasi
    # Ishlarni JORIY narxda tugatish uchun jami kerak bo'ladigan pul
    umum["kerak"] = umum["fakt_sum"] + umum["prognoz"]
    return qat, kat, umum


def _farq_str(v):
    """Narx farqi matni: +1 200 000 / −300 000 / — (nol)."""
    if not v:
        return "—"
    return ("+" if v > 0 else "−") + _money(abs(v))


@login_required
def dashboard(request):
    firma_id = request.GET.get("firma") or ""
    if not firma_id.isdigit():      # noto'g'ri qiymat 500 bermasin
        firma_id = ""
    obyektlar = visible_projects(request.user).order_by("code")
    if firma_id:
        obyektlar = obyektlar.filter(firma_id=firma_id)
    qatorlar = []
    jami_limit = Decimal("0.00")
    jami_mat = Decimal("0.00")
    jami_lab = Decimal("0.00")
    jami_mach = Decimal("0.00")
    jami_oth = Decimal("0.00")
    from .models import obyekt_yigindilar
    _Y = obyekt_yigindilar(obyektlar)
    for p in obyektlar:
        split = _Y[p.pk]["kind"]
        mat, lab, mach, oth = split["material"], split["labor"], split["machinery"], split["other"]
        sarf = mat + lab + mach + oth
        limit = p.budget_total or Decimal("0.00")
        holat = _holat(limit, sarf)
        foiz = float(sarf) / float(limit) * 100 if limit else 0
        jami_limit += limit
        jami_mat += mat
        jami_lab += lab
        jami_mach += mach
        jami_oth += oth
        qatorlar.append({
            "obj": p,
            "limit_str": _money(limit),
            "sarf_str": _money(sarf),
            "mat_str": _money(mat),
            "lab_str": _money(lab),
            "mach_str": _money(mach),
            "oth_str": _money(oth),
            "qoldiq_str": _money(limit - sarf),
            "holat": holat,
            "rang": RANGLAR.get(holat, "#6c757d"),
            "holat_class": HOLAT_CLASS.get(holat, ""),
            "foiz": round(foiz),
            "foiz_bar": min(round(foiz), 100),
        })
    jami_sarf = jami_mat + jami_lab + jami_mach + jami_oth

    from django.db.models import DecimalField, ExpressionWrapper, F, Sum
    _LT = ExpressionWrapper(F("quantity") * F("unit_price"),
                            output_field=DecimalField(max_digits=20, decimal_places=2))

    _lq = visible_projects(request.user)
    if firma_id:
        _lq = _lq.filter(firma_id=firma_id)
    la = _lq.aggregate(m=Sum("limit_material"), l=Sum("limit_labor"),
                       k=Sum("limit_machinery"), o=Sum("limit_other"))
    lim_mat = la["m"] or Decimal("0")
    lim_lab = la["l"] or Decimal("0")
    lim_mach = la["k"] or Decimal("0")
    lim_oth = la["o"] or Decimal("0")

    def _pct(part, whole):
        return round(float(part) / float(whole) * 100) if whole else 0

    foiz_used = _pct(jami_sarf, jami_limit)
    foiz_bar = min(foiz_used, 100)

    tot = jami_limit or Decimal("1")
    c1 = float(lim_mat) / float(tot) * 100
    c2 = c1 + float(lim_lab) / float(tot) * 100
    c3 = c2 + float(lim_mach) / float(tot) * 100
    donut = ("conic-gradient(#0a6ed1 0 %.2f%%, #5899da %.2f%% %.2f%%, "
             "#89c3f0 %.2f%% %.2f%%, #c7d9ee %.2f%% 100%%)"
             % (c1, c1, c2, c2, c3, c3))

    kategoriyalar = [
        {"nom": "Material", "rang": "#2563eb", "limit_str": _money(lim_mat), "sarf_str": _money(jami_mat),
         "pct": _pct(jami_mat, lim_mat), "bar": min(_pct(jami_mat, lim_mat), 100)},
        {"nom": "Ish haqi", "rang": "#3b82f6", "limit_str": _money(lim_lab), "sarf_str": _money(jami_lab),
         "pct": _pct(jami_lab, lim_lab), "bar": min(_pct(jami_lab, lim_lab), 100)},
        {"nom": "Mashina chasti", "rang": "#8b5cf6", "limit_str": _money(lim_mach), "sarf_str": _money(jami_mach),
         "pct": _pct(jami_mach, lim_mach), "bar": min(_pct(jami_mach, lim_mach), 100)},
        {"nom": "Ko'zda tutilmagan", "rang": "#f59e0b", "limit_str": _money(lim_oth), "sarf_str": _money(jami_oth),
         "pct": _pct(jami_oth, lim_oth), "bar": min(_pct(jami_oth, lim_oth), 100)},
    ]

    # haftalik sarf grafigi (tasdiqlangan)
    _cq = WeeklyRequestItem.objects.filter(
        request__status="approved", request__project__in=visible_projects(request.user))
    if firma_id:
        _cq = _cq.filter(request__project__firma_id=firma_id)
    crows = list(
        _cq.values("request__week_start").annotate(s=Sum(_LT)).order_by("request__week_start")
    )
    W, H = 640, 170
    pts, labels = [], []
    if crows:
        vals = [float(r["s"] or 0) for r in crows]
        labels = [r["request__week_start"].strftime("%d.%m") for r in crows]
        mx = max(vals) or 1
        n = len(vals)
        for i, v in enumerate(vals):
            x = (i / max(n - 1, 1)) * W
            y = H - (v / mx) * (H - 26) - 10
            pts.append((round(x, 1), round(y, 1)))
        if n == 1:
            pts = [(0, pts[0][1]), (W, pts[0][1])]
            labels = [labels[0], labels[0]]
    line_pts = " ".join(f"{x},{y}" for x, y in pts)
    area_pts = (f"0,{H} " + line_pts + f" {W},{H}") if pts else ""

    # amaliy ko'rsatkichlar (jiddiy nazorat)
    from .models import Firma, LimitChangeRequest
    oshgan = sum(1 for q in qatorlar if q["holat"] == "RUXSAT KERAK")
    yaqin = sum(1 for q in qatorlar if q["holat"] == "limitga yaqin")
    limitli = sum(1 for q in qatorlar if q["obj"].budget_total > 0)
    _pq = LimitChangeRequest.objects.filter(
        status__in=LIM_JARAYON, project__in=visible_projects(request.user))
    if firma_id:
        _pq = _pq.filter(project__firma_id=firma_id)
    pending_count = _pq.count()
    qolgan_foiz = max(0, 100 - foiz_used)

    # Chart.js uchun raqamli ma'lumot
    cat_values = [float(lim_mat), float(lim_lab), float(lim_mach), float(lim_oth)]
    wk_labels = [r["request__week_start"].strftime("%d.%m") for r in crows]
    wk_values = [float(r["s"] or 0) for r in crows]

    _tab = request.GET.get("tab")
    if _tab == "tasdiqlar" and request.user.is_superuser:
        active_tab = "tasdiqlar"
    else:
        active_tab = "korish"
    # Tasdiqlar tabi: DIREKTOR o'z navbatini (dir) ko'radi, ADMIN o'z navbatini (adm/submitted).
    # Superuser ikkalasini ham ko'radi (avval direktor navbati).
    from .roles import is_director
    from .roles import is_snab as _is_snab_f
    # Direktor navbati: haqiqiy direktor YOKI ASOSIY admin (admin1/2 emas)
    from .roles import is_asosiy_admin as _asosiy_f
    _dir = (is_director(request.user) and not request.user.is_superuser)         or _asosiy_f(request.user)
    _adm = is_admin(request.user)
    _snb = _is_snab_f(request.user)
    _pto_f = is_pto(request.user)
    from .roles import is_prov as _is_prov_f, is_asosiy_prov
    _prv = _is_prov_f(request.user)
    tas_lim, tas_wk, tas_lim2, tas_wk2 = [], [], [], []
    tas_lim_s, tas_lim_p2, tas_lim_pr = [], [], []
    tas_wk_s, tas_wk_p2, tas_wk_pr = [], [], []
    if _prv or _adm:
        tas_lim_pr, tas_wk_pr = _tasdiqlar_data("prov", user=request.user)
    if _dir:
        tas_lim, tas_wk = _tasdiqlar_data("dir", user=request.user)
    if _adm:
        tas_lim2, tas_wk2 = _tasdiqlar_data("adm", user=request.user)
    if _snb or _adm:
        tas_lim_s, tas_wk_s = _tasdiqlar_data("snab", user=request.user)
    if _pto_f:
        tas_lim_p2, tas_wk_p2 = _tasdiqlar_data("pto2", user=request.user)
    # Grafik tasdiqlash navbatlari (limit zanjiriga o'xshash)
    from .models import GrafikTasdiq
    graf_dir, graf_adm = [], []
    if _dir:
        graf_dir = list(GrafikTasdiq.objects.filter(
            status="dir", project__in=visible_projects(request.user)
        ).select_related("project", "yubordi"))
    if _adm:
        graf_adm = list(GrafikTasdiq.objects.filter(
            status="adm", project__in=visible_projects(request.user)
        ).select_related("project", "yubordi", "director_by"))
    tas_show = _dir or _adm or _snb or _prv or bool(tas_lim_p2) or bool(tas_wk_p2)
    from django.utils import timezone as _tz
    _h = _tz.localtime().hour
    greeting = "Xayrli tong" if _h < 12 else ("Xayrli kun" if _h < 18 else "Xayrli kech")

    kontekst = {
        "is_pto": is_pto(request.user),
        "active_tab": active_tab,
        "tas_lim": tas_lim, "tas_wk": tas_wk,          # direktor navbati
        "tas_lim2": tas_lim2, "tas_wk2": tas_wk2,      # admin navbati
        "tas_lim_s": tas_lim_s,                        # snabjeniye navbati
        "tas_lim_p2": tas_lim_p2,                      # PTO xulosasi navbati
        "tas_lim_pr": tas_lim_pr,                      # proverchik navbati
        "tas_wk_s": tas_wk_s, "tas_wk_p2": tas_wk_p2, "tas_wk_pr": tas_wk_pr,   # haftalik zanjir navbatlari
        "is_director": _dir, "is_snab": _snb, "is_prov": _prv, "tas_show": tas_show,
        "prov_asosiy": is_asosiy_prov(request.user),
        "graf_dir": graf_dir, "graf_adm": graf_adm,    # grafik tasdiqlash navbatlari
        "tas_count": (len(tas_lim) + len(tas_wk) + len(tas_lim2) + len(tas_wk2)
                      + len(tas_lim_s) + len(tas_lim_p2) + len(tas_lim_pr) + len(graf_dir) + len(graf_adm)
                      + len(tas_wk_s) + len(tas_wk_p2) + len(tas_wk_pr)),
        "greeting": greeting,
        "qatorlar": qatorlar,
        "cat_values": cat_values,
        "wk_labels": wk_labels,
        "wk_values": wk_values,
        "jami_limit_str": _money(jami_limit),
        "jami_sarf_str": _money(jami_sarf),
        "jami_mat_str": _money(jami_mat),
        "jami_lab_str": _money(jami_lab),
        "jami_mach_str": _money(jami_mach),
        "jami_oth_str": _money(jami_oth),
        "jami_qoldiq_str": _money(jami_limit - jami_sarf),
        "soni": len(qatorlar),
        "foiz_used": foiz_used,
        "foiz_bar": foiz_bar,
        "qolgan_foiz": qolgan_foiz,
        "donut": donut,
        "kategoriyalar": kategoriyalar,
        "line_pts": line_pts,
        "area_pts": area_pts,
        "chart_w": W,
        "chart_h": H,
        "chart_labels": labels,
        "obyektlar_soni": len(qatorlar),
        "firmalar_soni": 1 if firma_id else visible_firmas(request.user).count(),
        "limitli": limitli,
        "oshgan": oshgan,
        "yaqin": yaqin,
        "pending_count": pending_count,
        "firmalar": visible_firmas(request.user).order_by("name"),
        "sel_firma": firma_id,
    }
    return render(request, "projects/dashboard.html", kontekst)


@login_required
def firmalar(request):
    """Obyektlar — tablar: «Hammasi» + har bir firma alohida."""
    firmalar_data = []
    all_objs = []
    tot_limit = Decimal("0")
    tot_sarf = Decimal("0")
    from .models import obyekt_yigindilar as _oy
    _Yf = _oy(visible_projects(request.user))
    for f in visible_firmas(request.user).order_by("name"):
        objs = []
        f_limit = Decimal("0")
        f_sarf = Decimal("0")
        for pr in visible_projects(request.user).filter(firma=f).order_by("code"):
            bt = pr.budget_total
            srf = _Yf.get(pr.pk, {}).get("sarf", Decimal("0"))
            f_limit += bt
            f_sarf += srf
            foiz = round(float(srf) / float(bt) * 100) if bt else 0
            row = {
                "obj": pr,
                "firma_name": f.name,
                "status": pr.get_status_display(),
                "status_key": pr.status,
                "location": pr.location or "",
                "start": pr.start_date,
                "end": pr.end_date,
                "has_limit": bt > 0,
                "limit_str": _money(bt),
                "sarf_str": _money(srf),
                "qoldiq_str": _money(bt - srf),
                "foiz": foiz,
                "bar": min(foiz, 100),
                "rang": "#ba1a1a" if foiz > 100 else ("#b45309" if foiz >= 90 else "#2563eb"),
            }
            objs.append(row)
            all_objs.append(row)
        tot_limit += f_limit
        tot_sarf += f_sarf
        firmalar_data.append({
            "obj": f,
            "objects": objs,
            "count": len(objs),
            "limit_str": _money(f_limit),
            "sarf_str": _money(f_sarf),
            "qoldiq_str": _money(f_limit - f_sarf),
            "foiz": round(float(f_sarf) / float(f_limit) * 100) if f_limit else 0,
        })
    hammasi = {
        "objects": all_objs,
        "count": len(all_objs),
        "limit_str": _money(tot_limit),
        "sarf_str": _money(tot_sarf),
        "qoldiq_str": _money(tot_limit - tot_sarf),
    }
    return render(request, "projects/firmalar.html", {
        "firmalar": firmalar_data,
        "hammasi": hammasi,
        "soni": len(firmalar_data),
    })


def _qty(x):
    """Miqdorni chiroyli format qilish (3 xonagacha, ortiqcha nol tashlanadi)."""
    x = Decimal(x or 0)
    s = f"{x:,.3f}".replace(",", " ")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


@login_required
def m29(request):
    """M-29 — material sarfining reja-fakt tahlili.

    Reja  = tasdiqlangan haftalik so'rovlardagi material qatorlari.
    Fakt  = ombordan chiqim (rasxod) — StockMovement OUT.
    Material nomi + o'lchov birligi bo'yicha birlashtiriladi.
    """
    from django.db.models import DecimalField, ExpressionWrapper, F, Sum
    from ombor.models import StockMovement

    _LT = ExpressionWrapper(F("quantity") * F("unit_price"),
                            output_field=DecimalField(max_digits=20, decimal_places=2))

    firma_id = request.GET.get("firma") or ""
    obj_id = request.GET.get("obyekt") or ""
    d1 = request.GET.get("d1") or ""
    d2 = request.GET.get("d2") or ""

    projs = visible_projects(request.user).order_by("code")
    if firma_id:
        projs = projs.filter(firma_id=firma_id)

    # --- REJA: tasdiqlangan haftalik material qatorlari ---
    reja_q = WeeklyRequestItem.objects.filter(
        request__status=WeeklyRequest.Status.APPROVED,
        kind=WeeklyRequestItem.Kind.MATERIAL,
        request__project__in=visible_projects(request.user),
    )
    if firma_id:
        reja_q = reja_q.filter(request__project__firma_id=firma_id)
    if obj_id:
        reja_q = reja_q.filter(request__project_id=obj_id)
    if d1:
        reja_q = reja_q.filter(request__week_start__gte=d1)
    if d2:
        reja_q = reja_q.filter(request__week_start__lte=d2)
    reja_rows = reja_q.values("name", "unit").annotate(qty=Sum("quantity"), summa=Sum(_LT))

    # --- FAKT: ombor chiqimi (OUT — miqdor/summa manfiy ishorali) ---
    fakt_q = StockMovement.objects.filter(
        direction=StockMovement.OUT, project__in=visible_projects(request.user))
    if firma_id:
        fakt_q = fakt_q.filter(project__firma_id=firma_id)
    if obj_id:
        fakt_q = fakt_q.filter(project_id=obj_id)
    if d1:
        fakt_q = fakt_q.filter(date__gte=d1)
    if d2:
        fakt_q = fakt_q.filter(date__lte=d2)
    fakt_rows = fakt_q.values("material__name", "material__unit").annotate(
        qty=Sum("quantity"), summa=Sum("total_cost"))

    def _key(n, u):
        return ((n or "").strip().lower(), (u or "").strip().lower())

    data = {}

    def _slot(name, unit):
        k = _key(name, unit)
        if k not in data:
            data[k] = {"name": name or "—", "unit": unit or "—",
                       "reja_qty": Decimal("0"), "reja_sum": Decimal("0"),
                       "fakt_qty": Decimal("0"), "fakt_sum": Decimal("0")}
        return data[k]

    for r in reja_rows:
        s = _slot(r["name"], r["unit"])
        s["reja_qty"] += r["qty"] or Decimal("0")
        s["reja_sum"] += r["summa"] or Decimal("0")
    for r in fakt_rows:
        s = _slot(r["material__name"], r["material__unit"])
        s["fakt_qty"] += abs(r["qty"] or Decimal("0"))
        s["fakt_sum"] += abs(r["summa"] or Decimal("0"))

    rows = []
    t_reja = Decimal("0")
    t_fakt = Decimal("0")
    n_perer = 0
    n_ekon = 0
    for k in sorted(data.keys()):
        d = data[k]
        farq = d["fakt_sum"] - d["reja_sum"]
        if d["reja_sum"] == 0 and d["fakt_sum"] > 0:
            holat, rang = "Rejasiz sarf", "#b91c1c"
            n_perer += 1
        elif farq > 0:
            holat, rang = "Pererasxod", "#b91c1c"
            n_perer += 1
        elif farq < 0:
            holat, rang = "Ekonomiya", "#15803d"
            n_ekon += 1
        else:
            holat, rang = "Norma", "#64748b"
        t_reja += d["reja_sum"]
        t_fakt += d["fakt_sum"]
        rows.append({
            "name": d["name"],
            "unit": d["unit"],
            "reja_qty": _qty(d["reja_qty"]),
            "reja_sum": _money(d["reja_sum"]),
            "fakt_qty": _qty(d["fakt_qty"]),
            "fakt_sum": _money(d["fakt_sum"]),
            "farq_sum": _money(abs(farq)),
            "farq_neg": farq < 0,
            "farq_zero": farq == 0,
            "holat": holat,
            "rang": rang,
        })

    farq_jami = t_fakt - t_reja
    sel_obj = projs.filter(id=obj_id).first() if obj_id else None

    kontekst = {
        "rows": rows,
        "soni": len(rows),
        "reja_jami": _money(t_reja),
        "fakt_jami": _money(t_fakt),
        "farq_jami": _money(abs(farq_jami)),
        "farq_neg": farq_jami < 0,
        "farq_zero": farq_jami == 0,
        "n_perer": n_perer,
        "n_ekon": n_ekon,
        "firmalar": visible_firmas(request.user).order_by("name"),
        "obyektlar": projs,
        "sel_firma": firma_id,
        "sel_obj": obj_id,
        "sel_obj_name": (f"{sel_obj.code} — {sel_obj.name}") if sel_obj else "",
        "d1": d1,
        "d2": d2,
    }
    return render(request, "projects/m29.html", kontekst)


@login_required
def virtual_ofis(request):
    """Virtual ofis — har biri o'z vazifasiga ega avtomat agentlar.
    Har agent ERP ma'lumotini skanerlaydi va vazifalar/ogohlantirishlar chiqaradi."""
    from django.utils import timezone
    from django.urls import reverse as _rev
    from ombor.models import StockBalance
    today = timezone.localdate()

    agents = []

    def _u_obj(pid):
        return _rev("project_detail", args=[pid])

    # 1) Limit nazoratchisi
    tasks = []
    from .models import obyekt_yigindilar as _oy2
    _vp0 = visible_projects(request.user)
    _Ya = _oy2(_vp0)
    for p in _vp0.select_related("firma"):
        lim = p.budget_total
        if lim <= 0:
            continue
        sarf = _Ya[p.pk]["sarf"]
        if sarf > lim:
            tasks.append({"text": f"{p.code} — {p.name}: limitdan oshgan ({_money(sarf)} / {_money(lim)})",
                          "level": "bad", "url": _u_obj(p.id)})
        elif float(sarf) / float(lim) >= 0.9:
            tasks.append({"text": f"{p.code} — {p.name}: limitga yaqin ({round(float(sarf)/float(lim)*100)}%)",
                          "level": "warn", "url": _u_obj(p.id)})
    agents.append({"name": "Limit nazoratchisi", "role": "Obyekt limitlarini kuzatadi",
                   "av": "L", "tasks": tasks})

    # 2) Tasdiq kotibi
    tasks = []
    _vp = visible_projects(request.user)
    lc = LimitChangeRequest.objects.filter(status__in=LIM_JARAYON, project__in=_vp).count()
    wc = WeeklyRequest.objects.filter(status__in=WK_JARAYON, project__in=_vp).count()
    if lc:
        tasks.append({"text": f"{lc} ta limit o'zgartirish so'rovi tasdiq kutmoqda",
                      "level": "warn", "url": _rev("tasdiqlar")})
    if wc:
        tasks.append({"text": f"{wc} ta haftalik so'rov tasdiq kutmoqda",
                      "level": "warn", "url": _rev("tasdiqlar")})
    agents.append({"name": "Tasdiq kotibi", "role": "Tasdiq kutayotgan so'rovlarni yig'adi",
                   "av": "T", "tasks": tasks})

    # 3) Ombor nazoratchisi
    tasks = []
    low = (StockBalance.objects.select_related("material", "warehouse")
           .filter(quantity__lte=0))
    if not request.user.is_superuser:
        low = low.filter(warehouse__project__in=visible_projects(request.user))
    low = low.order_by("warehouse__name")[:30]
    for b in low:
        tasks.append({"text": f"{b.material.name} — {b.warehouse.kod_nom}: qoldiq {_qty(b.quantity)} (tugagan)",
                      "level": "bad", "url": _rev("ombor")})
    agents.append({"name": "Ombor nazoratchisi", "role": "Tugagan/kam material qoldiqlarini topadi",
                   "av": "O", "tasks": tasks})

    # 4) Muddat agenti
    tasks = []
    for p in visible_projects(request.user).filter(status="active").exclude(end_date__isnull=True):
        if p.end_date < today:
            tasks.append({"text": f"{p.code} — {p.name}: muddati o'tgan ({p.end_date:%d.%m.%Y})",
                          "level": "bad", "url": _u_obj(p.id)})
        elif (p.end_date - today).days <= 14:
            tasks.append({"text": f"{p.code} — {p.name}: {(p.end_date - today).days} kun qoldi ({p.end_date:%d.%m.%Y})",
                          "level": "warn", "url": _u_obj(p.id)})
    agents.append({"name": "Muddat agenti", "role": "Obyekt muddatlarini nazorat qiladi",
                   "av": "M", "tasks": tasks})

    # 5) Sarf tahlilchisi (kategoriya bo'yicha pererasxod)
    tasks = []
    KAT = [("Material", "limit_material", "material"), ("Ish haqi", "limit_labor", "labor"),
           ("Mashina chasti", "limit_machinery", "machinery"),
           ("Ko'zda tutilmagan", "limit_other", "other")]
    for p in _vp0:
        if p.budget_total <= 0:
            continue
        split = _Ya[p.pk]["kind"]
        for nom, lf, key in KAT:
            klim = getattr(p, lf) or Decimal("0")
            ksarf = split[key]
            if klim > 0 and ksarf > klim:
                tasks.append({"text": f"{p.code}: «{nom}» pererasxod ({_money(ksarf)} / {_money(klim)})",
                              "level": "bad", "url": _u_obj(p.id)})
    agents.append({"name": "Sarf tahlilchisi", "role": "Kategoriya bo'yicha pererasxodni aniqlaydi",
                   "av": "S", "tasks": tasks})

    # umumiy holat
    for a in agents:
        a["count"] = len(a["tasks"])
        a["bad"] = sum(1 for t in a["tasks"] if t["level"] == "bad")
        a["ok"] = a["count"] == 0
    jami_vazifa = sum(a["count"] for a in agents)
    band_agent = sum(1 for a in agents if a["count"] > 0)

    return render(request, "projects/virtual_ofis.html", {
        "agents": agents, "jami_vazifa": jami_vazifa,
        "band_agent": band_agent, "agent_soni": len(agents),
    })


def _hafta_jadval_guruhlar(w, lim_cache, fakt_cache):
    """Bitta haftalik so'rov uchun limit-jadval ko'rinishi:
    UMUMIY | BAJARILGAN | QOLGAN | shu hafta — bo'limlab, blok yakunlari bilan.
    lim_cache/fakt_cache — loyiha bo'yicha keshlangan xaritalar (ko'p so'rovda tez)."""
    pid = w.limit_id or w.project_id
    if pid not in lim_cache:
        lk = {}
        for li in (w.limit.items.all() if w.limit_id else w.project.limit_items.all()):
            k = _lj_key(li.name, li.bolim)
            d = lk.setdefault(k, {"qty": Decimal("0"), "sum": Decimal("0"),
                                  "price": li.unit_price})
            d["qty"] += li.quantity - (li.boshlangich_qty or 0)
            d["sum"] += li.total
        lim_cache[pid] = lk
        fk_ = {}
        for fi in WeeklyRequestItem.objects.filter(
                **({"request__limit_id": pid} if w.limit_id else {"request__project_id": pid}),
                request__status="approved"):
            k = _lj_key(fi.name, fi.bolim)
            d = fk_.setdefault(k, {"qty": Decimal("0"), "sum": Decimal("0")})
            d["qty"] += fi.quantity
            d["sum"] += fi.total
        fakt_cache[pid] = fk_
    lk, fkm = lim_cache[pid], fakt_cache[pid]
    kor, gr, tartib = set(), {}, []
    u_j = f_j = w_j = Decimal("0")
    for it in w.items.all():
        k = _lj_key(it.name, it.bolim)
        l = lk.get(k)
        f = fkm.get(k, {"qty": Decimal("0"), "sum": Decimal("0")})
        split = k in kor
        kor.add(k)
        kal = (it.bolim or "").strip()
        if kal not in gr:
            gr[kal] = {"bolim": kal, "rows": [], "u": Decimal("0"),
                       "f": Decimal("0"), "w": Decimal("0")}
            tartib.append(kal)
        g = gr[kal]
        row = {
            "name": it.name, "izoh": it.note, "unit": it.unit or "—",
            "split": split, "lim_bor": l is not None,
            "u_qty": _qty(l["qty"]) if l and not split else "",
            "u_price": _money(l["price"]) if l and not split else "",
            "u_sum": _money(l["sum"]) if l and not split else "",
            "f_qty": _qty(f["qty"]) if l and not split else "",
            "f_sum": _money(f["sum"]) if l and not split else "",
            "q_qty": _qty(l["qty"] - f["qty"]) if l and not split else "",
            "q_sum": _money(l["sum"] - f["sum"]) if l and not split else "",
            "w_qty": _qty(it.quantity), "w_price": _money(it.unit_price),
            "w_sum": _money(it.total),
        }
        g["rows"].append(row)
        u0 = l["sum"] if l and not split else Decimal("0")
        f0 = f["sum"] if l and not split else Decimal("0")
        g["u"] += u0; g["f"] += f0; g["w"] += it.total
        u_j += u0; f_j += f0; w_j += it.total
    groups = []
    for kal in tartib:
        g = gr[kal]
        groups.append({"bolim": kal, "rows": g["rows"],
                       "u_str": _money(g["u"]), "f_str": _money(g["f"]),
                       "q_str": _money(g["u"] - g["f"]), "w_str": _money(g["w"])})
    return {"groups": groups, "u_jami_str": _money(u_j), "f_jami_str": _money(f_j),
            "q_jami_str": _money(u_j - f_j), "w_jami_str": _money(w_j)}


@login_required
def haftalik_tarix(request):
    """Tasdiqdan o'tgan (eski) haftalik limitlar tarixi — filtr bilan ko'rish."""
    firma_id = request.GET.get("firma") or ""
    obj_id = request.GET.get("obyekt") or ""
    d1 = request.GET.get("d1") or ""
    d2 = request.GET.get("d2") or ""
    holat = request.GET.get("holat") or "approved"  # approved / submitted / draft / all

    projs = visible_projects(request.user).order_by("code")
    if firma_id:
        projs = projs.filter(firma_id=firma_id)

    qs = WeeklyRequest.objects.filter(
        project__in=visible_projects(request.user)).select_related(
        "project", "project__firma", "limit", "created_by", "approved_by").prefetch_related("items")
    if holat in ("approved", "submitted", "draft"):
        qs = qs.filter(status=holat)
    elif holat == "jarayon":
        qs = qs.filter(status__in=WK_JARAYON)
    if firma_id:
        qs = qs.filter(project__firma_id=firma_id)
    if obj_id:
        qs = qs.filter(project_id=obj_id)
    if d1:
        qs = qs.filter(week_start__gte=d1)
    if d2:
        qs = qs.filter(week_start__lte=d2)
    qs = qs.order_by("-week_start", "-id")

    rows = []
    t_jami = Decimal("0")
    lim_cache, fakt_cache = {}, {}
    for w in qs[:200]:
        s = {k: Decimal("0") for k in KINDS}
        cnt = 0
        for it in w.items.all():
            s[it.kind] = s.get(it.kind, Decimal("0")) + it.total
            cnt += 1
        jami = s["material"] + s["labor"] + s["machinery"] + s["other"]
        t_jami += jami
        rows.append({
            "detal": _hafta_jadval_guruhlar(w, lim_cache, fakt_cache),
            "id": w.id, "obj": w.project,
            "firma": w.project.firma.name if w.project.firma_id else "—",
            "week_start": w.week_start, "week_end": w.week_end, "number": w.number,
            "note": w.note,
            "kiritdi": w.created_by.username if w.created_by_id else "—",
            "kiritilgan": w.created_at,
            "tasdiqladi": w.approved_by.username if w.approved_by_id else "",
            "tasdiq_sana": w.approved_at,
            "is_approved": w.status == "approved",
            "mat_str": _money(s["material"]), "lab_str": _money(s["labor"]),
            "mach_str": _money(s["machinery"]), "oth_str": _money(s["other"]),
            "jami_str": _money(jami), "soni": cnt,
        })

    sel_obj = projs.filter(id=obj_id).first() if obj_id else None
    kontekst = {
        "rows": rows, "soni": len(rows), "jami_str": _money(t_jami),
        "firmalar": visible_firmas(request.user).order_by("name"), "obyektlar": projs,
        "sel_firma": firma_id, "sel_obj": obj_id,
        "sel_obj_name": (f"{sel_obj.code} — {sel_obj.name}") if sel_obj else "",
        "d1": d1, "d2": d2, "holat": holat,
    }
    return render(request, "projects/haftalik_tarix.html", kontekst)


@login_required
def project_detail(request, pk):
    """Obyekt sahifasi — ESKI (Sirdaryo) qolip o'chirildi: hamma obyekt uchun yagona
    «Ал-Мотрудий» limit jadvali ochiladi. URL nomi saqlangan (eski havolalar, redirectlar)."""
    _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
    return redirect("limit_jadval", pk=pk)


@login_required
def limit_narx_yangilash(request, pk):
    """Narx o'zgarganda umumiy limitni JORIY narxda yangilash so'rovi.

    Miqdorlar o'zgarmaydi — faqat narxi o'zgargan qatorlarning narxi oxirgi
    tasdiqlangan haftalik narxiga ko'tariladi/tushiriladi. Odatdagi zanjirdan
    o'tadi (snab → PTO2 → direktor → admin); admin bo'lsa to'g'ridan-to'g'ri.
    Narx hech qachon AVTOMATIK o'zgarmaydi — bu tasdiqlash zanjirini chetlab o'tish bo'lardi.
    """
    p = _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
    lim = _lj_joriy(p, request)
    if not is_pto(request.user):
        raise PermissionDenied("Limit so'rovini faqat PTO yuboradi.")
    if request.method != "POST":
        return redirect("project_detail", pk=pk)
    if lim.requests.filter(status__in=LIM_JARAYON).exists():
        messages.error(request, "Tasdiq kutilayotgan so'rov bor — yangi so'rov yuborib bo'lmaydi.")
        return redirect("project_detail", pk=pk)

    joriy = _joriy_narxlar(p)
    items, ozgardi, sums = [], [], {k: Decimal("0") for k in KINDS}
    for li in lim.items.all().order_by("id"):
        narx = joriy.get(_nom_kalit(li.kind, li.name), li.unit_price)
        if narx != li.unit_price:
            ozgardi.append(f"{li.name} {_money(li.unit_price)} → {_money(narx)}")
        items.append({"kind": li.kind, "name": li.name, "unit": li.unit,
                      "quantity": li.quantity, "unit_price": narx, "note": li.note,
                      "bolim": li.bolim, "masul": li.masul})
        sums[li.kind if li.kind in sums else "material"] += (li.quantity * narx).quantize(Decimal("0.01"))
    if not ozgardi:
        messages.info(request, "Narxi o'zgargan qator yo'q — limit joriy narxlarda.")
        return redirect("project_detail", pk=pk)

    reason = ("Narx o'zgarishi (joriy narxda yangilash): " + "; ".join(ozgardi))[:255]
    if is_admin(request.user):
        with transaction.atomic():
            sync_limit_items(lim, items)
            p.recompute_limits()
        messages.success(request, f"Limit joriy narxda yangilandi (admin). Umumiy limit: {_money(p.budget_total)}. "
                                  f"O'zgargan: {len(ozgardi)} qator.")
    else:
        _st = _limit_boshlangich(p)
        with transaction.atomic():
            req = LimitChangeRequest.objects.create(
                project=p, limit=lim,
                old_material=p.limit_material, old_labor=p.limit_labor,
                old_machinery=p.limit_machinery, old_other=p.limit_other,
                new_material=sums["material"], new_labor=sums["labor"],
                new_machinery=sums["machinery"], new_other=sums["other"],
                reason=reason, requested_by=request.user, status=_st,
            )
            LimitChangeItem.objects.bulk_create([LimitChangeItem(request=req, **it) for it in items])
        messages.success(request, f"{len(ozgardi)} qator narxi joriy narxga yangilanib so'rov yuborildi. "
                                  + _limit_yubor_xabar(_st))
    return redirect("project_detail", pk=pk)


def _qayt_kod_yubor(request, tur, pk, obj_nomi, sabab, matn=None):
    """PTOga qaytarish uchun 6 xonali tasdiqlash kodini Telegramga yuboradi.
    Kod sessiyada saqlanadi (10 daqiqa, 3 urinish)."""
    import secrets
    import time
    kod = "".join(str(secrets.randbelow(10)) for _ in range(6))
    request.session["qayt_kod"] = {"kod": kod, "tur": tur, "pk": pk,
                                   "sabab": sabab, "ts": time.time(), "urin": 0}
    from .auth2fa import admin_chatlar, tg_send
    prof = getattr(request.user, "profile", None)
    chat = (getattr(prof, "telegram_chat_id", "") or "") if prof else ""
    chats = [chat] if chat else list(admin_chatlar())
    matn = (f"🔐 Tasdiqlash kodi: {kod}\n"
            + (matn or f"{obj_nomi} — {tur}ni PTOga qaytarish.") + "\n"
            f"So'radi: {request.user.username}. Kod 10 daqiqa amal qiladi.")
    yubordi = False
    for c in chats:
        try:
            if tg_send(c, matn, timeout=8, urinish=2):   # foydalanuvchi kutib turibdi — uzoq osilmasin
                yubordi = True
        except Exception:
            pass
    return yubordi


def _qayt_kod_tekshir(request, tur, pk, kod):
    """Sessiyadagi kod bilan solishtiradi. (sabab, xato) qaytaradi."""
    import time
    d = request.session.get("qayt_kod") or {}
    if d.get("tur") != tur or d.get("pk") != pk:
        return None, "Kod so'ralmagan — jarayonni qaytadan boshlang."
    if time.time() - d.get("ts", 0) > 600:
        request.session.pop("qayt_kod", None)
        return None, "Kod muddati o'tdi (10 daqiqa) — qaytadan boshlang."
    if d.get("urin", 0) >= 3:
        request.session.pop("qayt_kod", None)
        return None, "3 marta xato kiritildi — qaytadan boshlang."
    import secrets as _sec
    if not _sec.compare_digest(kod, d.get("kod", "")):
        d["urin"] = d.get("urin", 0) + 1
        request.session["qayt_kod"] = d
        return None, "Kod noto'g'ri — Telegramdagi kodni tekshirib qayta kiriting."
    sabab = d.get("sabab", "")
    request.session.pop("qayt_kod", None)
    return sabab, ""


@login_required
def limit_return_pto(request, pk):
    """ADMIN tasdiqlangan limitni PTOga QAYTARADI (kamchilik topilganda) —
    Telegram tasdiqlash KODI bilan. Joriy limit tarkibidan «PTO xulosasi»
    bosqichida yangi so'rov ochiladi; amaldagi limit yangi tasdiqqacha o'zgarmaydi."""
    p = _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
    lim = _lj_joriy(p, request)
    if not is_admin(request.user):
        raise PermissionDenied("Limitni faqat admin qaytaradi.")
    if request.method != "POST":
        return redirect("project_detail", pk=pk)
    if lim.requests.filter(status__in=LIM_JARAYON).exists():
        messages.error(request, "Bu obyektda tasdiq kutilayotgan so'rov bor — avval uni yakunlang.")
        return redirect("project_detail", pk=pk)
    items = list(lim.items.all().order_by("id"))
    if not items:
        messages.error(request, "Limit tarkibi bo'sh — qaytaradigan narsa yo'q.")
        return redirect("project_detail", pk=pk)

    kod = (request.POST.get("kod") or "").strip()
    if not kod:
        # 1-bosqich: sabab keldi -> kod yuborib, tasdiqlash sahifasini ochamiz
        sabab = (request.POST.get("sabab") or "").strip()
        if not sabab:
            messages.error(request, "Qaytarish sababini (kamchilikni) yozing.")
            return redirect("project_detail", pk=pk)
        if not _qayt_kod_yubor(request, "limit", pk, f"{p.code} — {p.name}", sabab):
            messages.error(request, "Telegramga kod yuborib bo'lmadi — bot bog'lanishini tekshiring.")
            return redirect("project_detail", pk=pk)
        return render(request, "projects/qaytarish_kod.html", {
            "sarlavha": "Umumiy limitni PTOga qaytarish",
            "obj_nomi": f"{p.code} — {p.name}", "sabab": sabab,
            "action_url": reverse("limit_return_pto", args=[pk]),
            "bekor_url": reverse("project_detail", args=[pk]),
            "bosqich": "kod",
        })

    # 2-bosqich: kod tekshiriladi
    sabab, xato = _qayt_kod_tekshir(request, "limit", pk, kod)
    if xato:
        messages.error(request, xato)
        if "qaytadan" in xato:
            return redirect("project_detail", pk=pk)
        return render(request, "projects/qaytarish_kod.html", {
            "sarlavha": "Umumiy limitni PTOga qaytarish",
            "obj_nomi": f"{p.code} — {p.name}", "sabab": request.POST.get("sabab", ""),
            "action_url": reverse("limit_return_pto", args=[pk]),
            "bekor_url": reverse("project_detail", args=[pk]),
            "bosqich": "kod",
        })
    with transaction.atomic():
        req = LimitChangeRequest.objects.create(
            project=p, limit=lim,
            old_material=p.limit_material, old_labor=p.limit_labor,
            old_machinery=p.limit_machinery, old_other=p.limit_other,
            new_material=p.limit_material, new_labor=p.limit_labor,
            new_machinery=p.limit_machinery, new_other=p.limit_other,
            reason=(f"ADMIN QAYTARDI (kamchilik): {sabab}")[:500],
            requested_by=request.user, status="pto2",
        )
        LimitChangeItem.objects.bulk_create([
            LimitChangeItem(request=req, kind=it.kind, name=it.name, unit=it.unit,
                            quantity=it.quantity, unit_price=it.unit_price,
                            note=it.note, bolim=it.bolim, masul=it.masul)
            for it in items
        ])
    messages.success(request,
        "Limit PTOga qaytarildi — PTO «Tasdiqlar»da tahrirlab, direktorga qayta yuboradi.")
    return redirect("project_detail", pk=pk)


@login_required
def weekly_return_pto(request, pk):
    """ADMIN tasdiqlangan HAFTALIK so'rovni PTOga qaytaradi — xuddi limit kabi:
    sabab + Telegram tasdiqlash kodi. So'rov qoralamaga qaytadi, limit tiklanadi."""
    req = get_object_or_404(WeeklyRequest.objects.select_related("project"), pk=pk)
    p = _firma_yoki_403(request, req.project)
    if not is_admin(request.user):
        raise PermissionDenied("Haftalikni faqat admin qaytaradi.")
    if req.status != WeeklyRequest.Status.APPROVED:
        messages.error(request, "Faqat tasdiqlangan haftalik so'rov qaytariladi.")
        return redirect("project_detail", pk=p.pk)
    # To'lov yozilgan so'rov qaytarilmaydi — berilgan pul hisobdan yo'qolib qolmasin
    from .models import Moliya as _M
    if _M.objects.filter(item__request=req).exists():
        messages.error(request, "Bu haftalikka to'lovlar yozilgan — PTOga qaytarib bo'lmaydi. "
                                "Avval moliya masalasini buxgalter bilan hal qiling.")
        return redirect("project_detail", pk=p.pk)
    nomi = f"{p.code} — {req.week_start:%d.%m.%Y} haftalik"

    if request.method != "POST":
        return render(request, "projects/qaytarish_kod.html", {
            "sarlavha": "Haftalik so'rovni PTOga qaytarish",
            "obj_nomi": nomi, "sabab": "",
            "action_url": reverse("weekly_return_pto", args=[pk]),
            "bekor_url": reverse("project_detail", args=[p.pk]),
            "bosqich": "sabab",
        })

    kod = (request.POST.get("kod") or "").strip()
    if not kod:
        sabab = (request.POST.get("sabab") or "").strip()
        if not sabab:
            messages.error(request, "Qaytarish sababini (kamchilikni) yozing.")
            return redirect("weekly_return_pto", pk=pk)
        if not _qayt_kod_yubor(request, "haftalik", pk, nomi, sabab):
            messages.error(request, "Telegramga kod yuborib bo'lmadi — bot bog'lanishini tekshiring.")
            return redirect("project_detail", pk=p.pk)
        return render(request, "projects/qaytarish_kod.html", {
            "sarlavha": "Haftalik so'rovni PTOga qaytarish",
            "obj_nomi": nomi, "sabab": sabab,
            "action_url": reverse("weekly_return_pto", args=[pk]),
            "bekor_url": reverse("project_detail", args=[p.pk]),
            "bosqich": "kod",
        })

    sabab, xato = _qayt_kod_tekshir(request, "haftalik", pk, kod)
    if xato:
        messages.error(request, xato)
        if "qaytadan" in xato:
            return redirect("project_detail", pk=p.pk)
        return render(request, "projects/qaytarish_kod.html", {
            "sarlavha": "Haftalik so'rovni PTOga qaytarish",
            "obj_nomi": nomi, "sabab": request.POST.get("sabab", ""),
            "action_url": reverse("weekly_return_pto", args=[pk]),
            "bekor_url": reverse("project_detail", args=[p.pk]),
            "bosqich": "kod",
        })
    req.status = WeeklyRequest.Status.DRAFT
    req.approved_by = None
    req.approved_at = None
    req.reject_note = (f"ADMIN QAYTARDI (kamchilik): {sabab}")[:500]
    req.pto_notified = True
    req.save(update_fields=["status", "approved_by", "approved_at", "reject_note", "pto_notified"])
    messages.success(request,
        "Haftalik so'rov PTOga qaytarildi (qoralama) — limit tiklandi, PTO tahrirlab qayta yuboradi.")
    return redirect("project_detail", pk=p.pk)


def _tasdiqlar_data(status="dir", user=None):
    """Tasdiqlash markazi ma'lumoti: limit o'zgarishlari + haftalik so'rovlar.
    status='dir' — direktor navbati; status='adm' (limit) / 'submitted' (haftalik) — admin navbati.
    user berilsa — faqat o'sha foydalanuvchi firmasidagi so'rovlar (admin=hammasi)."""
    wk_status = "submitted" if status == "adm" else status
    _proj_qs = visible_projects(user) if user is not None else None
    lreqs = LimitChangeRequest.objects.filter(status=status)
    if _proj_qs is not None:
        lreqs = lreqs.filter(project__in=_proj_qs)
    lreqs = (lreqs.select_related("project", "project__firma", "limit", "requested_by", "director_by",
                                  "snab_by", "pto2_by", "prov_by", "decided_by")
             .prefetch_related("proposed_items", "limit__items").order_by("-created_at"))
    LI_CLS = {"material": "mat", "labor": "lab", "machinery": "mach", "other": "oth"}
    lim_list = []
    for r in lreqs:
        # Joriy tarkib bilan solishtirish — tasdiqlashdagi juftlash bilan BIR XIL tartibda,
        # shunda «yangi» / «o'zgargan» belgilari haqiqatga mos bo'ladi
        from collections import defaultdict, deque
        joriy = defaultdict(deque)
        for li in sorted(r.limit.items.all(), key=lambda x: x.id):   # prefetch'dan (order_by yangi so'rov ochardi)
            joriy[(li.kind, (li.name or "").strip().lower())].append(li)
        items = []
        for it in r.proposed_items.all():
            kalit = (it.kind, (it.name or "").strip().lower())
            navbat = joriy.get(kalit)
            eski = navbat.popleft() if navbat else None
            if eski is None:
                holat, eski_str = "yangi", ""
            elif (eski.quantity != it.quantity or eski.unit_price != it.unit_price
                  or eski.unit != it.unit or eski.note != it.note):
                holat = "ozgargan"
                eski_str = f"Oldin: {_qty(eski.quantity)} × {_money(eski.unit_price)} = {_money(eski.total)}"
            else:
                holat, eski_str = "", ""
            items.append({
                "id": it.id,
                "kind_disp": it.get_kind_display(), "cls": LI_CLS.get(it.kind, "mat"),
                "name": it.name, "unit": it.unit or "—",
                "qty_str": _qty(it.quantity), "price_str": _money(it.unit_price),
                "sum_str": _money(it.total), "izoh": it.note,
                "bolim": it.bolim, "masul": it.masul,
                "holat": holat, "eski_str": eski_str,
                "sana": it.created_at or r.created_at,
                "_qty": it.quantity, "_price": it.unit_price, "_sum": it.total,
            })
        # Limit jadvali ko'rinishi: TAKLIF (umumiy) | BAJARILGAN | QOLGAN, bo'limlab
        lfakt = {}
        for fi in WeeklyRequestItem.objects.filter(request__limit=r.limit,
                                                   request__status="approved"):
            k = _lj_key(fi.name, fi.bolim)
            d = lfakt.setdefault(k, {"qty": Decimal("0"), "sum": Decimal("0")})
            d["qty"] += fi.quantity
            d["sum"] += fi.total
        lkor = set()
        for it_ in items:
            k = _lj_key(it_["name"], it_["bolim"])
            fk = lfakt.get(k, {"qty": Decimal("0"), "sum": Decimal("0")})
            birinchi = k not in lkor
            lkor.add(k)
            it_["f_qty"] = _qty(fk["qty"]) if birinchi else ""
            it_["f_sum"] = _money(fk["sum"]) if birinchi else ""
            it_["q_qty"] = _qty(it_["_qty"] - (fk["qty"] if birinchi else Decimal("0")))
            it_["q_sum"] = _money(it_["_sum"] - (fk["sum"] if birinchi else Decimal("0")))
            it_["_f"] = fk["sum"] if birinchi else Decimal("0")
        lgr, lgr_tartib = {}, []
        for it_ in items:
            kal = (it_["bolim"] or "").strip()
            if kal not in lgr:
                lgr[kal] = {"bolim": kal, "masul": it_["masul"], "rows": [],
                            "u": Decimal("0"), "f": Decimal("0")}
                lgr_tartib.append(kal)
            g_ = lgr[kal]
            if not g_["masul"] and it_["masul"]:
                g_["masul"] = it_["masul"]
            g_["rows"].append(it_)
            g_["u"] += it_["_sum"]; g_["f"] += it_["_f"]
        lgroups = []
        lu_j = lf_j = Decimal("0")
        for kal in lgr_tartib:
            g_ = lgr[kal]
            lu_j += g_["u"]; lf_j += g_["f"]
            lgroups.append({"bolim": kal, "masul": g_["masul"], "rows": g_["rows"],
                            "u_str": _money(g_["u"]), "f_str": _money(g_["f"]),
                            "q_str": _money(g_["u"] - g_["f"])})
        lim_list.append({
            "obj": r,
            "old_str": _money(r.old_total), "new_str": _money(r.new_total),
            "cats": [
                ("Material", _money(r.old_material), _money(r.new_material)),
                ("Ish haqi", _money(r.old_labor), _money(r.new_labor)),
                ("Mashina chasti", _money(r.old_machinery), _money(r.new_machinery)),
                ("Ko'zda tutilmagan", _money(r.old_other), _money(r.new_other)),
            ],
            "items": items, "groups": lgroups,
            "u_jami_str": _money(lu_j), "f_jami_str": _money(lf_j),
            "q_jami_str": _money(lu_j - lf_j),
        })
    wreqs = WeeklyRequest.objects.filter(status=wk_status)
    if _proj_qs is not None:
        wreqs = wreqs.filter(project__in=_proj_qs)
    wreqs = (wreqs.select_related("project", "project__firma", "limit", "created_by", "director_by",
                                  "snab_by", "pto2_by", "prov_by", "approved_by")
             .prefetch_related("items", "limit__items").order_by("-week_start"))
    wk_list = []
    for w in wreqs:
        # Qatorlar — umumiy limit tarkibi bilan solishtiriladi.
        # «YANGI» = umumiy limitda yo'q (tasdiqlangach avtomatik qo'shiladi),
        # «BOSHQA NARX» = limitdagi narxdan farq qiladi.
        limit_map = {}
        for li in w.limit.items.all():
            limit_map.setdefault((li.kind, (li.name or "").strip().lower()), li)
        # Limit jadvali ustunlari uchun: (nom|bo'lim) -> umumiy / bajarilgan / qolgan
        lim_key = {}
        for li in w.limit.items.all():
            k = _lj_key(li.name, li.bolim)
            d = lim_key.setdefault(k, {"qty": Decimal("0"), "sum": Decimal("0"),
                                       "price": li.unit_price})
            d["qty"] += li.quantity - (li.boshlangich_qty or 0)
            d["sum"] += li.total
        fakt_key = {}
        for fi in WeeklyRequestItem.objects.filter(request__limit=w.limit,
                                                   request__status="approved"):
            k = _lj_key(fi.name, fi.bolim)
            d = fakt_key.setdefault(k, {"qty": Decimal("0"), "sum": Decimal("0")})
            d["qty"] += fi.quantity
            d["sum"] += fi.total
        witems = []
        kor_key = set()
        narx_farq, narx_soni = Decimal("0"), 0   # so'rov bo'yicha narx farqi jami
        for it in w.items.all():
            li = limit_map.get((it.kind, (it.name or "").strip().lower()))
            farq_str, foiz_str = "", ""
            if li is None:
                holat, eski_str = "yangi", "Umumiy limitda yo'q — tasdiqlangach ro'yxatga qo'shiladi"
            elif li.unit_price != it.unit_price:
                holat = "ozgargan"
                farq = ((it.unit_price - li.unit_price) * it.quantity).quantize(Decimal("0.01"))
                foiz = _narx_farq_foiz(li.unit_price, it.unit_price)
                narx_farq += farq
                narx_soni += 1
                farq_str = _farq_str(farq)
                foiz_str = f"{'+' if foiz > 0 else ''}{foiz}%"
                eski_str = (f"Umumiy limitdagi narx: {_money(li.unit_price)} · farq {farq_str}"
                            + (f" ({foiz_str})" if li.unit_price else ""))
            else:
                holat, eski_str = "", ""
            k = _lj_key(it.name, it.bolim)
            lk = lim_key.get(k)
            fk = fakt_key.get(k, {"qty": Decimal("0"), "sum": Decimal("0")})
            split = k in kor_key            # bir xil nom+bo'lim 2-marta (narx bo'lingan)
            kor_key.add(k)
            witems.append({
                "id": it.id,
                "kind_disp": it.get_kind_display(), "cls": LI_CLS.get(it.kind, "mat"),
                "name": it.name, "unit": it.unit or "—",
                "qty_str": _qty(it.quantity), "price_str": _money(it.unit_price),
                "sum_str": _money(it.total), "izoh": it.note,
                "bolim": it.bolim, "masul": "",
                "holat": holat, "eski_str": eski_str,
                "farq_str": farq_str, "foiz_str": foiz_str,
                "limit_narx_str": _money(li.unit_price) if li is not None else "",
                "sana": it.created_at or w.created_at,
                # Limit jadvali ustunlari (split qatorda takrorlanmaydi)
                "split": split, "lim_bor": lk is not None,
                "u_qty": _qty(lk["qty"]) if lk and not split else "",
                "u_price": _money(lk["price"]) if lk and not split else "",
                "u_sum": _money(lk["sum"]) if lk and not split else "",
                "f_qty": _qty(fk["qty"]) if lk and not split else "",
                "f_sum": _money(fk["sum"]) if lk and not split else "",
                "q_qty": _qty(lk["qty"] - fk["qty"]) if lk and not split else "",
                "q_sum": _money(lk["sum"] - fk["sum"]) if lk and not split else "",
                "_u": lk["sum"] if lk and not split else Decimal("0"),
                "_f": fk["sum"] if lk and not split else Decimal("0"),
                "_w": it.total,
            })
        # Bo'limlarga guruhlash (birinchi uchragan tartibda) + blok yakunlari
        wgr, wgr_tartib = {}, []
        for r_ in witems:
            kal = (r_["bolim"] or "").strip()
            if kal not in wgr:
                wgr[kal] = {"bolim": kal, "rows": [], "u": Decimal("0"),
                            "f": Decimal("0"), "w": Decimal("0")}
                wgr_tartib.append(kal)
            g_ = wgr[kal]
            g_["rows"].append(r_)
            g_["u"] += r_["_u"]; g_["f"] += r_["_f"]; g_["w"] += r_["_w"]
        wgroups = []
        u_j = f_j = w_j = Decimal("0")
        for kal in wgr_tartib:
            g_ = wgr[kal]
            u_j += g_["u"]; f_j += g_["f"]; w_j += g_["w"]
            wgroups.append({"bolim": kal, "rows": g_["rows"],
                            "u_str": _money(g_["u"]), "f_str": _money(g_["f"]),
                            "q_str": _money(g_["u"] - g_["f"]), "w_str": _money(g_["w"])})
        wk_list.append({
            "obj": w,
            "jami_str": _money(sum((it.total for it in w.items.all()), Decimal("0.00"))),
            "soni": len(w.items.all()),
            "items": witems, "groups": wgroups,
            "u_jami_str": _money(u_j), "f_jami_str": _money(f_j),
            "q_jami_str": _money(u_j - f_j),
            # Direktor/admin tasdiqlashdan oldin narx farqini ko'rsin
            "narx_farq_str": _farq_str(narx_farq), "narx_farq_pos": narx_farq > 0,
            "narx_soni": narx_soni,
            # Admin tasdiqlashdan OLDIN limitdan oshishini ko'rsin
            "oshgan": _limit_oshish(w.limit, _items_by_kind(w.items.all()),
                                    exclude_request_id=w.id),
        })
    return lim_list, wk_list


@login_required
def tasdiqlar(request):
    """Asosiy admin uchun tasdiqlash markazi (endi asosan «Limitlar» tab ichida)."""
    if not is_admin(request.user):
        raise PermissionDenied("Faqat asosiy admin.")
    lim_list, wk_list = _tasdiqlar_data()
    return render(request, "projects/tasdiqlar.html", {"lim_list": lim_list, "wk_list": wk_list})


def _limit_bosqich_egasi(req, status):
    """Limit so'rovining shu bosqichida javob beradigan foydalanuvchi: avval shu
    so'rovda o'sha bosqichni bajargan odam, bo'lmasa obyekt/firmaga biriktirilgani."""
    from django.contrib.auth import get_user_model
    U = get_user_model()
    p = req.project
    if status == "snab":
        return req.snab_by or (U.objects.filter(is_active=True, profile__projects=p, profile__role="snab")
                               .order_by("username").first())
    if status == "pto2":
        return req.pto2_by or req.requested_by or (
            U.objects.filter(is_active=True, profile__projects=p, profile__role="pto").order_by("username").first())
    if status == "dir":
        return req.director_by or (U.objects.filter(is_active=True, profile__firma=p.firma, profile__role="director")
                                   .order_by("username").first())
    if status == "prov":
        pr_ = _provlar()
        return req.prov_by or (pr_[0] if pr_ else None)
    return None


@login_required
def _limit_request_action_asl(request, pk):
    """Limit o'zgartirish so'rovini tasdiqlash / rad etish (admin)."""
    req = get_object_or_404(LimitChangeRequest, pk=pk)
    _firma_yoki_403(request, req.project)
    # «ack» — so'rov EGASI xabarni yopadi (admin bo'lishi shart emas)
    if request.method == "POST" and request.POST.get("action") == "ack":
        if req.requested_by_id == request.user.id:
            req.pto_notified = True
            req.save(update_fields=["pto_notified"])
        return redirect("project_detail", pk=req.project_id)
    from .roles import is_asosiy_admin, is_asosiy_prov, is_director, is_pto, is_snab
    S = LimitChangeRequest.Status
    from .roles import is_prov
    if not (is_director(request.user) or is_admin(request.user)
            or is_snab(request.user) or is_pto(request.user) or is_prov(request.user)):
        raise PermissionDenied("Faqat snabjeniye, PTO, direktor, proverchik yoki admin.")
    if request.method == "POST":
        a = request.POST.get("action")
        if a == "delitem":
            # Bitta taklif qatorini o'chirish — so'rov summalari qayta hisoblanadi.
            # Har kim FAQAT O'Z bosqichida o'chiradi: snab->snab, pto->pto2,
            # direktor->dir, admin->adm.
            _stage_ok = (req.status == S.ADM and is_admin(request.user)) or \
                        (req.status == S.DIR and
                         ((is_director(request.user) and not request.user.is_superuser)
                          or is_asosiy_admin(request.user))) or \
                        (req.status == S.SNAB and is_snab(request.user)) or \
                        (req.status == S.PTO2 and is_pto(request.user))
            it = req.proposed_items.filter(id=request.POST.get("item_id")).first()
            if req.status not in (S.SNAB, S.PTO2, S.DIR, S.ADM):
                messages.error(request, "Bu so'rov allaqachon ko'rib chiqilgan.")
            elif not _stage_ok:
                messages.error(request, "Bu so'rov sizning bosqichingizda emas.")
            elif it is None:
                messages.error(request, "Qator topilmadi.")
            elif req.proposed_items.count() <= 1:
                messages.error(request, "Oxirgi qatorni o'chirib bo'lmaydi — o'rniga so'rovni «Rad etish» qiling.")
            else:
                nom = it.name
                from django.utils import timezone as _tz2
                with transaction.atomic():
                    it.delete()
                    sums = {k: Decimal("0") for k in KINDS}
                    for x in req.proposed_items.all():
                        sums[x.kind] += x.total
                    req.new_material = sums["material"]
                    req.new_labor = sums["labor"]
                    req.new_machinery = sums["machinery"]
                    req.new_other = sums["other"]
                    req.edited_by = request.user
                    req.edited_at = _tz2.now()
                    req.save(update_fields=["new_material", "new_labor", "new_machinery",
                                            "new_other", "edited_by", "edited_at"])
                messages.info(request, f"«{nom}» qatori o'chirildi. Yangi umumiy: {_money(req.new_total)}.")
            return redirect(reverse("dashboard") + "?tab=tasdiqlar")
        from django.utils import timezone
        # SNABJENIYE bosqichi: snab → pto2 (narxlab PTOga qaytaradi)
        if a == "snab_return":
            if not (is_snab(request.user) or is_admin(request.user)):
                raise PermissionDenied("Faqat snabjeniye.")
            if req.status != S.SNAB:
                messages.error(request, "Bu so'rov snabjeniye bosqichida emas.")
            else:
                req.status = S.PTO2
                req.snab_by = request.user
                req.snab_at = timezone.now()
                req.save(update_fields=["status", "snab_by", "snab_at"])
                messages.success(request, f"«{req.project.name}» — snabjeniye ko'rdi, PTO xulosasiga qaytdi.")
            return redirect(reverse("dashboard") + "?tab=tasdiqlar")
        # PTO XULOSASI bosqichi: pto2 → dir (yakuniy xulosa bilan direktorga)
        if a == "pto_send_dir":
            if not (is_pto(request.user) or is_admin(request.user)):
                raise PermissionDenied("Faqat PTO.")
            if req.status != S.PTO2:
                messages.error(request, "Bu so'rov PTO xulosasi bosqichida emas.")
            else:
                req.status = S.DIR
                req.pto2_by = request.user
                req.pto2_at = timezone.now()
                req.save(update_fields=["status", "pto2_by", "pto2_at"])
                messages.success(request, f"«{req.project.name}» — PTO xulosasi bilan direktor tasdig'iga yuborildi.")
            return redirect(reverse("dashboard") + "?tab=tasdiqlar")
        # ORQAGA YUBORISH: har bosqich egasi so'rovni OLDINGI (yuborgan) bosqichga
        # tahrir/to'g'irlash uchun qaytaradi — sabab majburiy. Zanjir buzilmaydi,
        # qaytarilgach o'sha user tahrirlab yana oldinga yuboradi.
        if a == "orqaga":
            sabab = " ".join((request.POST.get("sabab") or "").split())[:300]
            target = request.POST.get("target") or ""
            RUXSAT = {
                # joriy status: (kim qaytara oladi, ruxsat etilgan targetlar) —
                # har bir foydalanuvchi YUBORGAN foydalanuvchiga to'g'rilash uchun qaytaradi
                S.PTO2: (lambda u: is_pto(u) or is_admin(u), {S.SNAB}),
                S.DIR: (lambda u: (is_director(u) and not u.is_superuser)
                        or is_asosiy_admin(u), {S.PTO2}),
                S.PROV: (lambda u: is_asosiy_prov(u) or is_admin(u), {S.DIR}),
                S.ADM: (lambda u: is_admin(u), {S.DIR, S.PTO2, S.PROV}),
            }
            NOMI = {S.SNAB: "Snabjeniyega", S.PTO2: "PTOga", S.DIR: "Direktorga", S.PROV: "Proverchikga"}
            if is_admin(request.user) and req.status in LIM_JARAYON:
                # ADMIN — istalgan bosqichdan snab / PTO / direktor / proverchikning
                # istalganiga (xato qilganiga) qaytaradi
                mumkin = {S.SNAB, S.PTO2, S.DIR, S.PROV}
                mumkin.discard(req.status)
                ruxsat_bor, targetlar = True, mumkin
            elif req.status in RUXSAT:
                ruxsat_bor, targetlar = RUXSAT[req.status][0](request.user), RUXSAT[req.status][1]
            else:
                ruxsat_bor, targetlar = None, set()
            if ruxsat_bor is None:
                messages.error(request, "Bu bosqichdan orqaga qaytarib bo'lmaydi.")
            elif not ruxsat_bor:
                raise PermissionDenied("Bu bosqichda qaytarish huquqi yo'q.")
            elif target not in targetlar:
                messages.error(request, "Qaytariladigan bosqich noto'g'ri.")
            elif not sabab:
                messages.error(request, "Qaytarish sababini yozing.")
            else:
                req.status = target
                req.decision_note = (f"↩ ORQAGA QAYTARILDI ({request.user.username}): "
                                     f"{sabab}")[:500]
                req.edited_by = request.user
                req.edited_at = timezone.now()
                req.save(update_fields=["status", "decision_note", "edited_by", "edited_at"])
                # Qaytarilgan bosqich EGASIGA xabar (tizim banneri + Telegram)
                oluvchi = _limit_bosqich_egasi(req, target)
                tg = False
                if oluvchi is not None and oluvchi.pk != request.user.pk:
                    _link = request.build_absolute_uri(_lj_url(req.limit or req.project.joriy_limit()))
                    _, tg = _lj_xabar_yubor(
                        f"↩ «{req.project.name}» limit so'rovi sizga QAYTARILDI "
                        f"({request.user.username}). Sabab: {sabab}\n"
                        f"Limit jadvalida tuzatib, qayta yuboring: {_link}",
                        oluvchi, request.user)
                messages.info(request, f"↩ So'rov {NOMI.get(target, target)} qaytarildi "
                                       f"(sabab: {sabab})."
                                       + (f" {oluvchi.username}ga xabar yuborildi"
                                          + (" (Telegram ham)." if tg else " (tizimda).")
                                          if oluvchi is not None else ""))
            return redirect(reverse("dashboard") + "?tab=tasdiqlar")
        # DIREKTOR bosqichi: dir → adm (yoki rad)
        if a == "dir_approve":
            # Haqiqiy direktor YOKI ASOSIY admin tasdiqlaydi (admin1/2 emas)
            if request.user.is_superuser and not is_asosiy_admin(request.user):
                messages.error(request, "Direktor bosqichini HAQIQIY direktor yoki "
                                        "ASOSIY admin tasdiqlaydi.")
                return redirect(reverse("dashboard") + "?tab=tasdiqlar")
            if not (is_director(request.user) or is_asosiy_admin(request.user)):
                raise PermissionDenied("Faqat direktor.")
            if req.status != S.DIR:
                messages.error(request, "Bu so'rov direktor bosqichida emas.")
            else:
                # Firmada proverchik bo'lsa — avval unga, keyin adminga
                keyingi = S.PROV if _prov_bor(req.project.firma) else S.ADM
                req.status = keyingi
                req.director_by = request.user
                req.director_at = timezone.now()
                req.save(update_fields=["status", "director_by", "director_at"])
                xb = ""
                if keyingi == S.PROV:
                    _link = request.build_absolute_uri(_lj_url(req.limit or req.project.joriy_limit(), korinish="taklif"))
                    xb = _provlarga_xabar(request, f"📋 «{req.project.name}» limit so'rovi tekshiruvga keldi "
                                                   f"(direktor tasdiqladi: {request.user.username}).\nJadval: {_link}")
                messages.success(request, f"«{req.project.name}» — direktor tasdiqladi, "
                                          + ("proverchik tekshiruviga o'tdi." if keyingi == S.PROV
                                             else "admin tasdig'iga o'tdi.") + xb)
        elif a == "prov_approve":
            # PROVERCHIK bosqichi: tekshirib adminga o'tkazadi — faqat ASOSIY proverchik
            if not (is_asosiy_prov(request.user) or is_admin(request.user)):
                raise PermissionDenied("Adminga faqat ASOSIY proverchik o'tkazadi — siz tahrirlab saqlashingiz mumkin.")
            if req.status != S.PROV:
                messages.error(request, "Bu so'rov proverchik bosqichida emas.")
            else:
                req.status = S.ADM
                req.prov_by = request.user
                req.prov_at = timezone.now()
                req.save(update_fields=["status", "prov_by", "prov_at"])
                messages.success(request, f"«{req.project.name}» — proverchik tekshirdi, admin tasdig'iga o'tdi.")
        elif a == "dir_reject":
            if request.user.is_superuser and not is_asosiy_admin(request.user):
                messages.error(request, "Direktor bosqichini HAQIQIY direktor yoki ASOSIY admin ko'radi.")
                return redirect(reverse("dashboard") + "?tab=tasdiqlar")
            if not (is_director(request.user) or is_asosiy_admin(request.user)):
                raise PermissionDenied("Faqat direktor.")
            izoh = (request.POST.get("decision_note") or "").strip()
            if req.status != S.DIR:
                messages.error(request, "Bu so'rov direktor bosqichida emas.")
            elif not izoh:
                messages.error(request, "Rad etish sababini yozing.")
            else:
                req.reject(request.user, izoh)
                messages.info(request, f"«{req.project.name}» — direktor rad etdi. Sabab: {izoh}")
        # ADMIN bosqichi: adm → approved (yoki rad)
        elif a == "approve":
            if not is_admin(request.user):
                raise PermissionDenied("Faqat asosiy admin.")
            if req.status != S.ADM:
                messages.error(request, "Bu so'rov admin bosqichida emas (avval direktor tasdiqlaydi).")
            else:
                req.approve(request.user)
                messages.success(request, f"«{req.project.name}» limiti yangilandi: {_money(req.new_total)}.")
        elif a == "reject":
            if not is_admin(request.user):
                raise PermissionDenied("Faqat asosiy admin.")
            izoh = (request.POST.get("decision_note") or "").strip()
            if req.status != S.ADM:
                messages.error(request, "Bu so'rov admin bosqichida emas.")
            elif not izoh:
                messages.error(request, "Rad etish sababini yozing.")
            else:
                req.reject(request.user, izoh)
                messages.info(request, f"«{req.project.name}» limit so'rovi rad etildi. Sabab: {izoh}")
    return redirect(reverse("dashboard") + "?tab=tasdiqlar")


# Haftalik so'rov zanjiri yordamchilari + _weekly_action_asl ning yangi boshlanishi
# (patch skripti shu faylni views.py ga joylaydi)

WK_JARAYON = ["snab", "pto2", "dir", "prov", "submitted"]   # haftalik zanjir tartibi (draft → … → approved)
WK_BOSQICH_NOM = {"draft": "ПТО", "snab": "Снабжения", "pto2": "ПТО хулосаси", "dir": "Директор",
                  "prov": "Проверчик", "submitted": "Админ", "approved": "Тасдиқланган"}


def _wk_keyingi(p, joriy):
    """Haftalik so'rovning KEYINGI bosqichi (limit zanjiri bilan bir xil qoida):
    snab xodimi bo'lmasa snab va pto2 o'tkazib yuboriladi, proverchik bo'lmasa — prov."""
    tartib = ["draft"] + WK_JARAYON + ["approved"]
    i = tartib.index(joriy) if joriy in tartib else 0
    snab_b, prov_b = _snab_bor(p), _prov_bor(p.firma)
    for st in tartib[i + 1:]:
        if st in ("snab", "pto2") and not snab_b:
            continue
        if st == "prov" and not prov_b:
            continue
        return st
    return "approved"


def _wk_bosqich_egasi(req, status):
    """Haftalik so'rovning shu bosqichida javob beradigan foydalanuvchi."""
    from django.contrib.auth import get_user_model
    U = get_user_model()
    p = req.project

    def birinchi(rol, firma=False):
        qs = U.objects.filter(is_active=True, profile__role=rol)
        qs = qs.filter(profile__firma=p.firma) if firma else qs.filter(profile__projects=p)
        return qs.order_by("username").first()

    if status == "draft" or status == "pto2":
        return (req.pto2_by if status == "pto2" else None) or req.created_by or birinchi("pto")
    if status == "snab":
        return req.snab_by or birinchi("snab")
    if status == "dir":
        return req.director_by or birinchi("director", True)
    if status == "prov":
        pr_ = _provlar()
        return req.prov_by or (pr_[0] if pr_ else None)
    if status == "submitted":
        from django.conf import settings as _s
        return (U.objects.filter(is_active=True, is_superuser=True,
                                 username=getattr(_s, "ASOSIY_ADMIN_LOGIN", "admin")).first()
                or U.objects.filter(is_active=True, is_superuser=True).order_by("username").first())
    return None


def _wk_xabar(request, req, status, matn):
    """Bosqich egasiga tizim banneri + Telegram (o'ziga yubormaydi). Natija: 'ism (Telegram ✓)' yoki ''."""
    if status == "prov":
        link = request.build_absolute_uri(_lj_url(req.limit or req.project.joriy_limit(), hafta=req.id))
        return _provlarga_xabar(request, matn + f"\nJadval: {link}")
    oluvchi = _wk_bosqich_egasi(req, status)
    if oluvchi is None or oluvchi.pk == request.user.pk:
        return ""
    link = request.build_absolute_uri(_lj_url(req.limit or req.project.joriy_limit(), hafta=req.id))
    _, tg = _lj_xabar_yubor(matn + f"\nJadval: {link}", oluvchi, request.user)
    return f" {oluvchi.username}ga xabar yuborildi" + (" (Telegram ham)." if tg else " (tizimda).")


def _wk_qayt(request, req):
    if request.POST.get("next") == "tasdiqlar":
        return redirect(reverse("dashboard") + "?tab=tasdiqlar")
    if request.POST.get("next") == "jadval":
        return redirect(_lj_url(req.limit or req.project.joriy_limit()))
    return redirect("project_detail", pk=req.project_id)


@login_required
def _weekly_action_asl(request, pk):
    """Haftalik so'rov oqimi — UMUMIY LIMIT zanjiri bilan bir xil:
    PTO yuboradi → Snabjeniye narxlaydi → PTO xulosasi → Direktor → Proverchik → Admin.
    Har bosqich egasi oldinga o'tkazadi yoki sabab bilan ORQAGA qaytaradi."""
    from django.utils import timezone
    from .roles import is_admin, is_asosiy_admin, is_asosiy_prov, is_director, is_prov, is_snab
    req = get_object_or_404(WeeklyRequest, pk=pk)
    _firma_yoki_403(request, req.project)
    WS = WeeklyRequest.Status
    proj_id = req.project_id
    u = request.user
    hafta_nom = f"{req.project.code} — {req.week_start:%d.%m}–{req.week_end:%d.%m} haftalik"
    dir_actor = (is_director(u) and not u.is_superuser) or is_asosiy_admin(u)

    def _oldinga(kutilgan, by_field, at_field, ok_matn):
        """Joriy bosqichdan keyingisiga o'tkazish (imzo + keyingi egaga xabar)."""
        if req.status != kutilgan:
            messages.error(request, "Bu so'rov sizning bosqichingizda emas.")
            return
        keyingi = _wk_keyingi(req.project, req.status)
        req.status = keyingi
        fields = ["status"]
        if by_field:
            setattr(req, by_field, u)
            setattr(req, at_field, timezone.now())
            fields += [by_field, at_field]
        req.save(update_fields=fields)
        xabar = _wk_xabar(request, req, keyingi,
                          f"📋 {hafta_nom} so'rovi sizga keldi ({WK_BOSQICH_NOM.get(keyingi, keyingi)} bosqichi). "
                          f"Yubordi: {u.username}.")
        messages.success(request, ok_matn + f" — {WK_BOSQICH_NOM.get(keyingi, keyingi)}ga o'tdi." + xabar)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "tahrir_ruxsat":
            # ADMIN: yopiq haftani jadvalda tahrirlashga BIR MARTALIK ruxsat (har hafta uchun alohida)
            if not is_admin(u):
                raise PermissionDenied("Ruxsatni faqat admin beradi.")
            if req.status == "draft":
                messages.info(request, "Qoralama hafta allaqachon tahrirlanadi.")
            else:
                req.tahrir_ruxsat = not req.tahrir_ruxsat
                req.save(update_fields=["tahrir_ruxsat"])
                messages.success(request, ("🔓 Hafta tahririga ruxsat berildi — jadvalda o'zgartirib "
                                           "«Сақлаш»ni bosing (bir martalik)."
                                           if req.tahrir_ruxsat else "🔒 Hafta tahriri yana yopildi."))
            return _wk_qayt(request, req)
        if action == "ack":
            # So'rov egasi «Tasdiqlandi» xabarini ko'rdi — boshqa ko'rsatilmaydi
            if req.created_by_id == u.id:
                req.pto_notified = True
                req.save(update_fields=["pto_notified"])
            return _wk_qayt(request, req)
        if action == "delitem":
            # Bitta qatorni so'rovdan o'chirish — har kim FAQAT O'Z bosqichida
            EGASI = {WS.SNAB: is_snab(u) or is_admin(u), WS.PTO2: is_pto(u) or is_admin(u),
                     WS.DIR: dir_actor, WS.PROV: is_prov(u) or is_admin(u), WS.SUBMITTED: is_admin(u)}
            it = req.items.filter(id=request.POST.get("item_id")).first()
            if req.status == WS.APPROVED:
                messages.error(request, "Tasdiqlangan so'rov qatorini o'chirib bo'lmaydi.")
            elif not EGASI.get(req.status):
                messages.error(request, "Bu so'rov sizning bosqichingizda emas.")
            elif it is None:
                messages.error(request, "Qator topilmadi.")
            elif req.items.count() <= 1:
                messages.error(request, "Oxirgi qatorni o'chirib bo'lmaydi — o'rniga so'rovni qaytaring.")
            else:
                nom = it.name
                it.delete()
                req.edited_by = u
                req.edited_at = timezone.now()
                req.save(update_fields=["edited_by", "edited_at"])
                messages.info(request, f"«{nom}» qatori so'rovdan o'chirildi.")
            return _wk_qayt(request, req)
        if action == "submit":
            # PTO qoralamani zanjirga yuboradi (snab bo'lsa — snabga, bo'lmasa to'g'ri direktorga)
            if not is_pto(u):
                raise PermissionDenied("Faqat PTO yuboradi.")
            if req.status != WS.DRAFT:
                messages.error(request, "Faqat qoralama holatidagi so'rov yuboriladi.")
            else:
                _oldinga(WS.DRAFT, None, None, "So'rov yuborildi")
        elif action == "recall":
            # PTO yuborilganini qaytarib oladi — faqat BIRINCHI bosqichda, hali hech kim imzolamagan bo'lsa
            if not is_pto(u):
                raise PermissionDenied("Faqat PTO.")
            if req.status != _wk_keyingi(req.project, "draft") or req.snab_by_id or req.director_by_id:
                messages.error(request, "Faqat hali ko'rib chiqilmagan (birinchi bosqichdagi) so'rov qaytarib olinadi.")
            else:
                req.status = WS.DRAFT
                req.save(update_fields=["status"])
                messages.info(request, "So'rov qoralamaga qaytarib olindi.")
        elif action == "snab_return":
            # SNABJENIYE narxlab PTO xulosasiga o'tkazadi
            if not (is_snab(u) or is_admin(u)):
                raise PermissionDenied("Faqat snabjeniye.")
            _oldinga(WS.SNAB, "snab_by", "snab_at", "Snabjeniye ko'rdi")
        elif action == "pto_send_dir":
            # PTO XULOSASI → direktor
            if not (is_pto(u) or is_admin(u)):
                raise PermissionDenied("Faqat PTO.")
            _oldinga(WS.PTO2, "pto2_by", "pto2_at", "PTO xulosasi berildi")
        elif action == "dir_approve":
            # DIREKTOR (yoki ASOSIY admin) tasdiqlaydi: dir → prov yoki admin
            if u.is_superuser and not is_asosiy_admin(u):
                messages.error(request, "Direktor bosqichini HAQIQIY direktor yoki ASOSIY admin tasdiqlaydi.")
                return _wk_qayt(request, req)
            if not dir_actor:
                raise PermissionDenied("Faqat direktor.")
            _oldinga(WS.DIR, "director_by", "director_at", "Direktor tasdiqladi")
        elif action == "prov_approve":
            if not (is_asosiy_prov(u) or is_admin(u)):
                raise PermissionDenied("Adminga faqat ASOSIY proverchik o'tkazadi.")
            _oldinga(WS.PROV, "prov_by", "prov_at", "Proverchik tekshirdi")
        elif action in ("orqaga", "dir_reject"):
            # ORQAGA: har bosqich egasi so'rovni oldingi bosqich egasiga (yoki PTO qoralamasiga)
            # sabab bilan qaytaradi; admin — istalgan bosqichdan istalganiga. dir_reject = eski nom.
            sabab = " ".join((request.POST.get("sabab") or request.POST.get("reject_note") or "").split())[:300]
            target = request.POST.get("target") or ("draft" if action == "dir_reject" else "")
            RUXSAT = {WS.SNAB: (is_snab(u) or is_admin(u), {"draft"}),
                      WS.PTO2: (is_pto(u) or is_admin(u), {"draft", WS.SNAB}),
                      WS.DIR: (dir_actor, {"draft", WS.SNAB, WS.PTO2}),
                      WS.PROV: (is_asosiy_prov(u) or is_admin(u), {"draft", WS.DIR}),
                      WS.SUBMITTED: (is_admin(u), {"draft", WS.SNAB, WS.PTO2, WS.DIR, WS.PROV})}
            if u.is_superuser and not is_asosiy_admin(u) and req.status == WS.DIR and not is_admin(u):
                messages.error(request, "Direktor bosqichini HAQIQIY direktor yoki ASOSIY admin ko'radi.")
                return _wk_qayt(request, req)
            if is_admin(u) and req.status in WK_JARAYON:
                ruxsat_bor, targetlar = True, {"draft", WS.SNAB, WS.PTO2, WS.DIR, WS.PROV} - {req.status}
            elif req.status in RUXSAT:
                ruxsat_bor, targetlar = RUXSAT[req.status]
            else:
                ruxsat_bor, targetlar = None, set()
            if ruxsat_bor is None:
                messages.error(request, "Bu bosqichdan orqaga qaytarib bo'lmaydi.")
            elif not ruxsat_bor:
                raise PermissionDenied("Bu bosqichda qaytarish huquqi yo'q.")
            elif target not in targetlar:
                messages.error(request, "Qaytariladigan bosqich noto'g'ri.")
            elif not sabab:
                messages.error(request, "Qaytarish sababini yozing.")
            else:
                req.status = target
                req.reject_note = (f"↩ ORQAGA QAYTARILDI ({u.username}): {sabab}")[:500]
                req.edited_by = u
                req.edited_at = timezone.now()
                req.save(update_fields=["status", "reject_note", "edited_by", "edited_at"])
                xabar = _wk_xabar(request, req, target,
                                  f"↩ {hafta_nom} so'rovi sizga QAYTARILDI ({u.username}). Sabab: {sabab}")
                messages.info(request, f"↩ So'rov {WK_BOSQICH_NOM.get(target, target)}ga qaytarildi (sabab: {sabab})." + xabar)
        elif action in ("approve", "draft"):
            if not is_admin(u):
                raise PermissionDenied("Haftalik limitni tasdiqlash/bekor qilish — faqat asosiy admin.")
            if action == "approve":
                from django.utils import timezone
                # Faqat DIREKTORdan o'tgan (submitted) so'rov admin tomonidan tasdiqlanadi
                if req.status != WS.SUBMITTED:
                    xm = ("Bu so'rov allaqachon tasdiqlangan." if req.status == WS.APPROVED
                          else "Avval direktor tasdiqlashi kerak.")
                    messages.error(request, xm)
                    if request.POST.get("next") == "tasdiqlar":
                        return redirect(reverse("dashboard") + "?tab=tasdiqlar")
                    return redirect("project_detail", pk=proj_id)
                # Tekshiruv + saqlash BITTA tranzaksiyada — ikki parallel tasdiqlash
                # ikkalasi ham limitga sig'ib qolmasin (poyga holati)
                with transaction.atomic():
                    req = (WeeklyRequest.objects.select_for_update()
                           .select_related("project").get(pk=req.pk))
                    if req.status != WS.SUBMITTED:
                        messages.error(request, "Bu so'rov allaqachon ko'rib chiqilgan.")
                        if request.POST.get("next") == "tasdiqlar":
                            return redirect(reverse("dashboard") + "?tab=tasdiqlar")
                        return redirect("project_detail", pk=proj_id)
                    # Loyihani ham qulflaymiz — IKKI XIL so'rov parallel tasdiqlanganda
                    # ikkalasi ham "limitga sig'adi" bo'lib qolmasin
                    Project.objects.select_for_update().get(pk=req.project_id)
                    oshgan = _limit_oshish(req.limit or req.project.joriy_limit(), _items_by_kind(req.items.all()),
                        exclude_request_id=req.id,
                    )
                    if oshgan and request.POST.get("confirm_over") != "1":
                        messages.error(
                            request,
                            "Tasdiqlanmadi — limitdan oshadi: " + "; ".join(oshgan)
                            + ". Baribir tasdiqlash uchun «Limitdan oshsa ham tasdiqlash» tugmasini bosing.",
                        )
                        if request.POST.get("next") == "tasdiqlar":
                            return redirect(reverse("dashboard") + "?tab=tasdiqlar")
                        return redirect("project_detail", pk=proj_id)
                    req.status = "approved"
                    req.approved_by = request.user
                    req.approved_at = timezone.now()
                    req.pto_notified = False   # PTO'ga "Tasdiqlandi" xabari chiqsin
                    req.save(update_fields=["status", "approved_by", "approved_at", "pto_notified"])
                    # Umumiy limitda yo'q materiallar shu yerda ro'yxatga qo'shiladi
                    qoshildi = _limitga_yangi_materiallar(req)
                if qoshildi:
                    messages.info(
                        request,
                        "Umumiy limit ro'yxatiga yangi material qo'shildi (miqdor 0 — "
                        "limit summasi oshmadi, kerak bo'lsa «Umumiy limit ichi»da miqdor belgilang): "
                        + ", ".join(qoshildi),
                    )
                if oshgan:
                    messages.warning(
                        request,
                        "So'rov LIMITDAN OSHIB tasdiqlandi (admin ruxsati bilan): " + "; ".join(oshgan),
                    )
                else:
                    messages.success(request, "So'rov tasdiqlandi — umumiy limitdan ayirildi.")
            else:
                if req.status == WeeklyRequest.Status.APPROVED:
                    # Tasdiqlanganini qaytarish — faqat KOD bilan (weekly_return_pto)
                    messages.error(request,
                        "Tasdiqlangan so'rov faqat tasdiqlash KODI bilan qaytariladi — "
                        "«PTOga qaytarish» tugmasidan foydalaning.")
                    return redirect("project_detail", pk=proj_id)
                req.status = "draft"
                req.approved_by = None
                req.approved_at = None
                sabab = (request.POST.get("reject_note") or "").strip()
                if sabab:
                    req.reject_note = (f"ADMIN QAYTARDI (to'g'irlash uchun): {sabab}")[:500]
                    req.pto_notified = True
                    req.save(update_fields=["status", "approved_by", "approved_at",
                                            "reject_note", "pto_notified"])
                else:
                    req.save(update_fields=["status", "approved_by", "approved_at"])
                messages.info(request, "So'rov PTOga (qoralamaga) qaytarildi — to'g'irlab qayta yuboradi.")
        elif action == "delete":
            if not is_pto(request.user):
                raise PermissionDenied("Faqat PTO.")
            # Tasdiqlangan so'rovni HECH KIM (admin ham) to'g'ridan-to'g'ri o'chira olmaydi —
            # aks holda tasdiqlangan sarf izsiz yo'qoladi. Avval qoralamaga qaytarilsin.
            if req.status == WeeklyRequest.Status.APPROVED:
                messages.error(
                    request,
                    "Tasdiqlangan so'rovni o'chirib bo'lmaydi. Avval admin uni "
                    "«Qoralamaga qaytarish» orqali bekor qilsin, so'ng o'chirish mumkin.",
                )
            elif req.status in WK_JARAYON:
                messages.error(request, "Yuborilgan so'rovni avval «Qaytarib olish» qiling, so'ng o'chirasiz.")
            else:
                from .models import Moliya as _M2
                if _M2.objects.filter(item__request=req).exists():
                    # To'lov jurnali bor so'rov o'chirilmaydi — pul izsiz yo'qolmasin
                    messages.error(request, "Bu so'rovga to'lovlar yozilgan — o'chirib bo'lmaydi.")
                else:
                    req.delete()
                    messages.info(request, "So'rov o'chirildi.")
    if request.POST.get("next") == "tasdiqlar":
        return redirect(reverse("dashboard") + "?tab=tasdiqlar")
    return redirect("project_detail", pk=proj_id)


def build_hisobotlar_zip(user=None):
    """Barcha hisobotlarni bitta ZIP (bytes) qilib qaytaradi:
       - Umumiy_limitlar.xlsx (barcha obyektlar jadvali)
       - Obyektlar/<kod>_limit.xlsx (har obyekt: limit ichi + haftalik)
       - Ombor_qoldiq.xlsx (joriy qoldiq), agar mavjud bo'lsa.
       View ham, Telegram bot ham shu funksiyani ishlatadi.
       user berilsa — faqat o'sha foydalanuvchi firmasidagi obyektlar (bot=None=hammasi)."""
    import zipfile

    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    proj_qs = visible_projects(user) if user is not None else Project.objects.all()

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        # 1) Umumiy limitlar jadvali
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Limitlar"
        ws.append(["Kod", "Nomi", "Firma", "Material", "Ish haqi", "Mashina chasti",
                   "Ko'zda tutilmagan", "Umumiy limit", "Sarflangan", "Qolgan"])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="213145")
        for p in proj_qs.select_related("firma").order_by("code"):
            sarf = p.sarflangan()
            lim = p.budget_total or Decimal("0")
            ws.append([p.code, p.name, p.firma.name if p.firma_id else "—",
                       float(p.limit_material or 0), float(p.limit_labor or 0),
                       float(p.limit_machinery or 0), float(p.limit_other or 0),
                       float(lim), float(sarf), float(lim - sarf)])
        ws.freeze_panes = "A2"
        for i, w in enumerate([16, 30, 22, 16, 16, 18, 18, 18, 18, 18], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        b = io.BytesIO(); wb.save(b)
        z.writestr("Umumiy_limitlar.xlsx", b.getvalue())

        # 2) Har obyekt uchun batafsil (faqat limiti yoki haftaligi bo'lganlar)
        for p in proj_qs.order_by("code"):
            if p.budget_total <= 0 and not p.weekly_requests.exists() and not p.limit_items.exists():
                continue
            b = io.BytesIO(); _obj_limit_wb(p).save(b)
            fn = f"Obyektlar/{p.code}_limit.xlsx".replace(" ", "_")
            z.writestr(fn, b.getvalue())

        # 3) Ombor qoldig'i (foydalanuvchi ko'rish doirasi bo'yicha; bot user=None=hammasi)
        try:
            from ombor.views import _ombor_wb
            b = io.BytesIO(); _ombor_wb(user=user).save(b)
            z.writestr("Ombor_qoldiq.xlsx", b.getvalue())
        except Exception:
            pass

    return zbuf.getvalue()


@login_required
def hisobotlar_zip(request):
    """Barcha hisobotlarni bitta ZIP faylga jamlab yuklab olish."""
    from django.utils import timezone as _tz
    resp = HttpResponse(build_hisobotlar_zip(user=request.user), content_type="application/zip")
    stamp = _tz.localtime().strftime("%Y%m%d_%H%M")
    resp["Content-Disposition"] = f'attachment; filename="hisobotlar_{stamp}.zip"'
    return resp


@login_required
def limit_export(request):
    """Obyektlar limitini Excel'ga eksport (qayta import qilsa bo'ladigan format)."""
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Limitlar"
    headers = ["Kod", "Nomi", "Material limiti", "Ish haqi limiti", "Mashina chasti limiti",
               "Ko'zda tutilmagan limiti", "Umumiy limit", "Sarflangan", "Qolgan"]
    ws.append(headers)
    for p in visible_projects(request.user).order_by("code"):
        sarf = p.sarflangan()
        limit = p.budget_total or Decimal("0")
        ws.append([
            p.code, p.name,
            float(p.limit_material or 0), float(p.limit_labor or 0),
            float(p.limit_machinery or 0), float(p.limit_other or 0),
            float(limit), float(sarf), float(limit - sarf),
        ])
    for i, w in enumerate([16, 30, 18, 18, 20, 20, 18, 18, 18], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="limitlar.xlsx"'
    return resp


def _obj_limit_wb(p):
    """Bitta obyekt uchun limit hisobotini (2 varaqli) openpyxl Workbook qaytaradi.
       1-varaq — Umumiy limit ichi; 2-varaq — Haftalik so'rovlar."""
    from django.utils.timezone import localtime as _tzloc
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    KIND = dict(KIND_NOMI)
    STATUS = {"draft": "Qoralama", "snab": "Snabjeniye ko'rigida", "pto2": "PTO xulosasida",
              "dir": "Direktor tasdig'ida", "prov": "Proverchik tekshiruvida",
              "submitted": "Admin tasdig'ida", "approved": "Tasdiqlangan"}

    hdr_font = Font(bold=True, color="FFFFFF", size=11)
    hdr_fill = PatternFill("solid", fgColor="213145")
    title_font = Font(bold=True, size=14, color="0B1C30")
    sub_font = Font(size=10, color="64748B")
    tot_font = Font(bold=True, size=11)
    tot_fill = PatternFill("solid", fgColor="EFF4FF")
    thin = Side(style="thin", color="E2E8F0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    money = '#,##0'
    qtyfmt = '#,##0.###'
    right = Alignment(horizontal="right")

    def style_head(ws, row):
        for c in ws[row]:
            c.font = hdr_font
            c.fill = hdr_fill
            c.border = border
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    wb = openpyxl.Workbook()

    # ===== 1-VARAQ: REJA — BAJARILGAN — QOLGAN (Sirdaryo qolipi uslubida) =====
    # Bitta qatorda: limit (reja) | tasdiqlangan haftaliklar (fakt) | qoldiq.
    # Bo'lim(blok)larga guruhlangan, har blok ostida jami, jonli formulalar.
    ws1 = wb.active
    ws1.title = "Limit (Reja-Fakt)"
    ws1["A1"] = f"{p.code} — {p.name}"
    ws1["A1"].font = title_font
    ws1["A2"] = ("Umumiy limit: REJA · BAJARILGAN (tasdiqlangan haftaliklar) · "
                 "QOLGAN (joriy narxda prognoz) · NARX FARQI")
    ws1["A2"].font = sub_font

    # FAKT + JORIY NARX: sahifadagi bilan BIR XIL hisob (nom+bo'lim kesimida)
    fakt, _, _ = _narx_tahlil(p)

    # Bo'limlar bo'yicha guruhlash (sahifadagi tartib: birinchi uchragan, bo'limsiz oxirida)
    guruh = {}
    for it in p.limit_items.all().order_by("id"):
        kal = (it.bolim or "").strip()
        guruh.setdefault(kal, []).append(it)
    tartib = [k for k in guruh if k] + ([""] if "" in guruh else [])

    # Ikki qatorli sarlavha (guruh ustunlari birlashtirilgan):
    #   F-H REJA | I-K BAJARILGAN | L-N QOLGAN | O NARX FARQI
    ws1.append([])
    h1, h2 = 4, 5
    ws1.append(["№", "Turi", "Nomi", "Izoh", "Birlik",
                "REJA (limit)", None, None,
                "BAJARILGAN ISHLAR", None, None,
                "QOLGAN ISHLAR", None, None,
                "NARX FARQI"])
    ws1.append([None, None, None, None, None,
                "Miqdor", "Narx", "Summa",
                "Miqdor", "O'rt. narx", "Summa",
                "Miqdor", "Joriy narx", "Prognoz",
                "Summa"])
    for a, b in (("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"), ("E", "E"), ("O", "O")):
        ws1.merge_cells(f"{a}{h1}:{b}{h2}")
    ws1.merge_cells(f"F{h1}:H{h1}")
    ws1.merge_cells(f"I{h1}:K{h1}")
    ws1.merge_cells(f"L{h1}:N{h1}")
    style_head(ws1, h1)
    style_head(ws1, h2)
    fakt_fill = PatternFill("solid", fgColor="1E9E5A")   # yashil — bajarilgan
    qol_fill = PatternFill("solid", fgColor="E35C1E")    # to'q sariq — qolgan
    farq_fill = PatternFill("solid", fgColor="7F1D1D")   # narx farqi
    for cc in ("I", "J", "K"):
        ws1[f"{cc}{h1}"].fill = fakt_fill; ws1[f"{cc}{h2}"].fill = fakt_fill
    for cc in ("L", "M", "N"):
        ws1[f"{cc}{h1}"].fill = qol_fill; ws1[f"{cc}{h2}"].fill = qol_fill
    ws1[f"O{h1}"].fill = farq_fill; ws1[f"O{h2}"].fill = farq_fill

    grp_fill = PatternFill("solid", fgColor="E8EEF7")
    grp_font = Font(bold=True, size=11, color="0B1C30")
    farq_pos_font = Font(bold=True, color="B91C1C")
    farq_neg_font = Font(bold=True, color="15803D")
    nr = 0
    fakt_berildi = set()
    jami_qatorlar = []   # blok-jami qator raqamlari (UMUMIYda yig'iladi)
    SUM_COLS = ((8, "H"), (11, "K"), (14, "N"), (15, "O"))
    for kal in tartib:
        items = guruh[kal]
        # Blok sarlavhasi
        rr = ws1.max_row + 1
        masul = next((x.masul for x in items if (x.masul or "").strip()), "")
        nomi = kal or "Bo'limsiz qatorlar"
        ws1.cell(rr, 1, nomi + (f"  ·  Mas'ul: {masul}" if masul else ""))
        ws1.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=15)
        for c in ws1[rr]:
            c.fill = grp_fill; c.font = grp_font; c.border = border
        boshi = rr + 1
        for it in items:
            nr += 1
            fk = (it.name or "").strip().lower() + "|" + (it.bolim or "").strip().lower()
            # Bir xil nom+bo'lim ikki qatorda bo'lsa fakt faqat birinchisiga yoziladi
            f = fakt.get(fk) if fk not in fakt_berildi else None
            fakt_berildi.add(fk)
            joriy = float(f["joriy"]) if f else float(it.unit_price)
            ws1.append([
                nr, KIND.get(it.kind, it.kind), it.name, it.note or "", it.unit or "",
                float(it.quantity), float(it.unit_price), None,
                float(f["fakt_qty"]) if f else 0, None, float(f["fakt_sum"]) if f else 0,
                None, joriy, None,
                None,
            ])
            r = ws1.max_row
            ws1.cell(r, 8).value = f"=F{r}*G{r}"                 # reja summa
            ws1.cell(r, 10).value = f"=IF(I{r}>0,K{r}/I{r},0)"   # bajarilgan o'rtacha narx
            ws1.cell(r, 12).value = f"=F{r}-I{r}"                # qolgan miqdor
            ws1.cell(r, 14).value = f"=MAX(L{r},0)*M{r}"         # qolgan prognoz (joriy narxda)
            # narx farqi = FAQAT narx ta'siri (sahifadagi bilan bir xil):
            #   (fakt summa − fakt miqdor × limit narxi) + qolgan × (joriy − limit narxi)
            ws1.cell(r, 15).value = f"=K{r}-I{r}*G{r}+MAX(L{r},0)*(M{r}-G{r})"
            for col in (7, 8, 10, 11, 13, 14, 15):
                ws1.cell(r, col).number_format = money
                ws1.cell(r, col).alignment = right
            for col in (6, 9, 12):
                ws1.cell(r, col).number_format = qtyfmt
                ws1.cell(r, col).alignment = right
            if f and f["farq"]:
                ws1.cell(r, 15).font = farq_pos_font if f["farq"] > 0 else farq_neg_font
            for c in ws1[r]:
                c.border = border
        # Blok jami
        rr = ws1.max_row + 1
        ws1.cell(rr, 3, f"«{nomi}» jami:").font = tot_font
        for col, harf in SUM_COLS:
            c = ws1.cell(rr, col)
            c.value = f"=SUM({harf}{boshi}:{harf}{rr - 1})"
            c.number_format = money; c.font = tot_font
        for c in ws1[rr]:
            c.fill = tot_fill; c.border = border
        jami_qatorlar.append(rr)

    oxiri = ws1.max_row
    # UMUMIY (blok jamilari yig'indisi)
    rr = ws1.max_row + 2
    ws1.cell(rr, 3, "UMUMIY:").font = Font(bold=True, size=12)
    for col, harf in SUM_COLS:
        c = ws1.cell(rr, col)
        c.value = "=" + "+".join(f"{harf}{j}" for j in jami_qatorlar) if jami_qatorlar else 0
        c.number_format = money
        c.font = Font(bold=True, size=12, color="2563EB")
        c.fill = tot_fill
    # Toifa bo'yicha jami (SUMIF — blok sarlavha/jami qatorlarida Turi bo'sh, qo'shilmaydi)
    if oxiri > h2:
        for k in KINDS:
            rr = ws1.max_row + 1
            ws1.cell(rr, 3, KIND[k] + " jami:").font = tot_font
            for col, harf in SUM_COLS:
                c = ws1.cell(rr, col)
                c.value = f'=SUMIF($B${h2 + 1}:$B${oxiri},"{KIND[k]}",${harf}${h2 + 1}:${harf}${oxiri})'
                c.number_format = money
    ws1.freeze_panes = "A6"
    for i, w in enumerate([6, 15, 32, 24, 9, 11, 13, 15, 11, 13, 15, 11, 13, 15, 15], start=1):
        ws1.column_dimensions[get_column_letter(i)].width = w

    # ===== 2-VARAQ: Haftalik so'rovlar =====
    ws2 = wb.create_sheet("Haftalik so'rovlar")
    ws2["A1"] = f"{p.code} — {p.name}"
    ws2["A1"].font = title_font
    ws2["A2"] = "Haftalik so'rovlar (barcha qatorlar bilan)"
    ws2["A2"].font = sub_font
    ws2.append([])
    ws2.append(["Hafta", "Holati", "Hujjat №", "Turi", "Nomi", "Izoh",
                "Birlik", "Miqdor", "Narxi", "Summa", "Kiritdi", "Sana"])
    style_head(ws2, 4)
    reqs = (p.weekly_requests.select_related("created_by")
            .prefetch_related("items").order_by("-week_start", "-id"))
    for w in reqs:
        hafta = f"{w.week_start:%d.%m.%Y} — {w.week_end:%d.%m.%Y}"
        items = list(w.items.all())
        if not items:
            ws2.append([hafta, STATUS.get(w.status, w.status), w.number or "", "", "(qatorsiz)"])
            continue
        for it in items:
            ws2.append([
                hafta, STATUS.get(w.status, w.status), w.number or "",
                KIND.get(it.kind, it.kind), it.name, it.note or "", it.unit or "",
                float(it.quantity), float(it.unit_price), None,
                w.created_by.username if w.created_by_id else "—",
                _tzloc(it.created_at).strftime("%d.%m.%Y %H:%M") if it.created_at else "",
            ])
            ws2.cell(ws2.max_row, 10).value = f"=H{ws2.max_row}*I{ws2.max_row}"
    for r in range(5, ws2.max_row + 1):
        for col in (8, 9, 10):
            ws2.cell(r, col).number_format = money
            ws2.cell(r, col).alignment = right
        for c in ws2[r]:
            c.border = border
    ws2.freeze_panes = "A5"
    for i, w in enumerate([22, 18, 12, 15, 30, 26, 10, 12, 14, 16, 14, 14], start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    return wb


@login_required
def limit_export_obj(request, pk):
    """Bitta obyekt limit hisoboti — Excel."""
    p = _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
    buf = io.BytesIO()
    _obj_limit_wb(p).save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    fn = f"limit_{p.code}.xlsx".replace(" ", "_")
    resp["Content-Disposition"] = f'attachment; filename="{fn}"'
    return resp


@login_required
def grafik_template(request):
    """Haftalik grafik qolipi (bo'sh): Limit (ish haqi + material) + Texnika (alohida)."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style="thin", color="AAB2C0")
    BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)
    HFILL = PatternFill("solid", fgColor="2563EB")
    GFILL = PatternFill("solid", fgColor="EEF2FF")
    DAYFILL = PatternFill("solid", fgColor="1E40AF")
    HFONT = Font(bold=True, color="FFFFFF", size=10)
    BFONT = Font(bold=True, size=10)
    CEN = Alignment(horizontal="center", vertical="center", wrap_text=True)
    DAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]

    def hcell(ws, r, c, val, fill=HFILL, font=HFONT):
        cell = ws.cell(r, c, val)
        cell.fill = fill
        cell.font = font
        cell.alignment = CEN
        cell.border = BORDER
        return cell

    def merge(ws, r1, c1, r2, c2):
        ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)

    wb = openpyxl.Workbook()

    # ===================== 1) LIMIT =====================
    ws = wb.active
    ws.title = "Limit"
    ws["A1"] = "Obyekt:"
    ws["A1"].font = BFONT
    ws["B1"] = "________________________"
    ws["E1"] = "Hafta boshi (Dushanba) sanasi →"
    ws["E1"].font = BFONT
    ws["H1"].number_format = "dd.mm.yyyy"
    ws["H1"].font = BFONT

    # sarlavha: 3 qatorli (super-guruh / guruh / kichik)
    HR1, HR2, HR3 = 3, 4, 5
    fixed = ["№", "Naimenovaniye (ish / material)", "O'lchov birligi", "Kol-vo"]
    for i, t in enumerate(fixed, start=1):
        hcell(ws, HR1, i, t); merge(ws, HR1, i, HR3, i)

    # --- UMUMIY LIMIT (5-9): Ish haqi + Material + Umumiy ---
    hcell(ws, HR1, 5, "UMUMIY LIMIT"); merge(ws, HR1, 5, HR1, 9)
    hcell(ws, HR2, 5, "Ish haqi"); merge(ws, HR2, 5, HR2, 6)
    hcell(ws, HR3, 5, "Narxi"); hcell(ws, HR3, 6, "Summasi")
    hcell(ws, HR2, 7, "Material"); merge(ws, HR2, 7, HR2, 8)
    hcell(ws, HR3, 7, "Narxi"); hcell(ws, HR3, 8, "Summasi")
    hcell(ws, HR2, 9, "Umumiy summa"); merge(ws, HR2, 9, HR3, 9)

    # --- BAJARILGAN / QOLDIQ (10-11) ---
    BQFILL = PatternFill("solid", fgColor="047857")
    hcell(ws, HR1, 10, "BAJARILGAN / QOLDIQ", fill=BQFILL); merge(ws, HR1, 10, HR1, 11)
    hcell(ws, HR2, 10, "Bajarilgan", fill=BQFILL); merge(ws, HR2, 10, HR3, 10)
    hcell(ws, HR2, 11, "Qoldiq", fill=BQFILL); merge(ws, HR2, 11, HR3, 11)

    # --- HAFTALIK LIMIT (kunlik) ---
    col = 12
    day_start = col
    for i in range(7):
        dc = hcell(ws, HR2, col, f'=IF($H$1="","",$H$1+{i})', fill=DAYFILL)
        dc.number_format = "dddd, dd.mm"  # hafta kuni + sana
        merge(ws, HR2, col, HR2, col + 1)
        hcell(ws, HR3, col, "Miqdor", fill=DAYFILL)
        hcell(ws, HR3, col + 1, "Summa", fill=DAYFILL)
        col += 2
    hcell(ws, HR2, col, "Haftalik jami"); merge(ws, HR2, col, HR3, col)
    last_col = col
    hcell(ws, HR1, day_start, "HAFTALIK LIMIT (sanalar avtomatik)", fill=DAYFILL); merge(ws, HR1, day_start, HR1, last_col)

    # JAMI qatori + formulali data qatorlar
    L = get_column_letter
    jrow = HR3 + 1
    data_first, data_last = jrow + 1, jrow + 34
    day_sum_cols = list(range(day_start + 1, last_col, 2))  # kunlik "Summa" ustunlari
    for r in range(data_first, data_last + 1):
        # summasi = narxi * kol-vo (ish haqi, material)
        ws.cell(r, 6, f'=IF(OR($D{r}="",E{r}=""),"",E{r}*$D{r})')
        ws.cell(r, 8, f'=IF(OR($D{r}="",G{r}=""),"",G{r}*$D{r})')
        # Umumiy summa = ish haqi + material
        ws.cell(r, 9, f'=IF(AND(F{r}="",H{r}=""),"",SUM(F{r},H{r}))')
        # Haftalik jami = kunlik summalar yig'indisi
        rng = ",".join(f"{L(c)}{r}" for c in day_sum_cols)
        ws.cell(r, last_col, f'=IF(SUM({rng})=0,"",SUM({rng}))')
        # Bajarilgan = haftalik jami (kunlik bajarilgan ish) ; Qoldiq = umumiy - bajarilgan
        ws.cell(r, 10, f'=IF({L(last_col)}{r}="","",{L(last_col)}{r})')
        ws.cell(r, 11, f'=IF(I{r}="","",I{r}-N(J{r}))')
        for c in range(1, last_col + 1):
            ws.cell(r, c).border = BORDER
    # JAMI SUMMA qatori — ustun bo'yicha yig'indi
    ws.cell(jrow, 2, "JAMI SUMMA:").font = BFONT
    for c in range(1, last_col + 1):
        ws.cell(jrow, c).fill = GFILL
        ws.cell(jrow, c).border = BORDER
    for c in [6, 8, 9, 10, 11] + day_sum_cols + [last_col]:
        cell = ws.cell(jrow, c, f'=SUM({L(c)}{data_first}:{L(c)}{data_last})')
        cell.font = BFONT

    ws.freeze_panes = ws.cell(jrow, 5).coordinate
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 9
    for c in range(5, last_col + 1):
        ws.column_dimensions[get_column_letter(c)].width = 13

    # ===================== 2) TEXNIKA (mashina chasti) =====================
    wt = wb.create_sheet("Texnika limit")
    wt["A1"] = "Obyekt:"; wt["A1"].font = BFONT
    wt["B1"] = "________________________"
    wt["E1"] = "Hafta boshi (Dushanba) sanasi →"; wt["E1"].font = BFONT
    wt["H1"].number_format = "dd.mm.yyyy"; wt["H1"].font = BFONT
    fixed2 = ["№", "Bajariladigan ish", "Texnika turi", "O'lchov birligi"]
    for i, t in enumerate(fixed2, start=1):
        hcell(wt, 2, i, t); merge(wt, 2, i, 3, i)
    col = 5
    for i in range(7):
        dc = hcell(wt, 2, col, f'=IF($H$1="","",$H$1+{i})', fill=DAYFILL)
        dc.number_format = "dddd, dd.mm"
        merge(wt, 2, col, 2, col + 2)
        hcell(wt, 3, col, "Miqdor", fill=DAYFILL)
        hcell(wt, 3, col + 1, "Narxi", fill=DAYFILL)
        hcell(wt, 3, col + 2, "Summa", fill=DAYFILL)
        col += 3
    hcell(wt, 2, col, "Haftalik summa"); merge(wt, 2, col, 3, col)
    last2 = col
    day_sum_t = list(range(7, last2, 3))  # har kunning "Summa" ustuni
    for r in range(5, 31):
        for sc in day_sum_t:
            m, n = L(sc - 2), L(sc - 1)  # miqdor, narxi
            wt.cell(r, sc, f'=IF(OR({m}{r}="",{n}{r}=""),"",{m}{r}*{n}{r})')
        rng = ",".join(f"{L(c)}{r}" for c in day_sum_t)
        wt.cell(r, last2, f'=IF(SUM({rng})=0,"",SUM({rng}))')
        for c in range(1, last2 + 1):
            wt.cell(r, c).border = BORDER
    wt.cell(4, 2, "JAMI:").font = BFONT
    for c in range(1, last2 + 1):
        wt.cell(4, c).fill = GFILL; wt.cell(4, c).border = BORDER
    for c in day_sum_t + [last2]:
        wt.cell(4, c, f'=SUM({L(c)}5:{L(c)}30)').font = BFONT
    wt.freeze_panes = "E4"
    wt.column_dimensions["A"].width = 5
    wt.column_dimensions["B"].width = 28
    wt.column_dimensions["C"].width = 20
    wt.column_dimensions["D"].width = 12
    for c in range(5, last2 + 1):
        wt.column_dimensions[get_column_letter(c)].width = 12

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="haftalik_grafik_qolip.xlsx"'
    return resp


GRAFIK_N = 15  # Excel namunadagi kunlik ustunlar soni (eksport oynasi)


def _grafik_kunlar(start, bugun, tugash=None):
    """Veb grafik uzunligi (kun) — HAFTALIK.
    Tugash sanasi berilgan bo'lsa: boshlanishdan tugashgacha (to'liq haftalarga
    yaxlitlab). Berilmasa: joriy hafta + kelasi haftagacha o'sib boradi.
    Kamida 7 kun, ko'pi 84 kun (12 hafta)."""
    if not start:
        return GRAFIK_N
    if tugash and tugash >= start:
        kun = (tugash - start).days + 1
        return max(7, min(84, ((kun + 6) // 7) * 7))
    farq = (bugun - start).days
    if farq < 0:
        return GRAFIK_N
    return max(GRAFIK_N, min(84, (farq // 7 + 2) * 7))


@login_required
def grafik_list(request):
    """Grafik ishlar — obyektlar ro'yxati, har biriga bosib grafik ochiladi."""
    from django.db.models import Count
    firma_id = request.GET.get("firma") or ""
    projs = (visible_projects(request.user).select_related("firma")
             .annotate(gcount=Count("grafik_rows")).order_by("code"))
    if firma_id:
        projs = projs.filter(firma_id=firma_id)
    rows = [{
        "obj": p,
        "firma": p.firma.name if p.firma_id else "—",
        "gcount": p.gcount,
        "start": p.grafik_start,
    } for p in projs]
    return render(request, "projects/grafik_list.html", {
        "rows": rows, "soni": len(rows),
        "firmalar": visible_firmas(request.user).order_by("name"), "sel_firma": firma_id,
    })


@login_required
def grafik_web(request, pk):
    """Ish grafigi — brauzerda (namunadagi «график работа» kabi tahrirlanadigan jadval)."""
    p = _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
    can_edit = is_pto(request.user)
    from django.utils import timezone as _tz
    bugun = _tz.localdate()

    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Grafikni faqat PTO tahrirlaydi.")
        # Jadval WYSIWYG: sarlavha sanalari tanlangan boshlanishga qarab jonli
        # yangilanadi — foydalanuvchi qaysi ustunda nimani ko'rsa, shu saqlanadi
        eski_qatorlar = list(p.grafik_rows.all())
        gs = (request.POST.get("grafik_start") or "").strip()
        ge = (request.POST.get("grafik_end") or "").strip()
        try:
            p.grafik_start = datetime.date.fromisoformat(gs) if gs else None
        except ValueError:
            p.grafik_start = None
        try:
            p.grafik_end = datetime.date.fromisoformat(ge) if ge else None
        except ValueError:
            p.grafik_end = None
        p.save(update_fields=["grafik_start", "grafik_end"])
        yangi_start = p.grafik_start or p.start_date or bugun
        N = _grafik_kunlar(yangi_start, bugun, p.grafik_end)

        names = request.POST.getlist("name")
        units = request.POST.getlist("unit")
        qtys = request.POST.getlist("qty")
        plans = request.POST.getlist("plan")
        m_boshlar = request.POST.getlist("muddat_boshi")
        muddatlar = request.POST.getlist("muddat")
        srok_offs = request.POST.getlist("srok_off")
        srok_ons = request.POST.getlist("srok_on")
        resps = request.POST.getlist("responsible")
        notes = request.POST.getlist("note")
        # Forma nechta kun ustunini yuborgan bo'lsa — shunchasini o'qiymiz
        form_n = 0
        while f"d{form_n}" in request.POST and form_n < 400:
            form_n += 1
        daycols = [request.POST.getlist(f"d{j}") for j in range(form_n)]

        CAP = 84
        new_rows = []
        t = 0
        for i in range(len(names)):
            nm = " ".join((names[i] or "").split())
            days = {}
            for j in range(form_n):
                col = daycols[j]
                v = (col[i] if i < len(col) else "").strip()
                if v:
                    d = _to_dec(v)
                    # 0 ham SAQLANADI — «ish bo'lmagan kun» (qizil) shu bilan belgilanadi
                    if d is not None and 0 <= j < CAP:
                        days[str(j)] = float(d)
            # Formada KO'RINMAGAN (oyna qisqarganda tashqarida qolgan) eski kunlar
            # YO'QOLMASIN + izoh o'zgargan bo'lsa ESKISI TARIXGA tushadi
            yangi_note = " ".join((notes[i] if i < len(notes) else "").split())[:500]
            tarix = []
            if i < len(eski_qatorlar) and (eski_qatorlar[i].name or "").strip() == nm:
                eski_q = eski_qatorlar[i]
                tarix = list(eski_q.note_tarix or [])
                eski_note = (eski_q.note or "").strip()
                if eski_note and eski_note != yangi_note:
                    tarix.append({"s": bugun.isoformat(), "t": eski_note})
                    tarix = tarix[-30:]   # oxirgi 30 ta izoh saqlanadi
                for k, v in (eski_q.days or {}).items():
                    try:
                        ki = int(k)
                    except (TypeError, ValueError):
                        continue
                    if ki >= form_n and 0 <= ki < CAP and str(ki) not in days:
                        days[str(ki)] = v
            qv = _to_dec(qtys[i] if i < len(qtys) else "0") or Decimal("0")
            pv = _to_dec(plans[i] if i < len(plans) else "0") or Decimal("0")
            if not nm and not days and qv == 0:
                continue
            mb = None
            try:
                mb_s = (m_boshlar[i] if i < len(m_boshlar) else "").strip()
                mb = datetime.date.fromisoformat(mb_s) if mb_s else None
            except ValueError:
                mb = None
            try:
                md = int(muddatlar[i]) if i < len(muddatlar) and muddatlar[i].strip() else 0
            except ValueError:
                md = 0
            def _idx_list(qatorlar_ro):
                nat = []
                if i < len(qatorlar_ro):
                    for tok in (qatorlar_ro[i] or "").split(","):
                        tok = tok.strip()
                        if tok.isdigit() and 0 <= int(tok) < CAP:
                            nat.append(int(tok))
                return nat
            off = _idx_list(srok_offs)
            qoshimcha = _idx_list(srok_ons)
            t += 1
            new_rows.append(GrafikRow(
                project=p, tartib=t, name=nm[:500],
                unit=(units[i] if i < len(units) else "").strip()[:32],
                qty=qv, plan=pv,
                muddat_boshi=mb, muddat=max(0, min(md, 366)),
                srok_off=off, srok_on=qoshimcha,
                responsible=(resps[i] if i < len(resps) else "").strip()[:255],
                note=yangi_note, note_tarix=tarix,
                days=days,
            ))
        with transaction.atomic():
            p.grafik_rows.all().delete()
            GrafikRow.objects.bulk_create(new_rows)
        messages.success(request, f"Grafik saqlandi ({len(new_rows)} qator).")
        return redirect("grafik_web", pk=pk)

    # GET — sana doim ko'rinsin (tanlanmagan bo'lsa: obyekt boshi yoki bugun)
    eff_start = p.grafik_start or p.start_date or bugun
    N = _grafik_kunlar(eff_start, bugun, p.grafik_end)
    # Har bir kun: sana, o'tganmi (bugundan oldin), qaysi haftaga tegishli
    days = []
    for j in range(N):
        d = eff_start + datetime.timedelta(days=j)
        days.append({
            "i": j, "label": d.strftime("%d.%m"), "iso": d.isoformat(),
            "is_past": d < bugun, "week": (d - eff_start).days // 7,
        })
    # Hafta guruhlari — sarlavha ustuni uchun (colspan)
    week_groups = []
    for w in sorted({dd["week"] for dd in days}):
        wdays = [dd for dd in days if dd["week"] == w]
        week_groups.append({
            "week": w, "span": len(wdays),
            "label": f"{wdays[0]['label']} — {wdays[-1]['label']}",
            "all_past": all(dd["is_past"] for dd in wdays),
        })
    day_headers = [dd["label"] for dd in days]
    eski_kun = sum(1 for dd in days if dd["is_past"])
    grows = []
    def _kun_str(v):
        # «%g» katta sonlarni 1.23e+06 ko'rinishida buzardi — aniq format
        s = f"{float(v):.3f}".rstrip("0").rstrip(".")
        return s or "0"
    for r in p.grafik_rows.all():
        kunlar = []
        for j in range(N):
            v = r.days.get(str(j))
            kunlar.append("" if v is None else _kun_str(v))
        grows.append({"r": r, "days": kunlar})
    g_tasdiq = p.grafik_tasdiqlar.select_related(
        "yubordi", "director_by", "decided_by").first()
    return render(request, "projects/grafik_web.html", {
        "p": p, "grows": grows, "N_range": list(range(N)), "day_headers": day_headers,
        "days": days, "week_groups": week_groups, "eski_kun": eski_kun,
        "eski_hafta": sum(1 for wg in week_groups if wg["all_past"]),
        "can_edit": can_edit, "N": N,
        "grafik_start": eff_start.isoformat(),
        "grafik_end": p.grafik_end.isoformat() if p.grafik_end else "",
        "g_tasdiq": g_tasdiq,
        "tasdiq_jarayonda": bool(g_tasdiq and g_tasdiq.status in ("dir", "adm")),
    })


@login_required
def grafik_tasdiq_yubor(request, pk):
    """PTO grafikni TASDIQQA yuboradi (limit zanjiriga o'xshash boshlanish)."""
    p = _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
    if not is_pto(request.user):
        raise PermissionDenied("Grafikni tasdiqqa faqat PTO yuboradi.")
    if request.method != "POST":
        return redirect("grafik_web", pk=pk)
    from .models import GrafikTasdiq
    if p.grafik_tasdiqlar.filter(status__in=("dir", "adm")).exists():
        messages.error(request, "Grafik allaqachon tasdiq jarayonida.")
        return redirect("grafik_web", pk=pk)
    if not p.grafik_rows.exists():
        messages.error(request, "Grafik bo'sh — avval qatorlarni kiritib saqlang.")
        return redirect("grafik_web", pk=pk)
    GrafikTasdiq.objects.create(project=p, yubordi=request.user)
    messages.success(request, "Grafik direktor tasdig'iga yuborildi.")
    return redirect("grafik_web", pk=pk)


@login_required
def grafik_tasdiq_action(request, pk):
    """Grafik tasdig'i: direktor (yoki asosiy admin) -> admin — limitdagidek."""
    from .models import GrafikTasdiq
    from .roles import is_asosiy_admin as _asos, is_director as _isdir
    gt = get_object_or_404(GrafikTasdiq.objects.select_related("project"), pk=pk)
    _firma_yoki_403(request, gt.project)
    if request.method != "POST":
        return redirect(reverse("dashboard") + "?tab=tasdiqlar")
    a = request.POST.get("action")
    from django.utils import timezone as _tz

    def _rad():
        izoh = (request.POST.get("decision_note") or "").strip()
        if not izoh:
            messages.error(request, "Rad etish sababini yozing.")
            return False
        gt.status = "rejected"
        gt.decided_by = request.user
        gt.decided_at = _tz.now()
        gt.decision_note = izoh[:500]
        gt.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        messages.info(request, f"{gt.project.code} grafigi rad etildi: {izoh}")
        return True

    if a in ("dir_approve", "dir_reject"):
        if request.user.is_superuser and not _asos(request.user):
            messages.error(request, "Direktor bosqichini HAQIQIY direktor yoki "
                                    "ASOSIY admin tasdiqlaydi.")
            return redirect(reverse("dashboard") + "?tab=tasdiqlar")
        if not (_isdir(request.user) or _asos(request.user)):
            raise PermissionDenied("Faqat direktor.")
        if gt.status != "dir":
            messages.error(request, "Bu grafik direktor bosqichida emas.")
        elif a == "dir_approve":
            gt.status = "adm"
            gt.director_by = request.user
            gt.director_at = _tz.now()
            gt.save(update_fields=["status", "director_by", "director_at"])
            messages.success(request,
                f"{gt.project.code} grafigi — direktor tasdiqladi, admin tasdig'iga o'tdi.")
        else:
            _rad()
    elif a in ("approve", "reject"):
        if not is_admin(request.user):
            raise PermissionDenied("Faqat admin.")
        if gt.status != "adm":
            messages.error(request, "Bu grafik admin bosqichida emas (avval direktor tasdiqlaydi).")
        elif a == "approve":
            gt.status = "approved"
            gt.decided_by = request.user
            gt.decided_at = _tz.now()
            gt.save(update_fields=["status", "decided_by", "decided_at"])
            messages.success(request, f"{gt.project.code} grafigi TASDIQLANDI.")
        else:
            _rad()
    return redirect(reverse("dashboard") + "?tab=tasdiqlar")


@login_required
def grafik_rabota(request, pk=None):
    """Ish grafigi — namunadagi AYNAN qolipda (grafik_namuna.xlsx dan)."""
    obj_name = "________________________"
    start = None
    fname = "grafik_rabota.xlsx"
    if pk:
        p = _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
        obj_name = p.name
        start = p.start_date
        fname = "grafik_rabota_%s.xlsx" % p.code

    import openpyxl
    from openpyxl.cell.cell import MergedCell
    from openpyxl.styles import PatternFill
    from django.conf import settings as _s

    tpl = _s.BASE_DIR / "projects" / "xlsx" / "grafik_namuna.xlsx"
    wb = openpyxl.load_workbook(tpl)

    BOSH_FILL = PatternFill()   # rangsiz

    def clear_rows(ws, first_row):
        # Yozuvlar BILAN birga namunadagi eski RANGLAR ham tozalanadi —
        # aks holda shablonning sariq/yashil/qizil kataklari hisobotda qolib ketadi
        for row in ws.iter_rows(min_row=first_row, max_row=ws.max_row):
            for cell in row:
                if not isinstance(cell, MergedCell):
                    cell.value = None
                    if cell.fill and cell.fill.patternType:
                        cell.fill = BOSH_FILL

    # ===== 1) grafik =====
    ws = wb["график"]
    ws["A1"] = obj_name
    clear_rows(ws, 4)
    # Vebda kiritilgan grafik qatorlari (bo'lsa) — ranglari bilan chiqadi
    grows = list(p.grafik_rows.all()) if pk else []
    if pk and p.grafik_start:
        start = p.grafik_start
    oxirgi = max(21, 3 + len(grows))
    # No + JONLI formulalar: kunlik H..V (8..22), E=5, G=7 (foiz), W=23 (qoldiq).
    # Z (yashirin) = oynadan TASHQARIDAGI haftalar yig'indisi — formulaga qo'shiladi,
    # shunda Excel ichida kun kataklarini o'zgartirsangiz foiz/qoldiq o'zi qayta hisoblanadi
    F_FOIZ = PatternFill("solid", fgColor="FFFDF0D2")   # och sariq fon
    for i, r in enumerate(range(4, oxirgi + 1), start=1):
        ws.cell(r, 1).value = i
        gc = ws.cell(r, 7)
        gc.value = f'=IF($E{r}="","",(SUM(H{r}:V{r})+N($Z{r}))/$E{r})'
        gc.number_format = "0%"
        gc.fill = F_FOIZ
        ws.cell(r, 23).value = f'=IF($E{r}="","",$E{r}-SUM(H{r}:V{r})-N($Z{r}))'
    ws.cell(3, 26).value = "oyna tashqarisi"
    ws.column_dimensions["Z"].hidden = True
    # Foiz katagi TO'Q SARIQ chiziq bilan foizga qarab TO'LIB boradi (data bar)
    from openpyxl.formatting.rule import DataBarRule
    ws.conditional_formatting.add(
        f"G4:G{oxirgi}",
        DataBarRule(start_type="num", start_value=0, end_type="num", end_value=1,
                    color="FFC000", showValue=True))
    # Surilma oyna: grafik haftalik davom etadi, Excel 15 kunlik panelga
    # O'TGAN HAFTA + JORIY HAFTA (+ keyingi kunlar) chiqadi
    ofs = 0
    if grows and start:
        from django.utils import timezone as _tz
        farq = (_tz.localdate() - start).days
        if farq >= 0 and farq // 7 >= 2:
            ofs = (farq // 7 - 1) * 7
    if start:
        ws.cell(3, 8).value = start + datetime.timedelta(days=ofs)
        for j in range(1, 15):
            ws.cell(3, 8 + j).value = f'=IF($H$3="","",$H$3+{j})'
    # Ma'lumotlar + namunadagi ranglar: 0 -> QIZIL, plandan kam -> SARIQ,
    # plan bajarilgan -> YASHIL (bo'sh kun rangsiz)
    if grows:
        F_QIZIL = PatternFill("solid", fgColor="FFFF0000")
        F_SARIQ = PatternFill("solid", fgColor="FFFFFF00")
        F_YASHIL = PatternFill("solid", fgColor="FF00B050")
        F_KOK = PatternFill("solid", fgColor="FFD7E9FB")   # srok (muddat) kunlari
        for idx, g in enumerate(grows):
            r = 4 + idx
            ws.cell(r, 2).value = g.name or None
            ws.cell(r, 4).value = g.unit or None
            ws.cell(r, 5).value = float(g.qty) if g.qty else None
            ws.cell(r, 6).value = float(g.plan) if g.plan else None
            ws.cell(r, 24).value = g.responsible or None
            ws.cell(r, 25).value = g.note or None
            # Z = oynadan tashqaridagi kunlar yig'indisi (formula shuni qo'shib hisoblaydi)
            tashqari = 0.0
            for k, v in (g.days or {}).items():
                try:
                    ki = int(k)
                except (TypeError, ValueError):
                    continue
                if not (ofs <= ki < ofs + GRAFIK_N):
                    tashqari += float(v)
            ws.cell(r, 26).value = tashqari
            plan = float(g.plan or 0)
            # Srok (muddat) oralig'i — vebdagi och ko'k belgi bilan bir xil
            boshi = None
            if g.muddat and start:
                boshi = 0
                if g.muddat_boshi:
                    boshi = (g.muddat_boshi - start).days
            off = set(int(x) for x in (g.srok_off or []))
            qosh = set(int(x) for x in (g.srok_on or []))
            for j in range(GRAFIK_N):
                gi = ofs + j
                c = ws.cell(r, 8 + j)
                v = (g.days or {}).get(str(gi))
                if v is not None:
                    v = float(v)
                    c.value = int(v) if v == int(v) else v
                    if v <= 0:
                        c.fill = F_QIZIL
                    elif plan > 0 and v < plan:
                        c.fill = F_SARIQ
                    else:
                        c.fill = F_YASHIL
                else:
                    srokmi = (boshi is not None and boshi <= gi < boshi + g.muddat
                              and gi not in off) or (gi in qosh)
                    if srokmi:
                        c.fill = F_KOK

    # ===== 2) Solyarka va gaz =====
    if "Солярка ва газ" in wb.sheetnames:
        wf = wb["Солярка ва газ"]
        try:
            wf["A1"] = obj_name
        except Exception:
            pass
        clear_rows(wf, 4)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="%s"' % fname
    return resp


def _xavfsiz_referer(request, fallback_name):
    """Referer'ga qaytish — faqat o'z saytimiz ichida bo'lsa (ochiq redirect emas)."""
    from django.utils.http import url_has_allowed_host_and_scheme
    ref = request.META.get("HTTP_REFERER") or ""
    if ref and url_has_allowed_host_and_scheme(ref, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return ref
    return reverse(fallback_name)


def _moliya_yoza_oladi(user):
    """To'lovni FAQAT BUXGALTER yozadi (admin ham ko'radi, lekin yozmaydi —
    moliya javobgarligi buxgalterda qolsin)."""
    from .roles import is_bux
    return bool(is_bux(user))


def moliya_kora_oladi(user):
    """Moliyalashtirish bo'limini KIM KO'RADI: hamma, FAQAT PRORAB emas
    (menyu ham, sahifa ham, obyektdagi berildi/qarz ko'rsatkichlari ham)."""
    from .roles import is_prorab
    return bool(user.is_authenticated and not is_prorab(user))


@login_required
def moliya(request):
    """MOLIYALASHTIRISH — UMUMIY LIMIT JADVALI bo'yicha (2026-10-12).
    Obyekt tanlanadi → har raqamli limit to'liq jadval: Umumiy / Bajarilgan / Qolgan
    + BERILGAN (buxgalter to'lovlari) / QOLGAN OSTATKI (limit − berilgan) / IZOH.
    To'lov limit qatoriga yoziladi; eski haftalik to'lovlar mos qatorga qo'shiladi.
    Obyekt tanlanmasa — obyektlar ro'yxati (limit / bajarilgan / berildi / ostatki)."""
    from django.db.models import Q
    from django.utils import timezone
    from .models import Moliya, LimitItem, MoliyaTahrirSorov
    if not moliya_kora_oladi(request.user):
        raise PermissionDenied("Moliyalashtirish bo'limi prorabga ko'rsatilmaydi.")
    yoza_oladi = _moliya_yoza_oladi(request.user)
    is_adm = is_admin(request.user)
    vp_all = visible_projects(request.user)
    vp = vp_all
    firma_id = request.GET.get("firma") or ""
    proj_id = request.GET.get("obyekt") or ""
    if firma_id.isdigit():
        vp = vp.filter(firma_id=int(firma_id))
    else:
        firma_id = ""
    if not proj_id.isdigit():
        proj_id = ""
    KCLS = {"material": "mat", "labor": "lab", "machinery": "mach", "other": "oth"}

    def _dec(x):
        return Decimal(str(x or 0))

    # ---------- OBYEKTLAR RO'YXATI ----------
    obyektlar = []
    j_lim = j_baj = j_ber = Decimal("0")
    j_qarz = Decimal("0")
    oxirgi_tolov = {}
    for m in (Moliya.objects.filter(Q(item__request__project__in=vp) | Q(limit_item__project__in=vp), bekor=False)
              .select_related("item__request", "limit_item").order_by("-sana", "-id")):
        pid = m.limit_item.project_id if m.limit_item_id else m.item.request.project_id
        oxirgi_tolov.setdefault(pid, m.sana)
    # Ochiq tahrir so'rovlari — obyekt bo'yicha soni (admin ko'radi)
    sorov_soni = {}
    for sr in (MoliyaTahrirSorov.objects.filter(status=MoliyaTahrirSorov.Status.SOROV)
               .filter(Q(limit_item__project__in=vp) | Q(item__request__project__in=vp))
               .select_related("limit_item", "item__request")):
        pid = sr.limit_item.project_id if sr.limit_item_id else sr.item.request.project_id
        sorov_soni[pid] = sorov_soni.get(pid, 0) + 1
    from .models import obyekt_yigindilar
    _Y = obyekt_yigindilar(vp)
    for p in vp.select_related("firma").order_by("code"):
        lim_s = p.budget_total or Decimal("0")
        baj = _Y[p.pk]["sarf"]
        ber = _Y[p.pk]["ber"]
        ost = lim_s - ber
        qarz = baj - ber if baj > ber else Decimal("0")
        j_lim += lim_s; j_baj += baj; j_ber += ber; j_qarz += qarz
        toliq = lim_s > 0 and ost <= 0
        if toliq:
            holat, holat_nom = "toliq", "To'liq"
        elif qarz > Decimal("0.5"):
            holat, holat_nom = "qarz", "Qarz " + _money(qarz)
        elif ber <= 0:
            holat, holat_nom = "yoq", "Boshlanmagan"
        else:
            holat, holat_nom = "ok", "Bajarilgani berilgan"
        obyektlar.append({
            "p": p, "lim_str": _money(lim_s), "baj_str": _money(baj), "ber_str": _money(ber),
            "ost_str": _money(ost), "ost_manfiy": ost < 0, "qarz": qarz, "qarz_str": _money(qarz),
            "foiz": min(100, int(ber / lim_s * 100)) if lim_s > 0 else 0,
            "toliq": toliq, "holat": holat, "holat_nom": holat_nom,
            "limit_soni": _Y[p.pk]["lim_soni"],
            "oxirgi": oxirgi_tolov.get(p.pk), "sorov_soni": sorov_soni.get(p.pk, 0),
            "q": f"{p.code} {p.name} {p.firma.name if p.firma_id else ''}".lower(),
        })
    tartib = request.GET.get("tartib") or "ost"
    if tartib == "qarz":
        obyektlar.sort(key=lambda d: -d["qarz"])
    elif tartib == "ost":
        obyektlar.sort(key=lambda d: -(Decimal(d["ost_str"].replace(" ", "") or 0)))
    reyting = sorted([d for d in obyektlar if d["qarz"] > 0], key=lambda d: -d["qarz"])[:5]
    r_max = reyting[0]["qarz"] if reyting else Decimal("1")
    for d in reyting:
        d["qarz_foiz"] = int(d["qarz"] / r_max * 100) if r_max else 0
    jami = {"lim": _money(j_lim), "baj": _money(j_baj), "ber": _money(j_ber),
            "ost": _money(j_lim - j_ber), "ost_manfiy": j_lim - j_ber < 0, "qarz": _money(j_qarz),
            "foiz": min(100, int(j_ber / j_lim * 100)) if j_lim > 0 else 0,
            "soni": len(obyektlar), "qarz_soni": sum(1 for d in obyektlar if d["holat"] == "qarz"),
            "toliq_soni": sum(1 for d in obyektlar if d["toliq"]), "sorov_soni": sum(sorov_soni.values())}

    # ---------- TANLANGAN OBYEKT — LIMIT JADVALI ----------
    sel_p, limit_bloklar = None, []
    if proj_id:
        sel_p = vp_all.filter(pk=int(proj_id)).select_related("firma").first()
    if sel_p is not None:
        for lim in sel_p.limitlar.order_by("raqam"):
            # Bajarilgan = TASDIQLANGAN haftaliklar (shu limit) — nom+bo'lim kesimida
            fakt = {}
            boshqa_items = []   # limitda yo'q qatorlar (haftaliklardan)
            w_items = (WeeklyRequestItem.objects
                       .filter(request__limit=lim, request__status=WeeklyRequest.Status.APPROVED)
                       .select_related("request").prefetch_related("moliyalar__yozdi").order_by("request__week_start", "id"))
            for it in w_items:
                k = _lj_key(it.name, it.bolim)
                d = fakt.setdefault(k, {"qty": Decimal("0"), "sum": Decimal("0")})
                d["qty"] += it.quantity; d["sum"] += it.total
            items = list(lim.items.all().order_by("id"))
            guruhlar = _lj_guruhlar(items, fakt)
            li_map = {li.pk: li for li in items}
            limit_keys = {r["key"] for g in guruhlar for r in g["rows"]}
            # To'lovlar: limit qatoriga yozilganlar + eski haftalik to'lovlar (kalit bo'yicha,
            # kalitning BIRINCHI qatoriga)
            tol_li = {}
            for m in (Moliya.objects.filter(limit_item__limit=lim).select_related("yozdi", "bekor_by", "tahrir_by")
                      .order_by("sana", "id")):
                tol_li.setdefault(m.limit_item_id, []).append(m)
            tol_key = {}
            for it in w_items:
                k = _lj_key(it.name, it.bolim)
                if k in limit_keys:
                    tol_key.setdefault(k, []).extend(it.moliyalar.all())
                else:
                    boshqa_items.append(it)
            # Ochiq tahrir so'rovlari (pozitsiya bo'yicha)
            sor_li, sor_it = {}, {}
            for sr in (MoliyaTahrirSorov.objects
                       .filter(status__in=[MoliyaTahrirSorov.Status.SOROV, MoliyaTahrirSorov.Status.RUXSAT])
                       .filter(Q(limit_item__limit=lim) | Q(item__request__limit=lim)).select_related("soradi")):
                if sr.limit_item_id:
                    sor_li[sr.limit_item_id] = sr
                else:
                    sor_it[sr.item_id] = sr
            key_korildi = set()
            l_t = l_f = l_b = Decimal("0")
            for g in guruhlar:
                g_t = g_f = g_b = Decimal("0")
                for r in g["rows"]:
                    li = li_map[r["id"]]
                    total = li.total
                    f_sum = _dec(r["fakt_sum"])
                    tolovlar = list(tol_li.get(li.pk, []))
                    if r["key"] not in key_korildi:
                        key_korildi.add(r["key"])
                        tolovlar += list(tol_key.get(r["key"], []))
                    tolovlar.sort(key=lambda m: (m.sana, m.id))
                    ber = sum((m.summa for m in tolovlar if not m.bekor), Decimal("0"))
                    ost = total - ber
                    toliq_ = total > 0 and ost <= 0
                    holat = "toliq" if toliq_ else ("qisman" if ber > 0 else "yoq")
                    sr = sor_li.get(li.pk)
                    r.update({
                        "sorov": sr, "faol_tolov": sum(1 for m in tolovlar if not m.bekor),
                        "sorov_url": reverse("moliya_tahrir_sorov", args=["limit", li.pk]),
                        "holat": holat, "holat_nom": {"toliq": "To'liq", "qisman": "Qisman", "yoq": "Berilmagan"}[holat],
                        "baj_qarz": _money(f_sum - ber) if f_sum - ber > Decimal("0.5") else "",
                        "tolov_json": json.dumps(_moliya_tolov_json(tolovlar), ensure_ascii=False),
                        "ber_raw": str(ber), "total_raw": str(total), "fs_raw": str(f_sum),
                        "li": li, "kind_cls": KCLS.get(r["kind"], "mat"),
                        "kind_disp": KIND_NOMI.get(r["kind"], r["kind"]),
                        "vol_str": _qty(li.quantity), "price_str": _money(li.unit_price), "total_str": _money(total),
                        "fq_str": _qty(_dec(r["fakt_qty"])), "fs_str": _money(f_sum),
                        "qq_str": _qty(li.quantity - _dec(r["fakt_qty"])), "qs_str": _money(total - f_sum),
                        "ber_str": _money(ber), "ost_str": _money(ost), "ost_manfiy": ost < 0,
                        "toliq": total > 0 and ost <= 0, "tolovlar": tolovlar,
                        "foiz": min(100, int(ber / total * 100)) if total > 0 else 0,
                        "m_izoh": li.moliya_izoh or "",
                        "ber_bajdan_kop": ber > f_sum + Decimal("0.5"),
                    })
                    g_t += total; g_f += f_sum; g_b += ber
                g.update({"t_str": _money(g_t), "f_str": _money(g_f), "b_str": _money(g_b),
                          "o_str": _money(g_t - g_b), "o_manfiy": g_t - g_b < 0, "soni": len(g["rows"]),
                          "foiz": min(100, int(g_b / g_t * 100)) if g_t > 0 else 0})
                l_t += g_t; l_f += g_f; l_b += g_b
            # Limitda yo'q (haftalikdan kelgan) qatorlar — to'lov haftalik qatoriga (eski usul)
            boshqa, bq_t, bq_b = [], Decimal("0"), Decimal("0")
            for it in boshqa_items:
                b_ = it.berildi
                tl_ = list(it.moliyalar.all())
                toliq_ = it.qarz <= 0
                holat = "toliq" if toliq_ else ("qisman" if b_ > 0 else "yoq")
                boshqa.append({"it": it, "w": it.request, "kind_cls": KCLS.get(it.kind, "mat"),
                               "sorov": sor_it.get(it.pk), "faol_tolov": sum(1 for m in tl_ if not m.bekor),
                               "sorov_url": reverse("moliya_tahrir_sorov", args=["hafta", it.pk]),
                               "kind_disp": KIND_NOMI.get(it.kind, it.kind),
                               "qty_str": _qty(it.quantity), "price_str": _money(it.unit_price),
                               "total_str": _money(it.total), "ber_str": _money(b_),
                               "ost_str": _money(it.total - b_), "toliq": toliq_,
                               "holat": holat, "holat_nom": {"toliq": "To'liq", "qisman": "Qisman", "yoq": "Berilmagan"}[holat],
                               "foiz": min(100, int(b_ / it.total * 100)) if it.total > 0 else 0,
                               "tolovlar": tl_, "tolov_json": json.dumps(_moliya_tolov_json(tl_), ensure_ascii=False),
                               "ber_raw": str(b_), "total_raw": str(it.total), "fs_raw": str(it.total)})
                bq_t += it.total; bq_b += b_
            limit_bloklar.append({
                "lim": lim, "guruhlar": guruhlar, "boshqa": boshqa,
                "bq_t_str": _money(bq_t), "bq_b_str": _money(bq_b), "bq_o_str": _money(bq_t - bq_b),
                "t_str": _money(l_t), "f_str": _money(l_f), "b_str": _money(l_b),
                "o_str": _money(l_t - l_b), "o_manfiy": l_t - l_b < 0,
                "foiz": min(100, int(l_b / l_t * 100)) if l_t > 0 else 0,
                "qator_soni": sum(len(g["rows"]) for g in guruhlar),
                "jadval_url": _lj_url(lim),
            })
        # Holat bo'yicha sonlar (filtr chiplari) — barcha limitlar bo'yicha
        _hs = {"hammasi": 0, "yoq": 0, "qisman": 0, "toliq": 0}
        for bl in limit_bloklar:
            for g in bl["guruhlar"]:
                for r in g["rows"]:
                    _hs["hammasi"] += 1; _hs[r["holat"]] += 1
            for b in bl["boshqa"]:
                _hs["hammasi"] += 1; _hs[b["holat"]] += 1
        holat_soni = _hs
        sorovlar = []
        for bl in limit_bloklar:
            for g in bl["guruhlar"]:
                for r in g["rows"]:
                    if r.get("sorov"):
                        sorovlar.append({"s": r["sorov"], "nom": r["name"], "bolim": g["bolim"], "lim": bl["lim"]})
            for b in bl["boshqa"]:
                if b.get("sorov"):
                    sorovlar.append({"s": b["sorov"], "nom": b["it"].name, "bolim": b["it"].bolim, "lim": bl["lim"]})

    # ---------- SO'NGGI TO'LOVLAR JURNALI ----------
    jurnal = []
    _jp = [sel_p] if sel_p is not None else vp_all
    for m_ in (Moliya.objects
               .filter(Q(item__request__project__in=_jp) | Q(limit_item__project__in=_jp))
               .select_related("item", "item__request", "item__request__project",
                               "limit_item", "limit_item__project", "limit_item__limit", "yozdi")
               .order_by("-created_at")[:40]):
        q = m_.qator
        if q is None:
            continue
        if m_.limit_item_id:
            p_, lim_, w_ = q.project, q.limit, None
            tol_jami = sum((x.summa for x in q.moliyalar.all() if not x.bekor), Decimal("0"))
        else:
            p_, lim_, w_ = q.request.project, q.request.limit, q.request
            tol_jami = q.berildi
        jurnal.append({
            "m": m_, "q": q, "p": p_, "lim": lim_, "w": w_,
            "summa_str": _money(m_.summa),
            "miqdor_str": _qty(m_.miqdor) if m_.miqdor is not None else "",
            "total_str": _money(q.total), "ber_str": _money(tol_jami),
            "ost_str": _money(q.total - tol_jami), "toliq": q.total - tol_jami <= 0,
            "kind_disp": KIND_NOMI.get(q.kind, q.kind),
            "qty_str": _qty(q.quantity), "price_str": _money(q.unit_price),
            "usul": m_.get_usul_display() if m_.usul else "",
        })
    sel_kpi = next((d for d in obyektlar if sel_p is not None and d["p"].pk == sel_p.pk), None)
    if sel_p is not None and sel_kpi is None:
        # tanlangan obyekt firma filtridan tashqarida — alohida hisoblash
        lim_s = sel_p.budget_total or Decimal("0"); baj = sel_p.sarflangan(); ber = sel_p.berildi()
        qarz = baj - ber if baj > ber else Decimal("0")
        sel_kpi = {"lim_str": _money(lim_s), "baj_str": _money(baj), "ber_str": _money(ber), "ost_str": _money(lim_s - ber),
                   "ost_manfiy": lim_s - ber < 0, "qarz_str": _money(qarz), "qarz": qarz,
                   "foiz": min(100, int(ber / lim_s * 100)) if lim_s > 0 else 0, "toliq": lim_s > 0 and lim_s - ber <= 0,
                   "limit_soni": sel_p.limitlar.count(), "oxirgi": None}
    return render(request, "projects/moliya.html", {
        "obyektlar": obyektlar, "jami": jami, "sel_p": sel_p, "sel_kpi": sel_kpi, "limit_bloklar": limit_bloklar,
        "holat_soni": holat_soni if sel_p is not None else None, "tartib": tartib,
        "is_adm": is_adm, "sorovlar": sorovlar if sel_p is not None else [],
        "amal_ustun": yoza_oladi or is_adm,
        "tolov_soni": sum(1 for _ in jurnal),
        "jurnal": jurnal, "yoza_oladi": yoza_oladi,
        "firmalar": visible_firmas(request.user).order_by("name"),
        "obyekt_ro": vp.order_by("code"),
        "sel_firma": firma_id, "sel_obyekt": proj_id,
        "bugun": timezone.localdate().isoformat(),
    })


def _moliya_tolov_json(tolovlar):
    """Yon panel (JS) uchun to'lov tarixi."""
    from django.utils.timezone import localtime as _lt
    out = []
    for m in tolovlar:
        out.append({"id": m.pk, "sana": f"{m.sana:%d.%m.%Y}", "sana_iso": m.sana.isoformat(),
                    "summa": _money(m.summa), "summa_raw": str(m.summa),
                    "usul": m.get_usul_display() if m.usul else "", "usul_kod": m.usul or "", "izoh": m.izoh or "",
                    "kim": m.yozdi.username if m.yozdi_id else "—",
                    "vaqt": f"{_lt(m.created_at):%d.%m %H:%M}" if m.created_at else "",
                    "eski": bool(m.item_id), "bekor": bool(m.bekor), "bekor_sabab": m.bekor_sabab or "",
                    "asl": _money(m.asl_summa) if m.asl_summa is not None else "",
                    "tahrir": (m.tahrir_by.username if m.tahrir_by_id else "") + (f" · {_lt(m.tahrir_at):%d.%m %H:%M}" if m.tahrir_at else "")})
    return out


def _moliya_li_berildi(li):
    """Limit qatoriga BERILGAN jami: limit qatori to'lovlari + shu limitdagi tasdiqlangan
    haftaliklarning (eski usul) shu nom+bo'limli qator to'lovlari."""
    from .models import Moliya
    s = Moliya.objects.filter(limit_item=li, bekor=False).aggregate(s=Sum("summa"))["s"] or Decimal("0")
    for m in Moliya.objects.filter(item__request__limit_id=li.limit_id, bekor=False,
                                   item__request__status=WeeklyRequest.Status.APPROVED).select_related("item"):
        if _lj_key(m.item.name, m.item.bolim) == _lj_key(li.name, li.bolim):
            s += m.summa
    return s


@login_required
def moliya_limit_yozish(request, li_id):
    """Buxgalter: UMUMIY LIMIT qatoriga TO'LOV yozadi (jurnal — o'chirilmaydi).
    Chegara: limit qatori summasi − berilgan (ortiqcha to'lov yozilmaydi)."""
    from .models import Moliya, LimitItem
    if not _moliya_yoza_oladi(request.user):
        raise PermissionDenied("To'lovni faqat BUXGALTER yozadi.")
    li = get_object_or_404(LimitItem.objects.select_related("project", "limit"), pk=li_id)
    _firma_yoki_403(request, li.project)
    qayt = _xavfsiz_referer(request, "moliya")
    if request.method != "POST":
        return redirect(qayt)
    summa = _to_dec((request.POST.get("summa") or "").replace(" ", "").replace("\xa0", ""))
    miqdor = _to_dec(request.POST.get("miqdor"))
    izoh = " ".join((request.POST.get("izoh") or "").split())[:300]
    usul = request.POST.get("usul") or ""
    if usul not in ("kassa", "karta", "avans"):
        usul = ""
    try:
        sana = datetime.date.fromisoformat(request.POST.get("sana", ""))
    except ValueError:
        sana = datetime.date.today()
    if summa is None or summa <= 0 or summa > Decimal("1000000000000"):
        messages.error(request, "Berilgan summani kiriting (0 dan katta).")
        return redirect(qayt)
    if miqdor is not None and (miqdor < 0 or miqdor > Decimal("1000000000")):
        miqdor = None
    with transaction.atomic():
        li = LimitItem.objects.select_for_update().select_related("project", "limit").get(pk=li.pk)
        ost = li.total - _moliya_li_berildi(li)
        if summa > ost + Decimal("0.01"):
            messages.error(request,
                f"Summa qolgan ostatkidan katta: ostatki {_money(ost)}, siz {_money(summa)} yozdingiz. "
                "Ortiqcha to'lov yozilmaydi — avval limitni oshiring.")
            return redirect(qayt)
        Moliya.objects.create(limit_item=li, summa=summa, miqdor=miqdor, izoh=izoh, usul=usul,
                              sana=sana, yozdi=request.user)
    qoldi = li.total - _moliya_li_berildi(li)
    messages.success(request,
        f"«{li.name}» — {_money(summa)} berildi deb yozildi. "
        + ("Qator TO'LIQ moliyalashtirildi ✓" if qoldi <= 0 else f"Qolgan ostatki: {_money(qoldi)}"))
    return redirect(qayt)


def _moliya_qator(tur, pk):
    """Pozitsiya: ('limit', LimitItem.pk) yoki ('hafta', WeeklyRequestItem.pk) → (obj, project, total)."""
    from .models import LimitItem
    if tur == "limit":
        li = get_object_or_404(LimitItem.objects.select_related("project", "limit"), pk=pk)
        return li, li.project, li.total
    it = get_object_or_404(WeeklyRequestItem.objects.select_related("request", "request__project"), pk=pk)
    return it, it.request.project, it.total


def _moliya_adminlarga_xabar(request, matn):
    """Barcha faol adminlarga (superuser) tizim banneri + Telegram."""
    from django.contrib.auth import get_user_model
    n = 0
    for u in get_user_model().objects.filter(is_active=True, is_superuser=True).exclude(pk=request.user.pk):
        _lj_xabar_yubor(matn, u, request.user)
        n += 1
    return n


@login_required
def moliya_tahrir_sorov(request, tur, pk):
    """BUXGALTER: yozilgan to'lovlarni tahrirlash uchun admindan RUXSAT so'raydi —
    har pozitsiya uchun alohida (sabab majburiy). Ochiq so'rov bo'lsa ikkinchisi ochilmaydi."""
    from .models import MoliyaTahrirSorov
    if not _moliya_yoza_oladi(request.user):
        raise PermissionDenied("Tahrir ruxsatini faqat BUXGALTER so'raydi.")
    if tur not in ("limit", "hafta"):
        raise PermissionDenied("Noto'g'ri pozitsiya.")
    obj, p, total = _moliya_qator(tur, pk)
    _firma_yoki_403(request, p)
    qayt = _xavfsiz_referer(request, "moliya")
    if request.method != "POST":
        return redirect(qayt)
    sabab = " ".join((request.POST.get("sabab") or "").split())[:300]
    if not sabab:
        messages.error(request, "Tahrir sababini yozing.")
        return redirect(qayt)
    if not obj.moliyalar.filter(bekor=False).exists():
        messages.error(request, f"«{obj.name}» qatorida to'lov yo'q — tahrirlashga hojat yo'q.")
        return redirect(qayt)
    fk = {"limit_item": obj} if tur == "limit" else {"item": obj}
    if MoliyaTahrirSorov.objects.filter(status__in=["sorov", "ruxsat"], **fk).exists():
        messages.error(request, f"«{obj.name}» uchun ochiq so'rov allaqachon bor — admin javobini kuting.")
        return redirect(qayt)
    sr = MoliyaTahrirSorov.objects.create(soradi=request.user, sabab=sabab, **fk)
    link = request.build_absolute_uri(reverse("moliya") + f"?obyekt={p.pk}")
    n = _moliya_adminlarga_xabar(request, f"💳 Moliya: «{obj.name}» ({p.code}) to'lovlarini tahrirlashga ruxsat "
                                          f"so'raldi. Sabab: {sabab}. So'radi: {request.user.username}.\n{link}")
    messages.success(request, f"«{obj.name}» — tahrir ruxsati so'raldi (admin{'larga xabar ketdi' if n else 'ga'}). "
                              "Ruxsat berilgach qatorda «Tahrirlash» tugmasi chiqadi.")
    return redirect(qayt)


@login_required
def moliya_tahrir_javob(request, pk):
    """ADMIN: tahrir so'roviga javob — ruxsat / rad (har so'rov alohida)."""
    from django.utils import timezone
    from .models import MoliyaTahrirSorov
    if not is_admin(request.user):
        raise PermissionDenied("Tahrir ruxsatini faqat ADMIN beradi.")
    sr = get_object_or_404(MoliyaTahrirSorov.objects.select_related("soradi", "limit_item__project", "item__request__project"), pk=pk)
    p = sr.project
    _firma_yoki_403(request, p)
    qayt = _xavfsiz_referer(request, "moliya")
    if request.method != "POST":
        return redirect(qayt)
    amal = request.POST.get("amal")
    izoh = " ".join((request.POST.get("izoh") or "").split())[:300]
    if sr.status != MoliyaTahrirSorov.Status.SOROV:
        messages.error(request, "Bu so'rovga allaqachon javob berilgan.")
        return redirect(qayt)
    if amal not in ("ruxsat", "rad"):
        messages.error(request, "Amal noto'g'ri.")
        return redirect(qayt)
    sr.status = MoliyaTahrirSorov.Status.RUXSAT if amal == "ruxsat" else MoliyaTahrirSorov.Status.RAD
    sr.javob_by, sr.javob_at, sr.javob_izoh = request.user, timezone.now(), izoh
    sr.save(update_fields=["status", "javob_by", "javob_at", "javob_izoh"])
    nom = sr.qator.name
    if sr.soradi_id and sr.soradi_id != request.user.pk:
        link = request.build_absolute_uri(reverse("moliya") + f"?obyekt={p.pk}")
        _lj_xabar_yubor((f"✅ Admin «{nom}» ({p.code}) to'lovlarini tahrirlashga RUXSAT berdi — bir marta tahrirlang."
                         if amal == "ruxsat" else f"⛔ Admin «{nom}» ({p.code}) tahrir so'rovini RAD etdi.")
                        + (f" Izoh: {izoh}." if izoh else "") + f"\n{link}", sr.soradi, request.user)
    messages.success(request, f"«{nom}» — " + ("tahrirga ruxsat berildi (buxgalterga xabar ketdi)." if amal == "ruxsat" else "so'rov rad etildi."))
    return redirect(qayt)


@login_required
def moliya_tahrir_saqla(request, pk):
    """BUXGALTER (ruxsat bilan, BIR MARTA): pozitsiya to'lovlarini tahrirlaydi —
    summa/sana/usul/izoh, xato to'lovni BEKOR qiladi (o'chirilmaydi). Faol to'lovlar
    yig'indisi qator summasidan oshmasligi kerak. So'ng ruxsat yopiladi."""
    from django.utils import timezone
    from .models import Moliya, MoliyaTahrirSorov
    if not _moliya_yoza_oladi(request.user):
        raise PermissionDenied("To'lovni faqat BUXGALTER tahrirlaydi.")
    sr = get_object_or_404(MoliyaTahrirSorov.objects.select_related("limit_item__project", "item__request__project"), pk=pk)
    p = sr.project
    _firma_yoki_403(request, p)
    qayt = _xavfsiz_referer(request, "moliya")
    if request.method != "POST":
        return redirect(qayt)
    if sr.status != MoliyaTahrirSorov.Status.RUXSAT:
        messages.error(request, "Bu pozitsiya uchun admin ruxsati yo'q (yoki allaqachon ishlatilgan).")
        return redirect(qayt)
    obj = sr.qator
    total = obj.total
    with transaction.atomic():
        sr = MoliyaTahrirSorov.objects.select_for_update().get(pk=sr.pk)
        if sr.status != MoliyaTahrirSorov.Status.RUXSAT:
            messages.error(request, "Ruxsat allaqachon ishlatilgan.")
            return redirect(qayt)
        tolovlar = list(Moliya.objects.select_for_update().filter(bekor=False, **({"limit_item": obj} if sr.limit_item_id else {"item": obj})))
        yangi, jami, ozg = [], Decimal("0"), 0
        for m in tolovlar:
            pre = f"_{m.pk}"
            if request.POST.get("bekor" + pre):
                sab = " ".join((request.POST.get("bekor_sabab" + pre) or "").split())[:300]
                yangi.append((m, None, sab)); ozg += 1
                continue
            summa = _to_dec((request.POST.get("summa" + pre) or "").replace(" ", "").replace("\xa0", ""))
            if summa is None or summa <= 0 or summa > Decimal("1000000000000"):
                messages.error(request, f"«{obj.name}»: {m.sana:%d.%m.%Y} to'lovi summasi noto'g'ri.")
                return redirect(qayt)
            try:
                sana = datetime.date.fromisoformat(request.POST.get("sana" + pre, ""))
            except ValueError:
                sana = m.sana
            usul = request.POST.get("usul" + pre) or ""
            if usul not in ("kassa", "karta", "avans"):
                usul = ""
            izoh = " ".join((request.POST.get("izoh" + pre) or "").split())[:300]
            jami += summa
            yangi.append((m, (summa, sana, usul, izoh), ""))
        if jami > total + Decimal("0.01"):
            messages.error(request, f"Faol to'lovlar yig'indisi ({_money(jami)}) qator summasidan ({_money(total)}) oshib ketdi — saqlanmadi.")
            return redirect(qayt)
        now = timezone.now()
        for m, qiym, sab in yangi:
            if qiym is None:
                m.bekor, m.bekor_sabab, m.bekor_by, m.bekor_at = True, sab or "Buxgalter bekor qildi (admin ruxsati bilan)", request.user, now
                m.save(update_fields=["bekor", "bekor_sabab", "bekor_by", "bekor_at"])
                continue
            summa, sana, usul, izoh = qiym
            if (summa, sana, usul, izoh) == (m.summa, m.sana, m.usul or "", m.izoh or ""):
                continue
            if summa != m.summa and m.asl_summa is None:
                m.asl_summa = m.summa
            m.summa, m.sana, m.usul, m.izoh = summa, sana, usul, izoh
            m.tahrir_by, m.tahrir_at = request.user, now
            m.save(update_fields=["summa", "sana", "usul", "izoh", "asl_summa", "tahrir_by", "tahrir_at"])
            ozg += 1
        sr.status, sr.bajarildi_at = MoliyaTahrirSorov.Status.BAJARILDI, now
        sr.save(update_fields=["status", "bajarildi_at"])
    messages.success(request, f"«{obj.name}» — to'lovlar tahrirlandi ({ozg} ta o'zgarish). Berilgan: {_money(jami)}. Ruxsat yopildi.")
    return redirect(qayt)


@login_required
def moliya_izoh(request, li_id):
    """Buxgalter: moliyalashtirish jadvalidagi «Izoh» ustuni (limit qatori bo'yicha)."""
    from .models import LimitItem
    if not _moliya_yoza_oladi(request.user):
        raise PermissionDenied("Izohni faqat BUXGALTER yozadi.")
    li = get_object_or_404(LimitItem.objects.select_related("project"), pk=li_id)
    _firma_yoki_403(request, li.project)
    qayt = _xavfsiz_referer(request, "moliya")
    if request.method == "POST":
        li.moliya_izoh = " ".join((request.POST.get("izoh") or "").split())[:300]
        li.save(update_fields=["moliya_izoh"])
        messages.success(request, f"«{li.name}» — izoh saqlandi." if li.moliya_izoh else f"«{li.name}» — izoh o'chirildi.")
    return redirect(qayt)


@login_required
def moliya_yozish(request, item_id):
    """Buxgalter/admin: haftalik qatoriga TO'LOV yozadi (jurnal — o'chirilmaydi)."""
    from .models import Moliya
    if not _moliya_yoza_oladi(request.user):
        raise PermissionDenied("To'lovni faqat BUXGALTER yozadi.")
    it = get_object_or_404(
        WeeklyRequestItem.objects.select_related("request", "request__project"), pk=item_id)
    _firma_yoki_403(request, it.request.project)
    if request.method != "POST":
        return redirect("moliya")
    if it.request.status != WeeklyRequest.Status.APPROVED:
        messages.error(request, "Faqat TASDIQLANGAN haftalik qatoriga to'lov yoziladi.")
        return redirect("moliya")
    summa = _to_dec((request.POST.get("summa") or "").replace(" ", "").replace("\xa0", ""))
    miqdor = _to_dec(request.POST.get("miqdor"))
    izoh = " ".join((request.POST.get("izoh") or "").split())[:300]
    usul = request.POST.get("usul") or ""
    if usul not in ("kassa", "karta", "avans"):
        usul = ""
    try:
        sana = datetime.date.fromisoformat(request.POST.get("sana", ""))
    except ValueError:
        sana = datetime.date.today()
    if summa is None or summa <= 0:
        messages.error(request, "Berilgan summani kiriting (0 dan katta).")
        return redirect(_xavfsiz_referer(request, "moliya"))
    if miqdor is not None and miqdor < 0:
        miqdor = None
    # Tekshiruv + yozish BITTA tranzaksiyada, qator qulflanadi — ikki marta
    # bosilganda (yoki ikki oynadan) qarzdan ORTIQCHA to'lov yozilib qolmasin
    with transaction.atomic():
        it = (WeeklyRequestItem.objects.select_for_update()
              .select_related("request").get(pk=it.pk))
        if it.request.status != WeeklyRequest.Status.APPROVED:
            messages.error(request, "Faqat TASDIQLANGAN haftalik qatoriga to'lov yoziladi.")
            return redirect(_xavfsiz_referer(request, "moliya"))
        qarz = it.qarz
        if summa > qarz + Decimal("0.01"):
            messages.error(request,
                f"Summa qarzdan katta: qarz {_money(qarz)}, siz {_money(summa)} yozdingiz. "
                "Ortiqcha to'lov yozilmaydi.")
            return redirect(_xavfsiz_referer(request, "moliya"))
        Moliya.objects.create(item=it, summa=summa, miqdor=miqdor, izoh=izoh, usul=usul,
                              sana=sana, yozdi=request.user)
    qoldi = it.qarz
    messages.success(request,
        f"«{it.name}» — {_money(summa)} berildi deb yozildi. "
        + ("Qator TO'LIQ moliyalashtirildi ✓" if qoldi <= 0 else f"Qoldi: {_money(qoldi)}"))
    return redirect(_xavfsiz_referer(request, "moliya"))


@login_required
def xabar_yuborish(request):
    """ADMIN xodim(lar)ga xabar yuboradi. Xabar xodim oynasi tepasida ko'rinadi
    («O'qidim» bosguncha) + Telegram bog'langan bo'lsa botga ham ketadi."""
    from django.contrib.auth import get_user_model
    from .models import Xabar, XabarOqildi
    if not is_admin(request.user):
        raise PermissionDenied("Xabarni faqat admin yuboradi.")
    U = get_user_model()
    xodimlar = (U.objects.filter(is_active=True).exclude(pk=request.user.pk)
                .select_related("profile").order_by("username"))
    if request.method == "POST":
        matn = " ".join((request.POST.get("matn") or "").split())[:2000]
        muhimlik = request.POST.get("muhimlik") or "oddiy"
        if muhimlik not in ("oddiy", "muhim", "shoshilinch"):
            muhimlik = "oddiy"
        hammaga = bool(request.POST.get("hammaga"))
        ids = request.POST.getlist("kimga")
        if not matn:
            messages.error(request, "Xabar matnini yozing.")
            return redirect("xabar_yuborish")
        if not hammaga and not ids:
            messages.error(request, "Kimga yuborishni tanlang (yoki «Hammaga»).")
            return redirect("xabar_yuborish")
        x = Xabar.objects.create(matn=matn, muhimlik=muhimlik,
                                 yubordi=request.user, hammaga=hammaga)
        if hammaga:
            oluvchilar = list(xodimlar)
        else:
            oluvchilar = list(xodimlar.filter(pk__in=ids))
            x.kimga.set(oluvchilar)
        # Telegram: FAQAT o'sha xodimning o'z botiga (shaxsiy chat) — admin
        # arxiv chatiga emas. Bot bog'lanmaganlar ro'yxati adminga ko'rsatiladi.
        tg_soni = 0
        bot_yoq = []
        try:
            from django.utils import timezone as _tz
            from .auth2fa import tg_send_fon as tg_send
            belgi = {"oddiy": "📩", "muhim": "❗", "shoshilinch": "🚨"}[muhimlik]
            muh_nom = {"oddiy": "Oddiy", "muhim": "MUHIM", "shoshilinch": "SHOSHILINCH"}[muhimlik]
            vaqt = _tz.localtime().strftime("%d.%m.%Y %H:%M")
            for u in oluvchilar:
                prof = getattr(u, "profile", None)
                chat = (getattr(prof, "telegram_chat_id", "") or "").strip() if prof else ""
                if not chat:
                    bot_yoq.append(u.username)
                    continue
                if tg_send(chat, f"{belgi} Admin xabari — {muh_nom}\n"
                                 f"Yubordi: {request.user.username} · {vaqt}\n\n{matn}\n\n"
                                 f"Tizimga kirib «O'qidim» tugmasini bosing."):
                    tg_soni += 1
        except Exception:
            pass
        messages.success(request,
            f"Xabar yuborildi: {len(oluvchilar)} ta xodimga"
            + (f" (Telegram botiga ham: {tg_soni})" if tg_soni else "") + ".")
        if bot_yoq:
            messages.warning(request,
                "Bot bog'lanmagan (faqat saytda ko'radi): " + ", ".join(bot_yoq[:10])
                + (" ..." if len(bot_yoq) > 10 else ""))
        return redirect("xabar_yuborish")
    # GET — forma + yuborilgan xabarlar tarixi (kim o'qidi)
    tarix = []
    for x in Xabar.objects.select_related("yubordi").prefetch_related(
            "kimga", "oqilganlar__user").order_by("-created_at")[:30]:
        if x.hammaga:
            oluvchi_soni = xodimlar.count()
        else:
            oluvchi_soni = x.kimga.count()
        oqigan = [o.user.username for o in x.oqilganlar.all()]
        oqigan_batafsil = [
            {"kim": o.user.username, "qachon": o.oqilgan_at}
            for o in sorted(x.oqilganlar.all(), key=lambda o: o.oqilgan_at)
        ]
        if x.hammaga:
            oluvchi_nomlar = [u.username for u in xodimlar]
        else:
            oluvchi_nomlar = [u.username for u in x.kimga.all()]
        oqimagan = [n for n in oluvchi_nomlar if n not in oqigan]
        tarix.append({"obj": x, "oluvchi_soni": oluvchi_soni,
                      "oqigan": oqigan, "oqigan_soni": len(oqigan),
                      "oqigan_batafsil": oqigan_batafsil, "oqimagan": oqimagan,
                      "kimga": ("Hammaga" if x.hammaga else
                                ", ".join(u.username for u in x.kimga.all()))})
    return render(request, "projects/xabar_yuborish.html", {
        "xodimlar": xodimlar, "tarix": tarix,
    })


@login_required
def xabar_oqidim(request, pk):
    """Xodim xabarni o'qiganini belgilaydi — banner yo'qoladi."""
    from django.db.models import Q
    from .models import Xabar, XabarOqildi
    if request.method == "POST":
        # Faqat O'ZIGA yuborilgan (yoki hammaga) xabarni belgilay oladi
        x = get_object_or_404(Xabar.objects.filter(Q(hammaga=True) | Q(kimga=request.user)),
                              pk=pk)
        XabarOqildi.objects.get_or_create(xabar=x, user=request.user)
    return redirect(_xavfsiz_referer(request, "dashboard"))


@login_required
def weekly_export(request, pk):
    """Bitta haftalik so'rovni ALOHIDA Excel qilib yuklab olish."""
    w = get_object_or_404(
        WeeklyRequest.objects.select_related(
            "project", "created_by", "director_by", "approved_by"), pk=pk)
    p = _firma_yoki_403(request, w.project)

    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from django.utils.timezone import localtime as _lt

    KIND = dict(KIND_NOMI)
    STATUS = {"draft": "Qoralama", "snab": "Snabjeniye ko'rigida", "pto2": "PTO xulosasida",
              "dir": "Direktor tasdig'ida", "prov": "Proverchik tekshiruvida",
              "submitted": "Admin tasdig'ida", "approved": "Tasdiqlangan"}

    hdr_font = Font(bold=True, color="FFFFFF", size=11)
    hdr_fill = PatternFill("solid", fgColor="213145")
    tot_font = Font(bold=True, size=11)
    tot_fill = PatternFill("solid", fgColor="EFF4FF")
    thin = Side(style="thin", color="E2E8F0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    money = '#,##0'
    right = Alignment(horizontal="right")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Haftalik so'rov"
    ws["A1"] = f"{p.code} — {p.name}"
    ws["A1"].font = Font(bold=True, size=14, color="0B1C30")
    davri = f"Haftalik so'rov: {w.week_start:%d.%m.%Y} — {w.week_end:%d.%m.%Y}"
    if w.number:
        davri += f" · {w.number}"
    ws["A2"] = davri + f" · {STATUS.get(w.status, w.status)}"
    ws["A2"].font = Font(size=10, color="64748B")
    imzo = []
    if w.created_by_id:
        imzo.append(f"Kiritdi: {w.created_by.username} · {_lt(w.created_at):%d.%m.%Y %H:%M}")
    if w.director_by_id:
        imzo.append(f"Direktor: {w.director_by.username}"
                    + (f" · {_lt(w.director_at):%d.%m.%Y %H:%M}" if w.director_at else ""))
    if w.approved_by_id:
        imzo.append(f"Admin: {w.approved_by.username}"
                    + (f" · {_lt(w.approved_at):%d.%m.%Y %H:%M}" if w.approved_at else ""))
    if w.edited_by_id:
        imzo.append(f"Oxirgi tahrir: {w.edited_by.username}"
                    + (f" · {_lt(w.edited_at):%d.%m.%Y %H:%M}" if w.edited_at else ""))
    ws["A3"] = " | ".join(imzo) if imzo else ""
    ws["A3"].font = Font(size=10, color="64748B")
    ws.append([])
    hrow = 5
    # Moliya ustunlari (Berildi/Qoldi) — prorabga KO'RSATILMAYDI (moliya sirlari)
    moliya_kora = moliya_kora_oladi(request.user)
    sarlavha = ["Turi", "Nomi", "Bo'lim", "Izoh", "Birlik", "Miqdor", "Narxi", "Summa",
                "Kiritilgan sana"]
    if moliya_kora:
        sarlavha += ["Berildi (fakt)", "Qoldi (qarz)"]
    ws.append(sarlavha)
    for c in ws[hrow]:
        c.font = hdr_font
        c.fill = hdr_fill
        c.border = border
        c.alignment = Alignment(horizontal="center", vertical="center")

    for it in w.items.prefetch_related("moliyalar"):
        qator = [
            KIND.get(it.kind, it.kind), it.name, it.bolim or "", it.note or "", it.unit or "",
            float(it.quantity), float(it.unit_price), None,
            _lt(it.created_at).strftime("%d.%m.%Y %H:%M") if it.created_at else "",
        ]
        if moliya_kora:
            qator += [float(it.berildi), None]
        ws.append(qator)
        # Summa = Miqdor x Narxi — jonli formula; Qoldi = Summa − Berildi
        rr_ = ws.max_row
        ws.cell(rr_, 8).value = f"=F{rr_}*G{rr_}"
        if moliya_kora:
            ws.cell(rr_, 11).value = f"=MAX(0,H{rr_}-J{rr_})"
    oxirgi_q = ws.max_row
    pul_ustunlar = (7, 8, 10, 11) if moliya_kora else (7, 8)
    for r in range(hrow + 1, ws.max_row + 1):
        for col in pul_ustunlar:
            ws.cell(r, col).number_format = money
            ws.cell(r, col).alignment = right
        ws.cell(r, 6).number_format = '#,##0.###'   # miqdor kasrli bo'lishi mumkin
        ws.cell(r, 6).alignment = right
        for c in ws[r]:
            c.border = border
    ws.append([])
    tot_birinchi = None
    for k in KINDS:
        rr = ws.max_row + 1
        if tot_birinchi is None:
            tot_birinchi = rr
        ws.cell(rr, 7, KIND[k] + " jami:").font = tot_font
        c = ws.cell(rr, 8)
        if oxirgi_q > hrow:
            c.value = f'=SUMIF($A${hrow + 1}:$A${oxirgi_q},"{KIND[k]}",$H${hrow + 1}:$H${oxirgi_q})'
        else:
            c.value = 0
        c.number_format = money
        c.font = tot_font
        c.fill = tot_fill
    rr = ws.max_row + 1
    ws.cell(rr, 7, "JAMI:").font = Font(bold=True, size=12)
    c = ws.cell(rr, 8)
    c.value = f"=SUM(H{tot_birinchi}:H{rr - 1})"
    c.number_format = money
    c.font = Font(bold=True, size=12, color="2563EB")
    c.fill = tot_fill
    ws.freeze_panes = "A6"
    for i, kenglik in enumerate([16, 32, 18, 28, 10, 12, 14, 16, 17, 15, 15], start=1):
        ws.column_dimensions[get_column_letter(i)].width = kenglik

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = ('attachment; filename="haftalik_%s_%s.xlsx"'
                                   % (p.code, w.week_start.strftime("%Y%m%d")))
    return resp


# ===================== PRORAB -> PTO material so'rovi =====================

@login_required
def material_sorov(request):
    """Material so'rovlari ro'yxati.
    Prorab — o'zi yuborgan so'rovlar. PTO — o'z obyektlariga kelgan so'rovlar."""
    from .models import MaterialRequest
    vp = visible_projects(request.user)
    reqs = (MaterialRequest.objects.filter(project__in=vp)
            .select_related("project", "created_by", "decided_by")
            .prefetch_related("items"))
    from .roles import is_prov as _is_prov_ms
    prorab = is_prorab(request.user)
    pto = is_pto(request.user)
    if prorab:
        # Prorab faqat O'ZI yuborgan so'rovlarni ko'radi
        reqs = reqs.filter(created_by=request.user)
    STATUS_CLS = {"pending": "warn", "accepted": "ok", "rejected": "bad"}
    rows = []
    for r in reqs:
        rows.append({
            "obj": r,
            "status_disp": r.get_status_display(),
            "status_cls": STATUS_CLS.get(r.status, ""),
            "soni": r.items.count(),
            "is_pending": r.status == MaterialRequest.Status.PENDING,
        })
    return render(request, "projects/material_sorov.html", {
        "rows": rows,
        "is_prorab": prorab,
        "is_pto": pto,
        "is_prov": _is_prov_ms(request.user),
        "kutayotgan": sum(1 for x in rows if x["is_pending"]),
    })


@login_required
def material_sorov_add(request):
    """Prorab yangi material so'rovini yaratadi va PTOga yuboradi."""
    from .models import MaterialRequest, MaterialRequestItem
    if not (is_prorab(request.user) or request.user.is_superuser):
        raise PermissionDenied("Prorab so'rovini faqat prorab yuboradi.")
    vp = visible_projects(request.user).order_by("code")
    if request.method == "POST":
        pid = request.POST.get("project") or ""
        p = vp.filter(pk=pid).first()
        if not p:
            messages.error(request, "Obyekt tanlang (faqat sizga biriktirilgan).")
            return redirect("material_sorov_add")
        names = request.POST.getlist("name")
        units = request.POST.getlist("unit")
        qtys = request.POST.getlist("quantity")
        notes = request.POST.getlist("note")
        kinds = request.POST.getlist("kind")
        prices = request.POST.getlist("unit_price")
        valid_kind = set(KINDS)
        satrlar = []
        for i in range(len(names)):
            nom = (names[i] or "").strip()
            if not nom:
                continue
            q = _to_dec(qtys[i] if i < len(qtys) else None)
            if q is None or q <= 0:
                continue
            narx = _to_dec(prices[i] if i < len(prices) else None) or Decimal("0")
            if narx < 0:
                narx = Decimal("0")
            satrlar.append(MaterialRequestItem(
                kind=(kinds[i] if i < len(kinds) and kinds[i] in valid_kind else "material"),
                name=nom, unit=(units[i] if i < len(units) else "").strip()[:32],
                quantity=q, unit_price=narx,
                note=(notes[i] if i < len(notes) else "").strip()[:500],
            ))
        if not satrlar:
            messages.error(request, "Kamida bitta material qatori kiriting.")
            return redirect("material_sorov_add")
        req = MaterialRequest.objects.create(
            project=p, created_by=request.user,
            note=(request.POST.get("note_umumiy") or "").strip()[:500],
        )
        for it in satrlar:
            it.request = req
        MaterialRequestItem.objects.bulk_create(satrlar)
        messages.success(request, f"Prorab so'rovi PTOga yuborildi ({len(satrlar)} qator).")
        return redirect("material_sorov")
    return render(request, "projects/material_sorov_add.html", {"obyektlar": vp})


@login_required
def material_sorov_korish(request, pk):
    """So'rovni ko'rish. PTO tahrirlab qabul/rad qilishi mumkin."""
    from .models import MaterialRequest
    req = get_object_or_404(
        MaterialRequest.objects.select_related("project", "created_by", "decided_by"), pk=pk)
    _firma_yoki_403(request, req.project)
    # Prorab faqat O'ZI yaratgan so'rovni ochadi (ro'yxatdagi filtr bilan bir xil)
    if is_prorab(request.user) and req.created_by_id != request.user.id:
        raise PermissionDenied("Bu so'rov sizniki emas.")
    pto = is_pto(request.user) or request.user.is_superuser
    items = list(req.items.all())
    jami = Decimal("0")
    for it in items:
        it.price_str = _money(it.unit_price)
        it.total_str = _money(it.total)
        jami += it.total
    pending = req.status == MaterialRequest.Status.PENDING
    return render(request, "projects/material_sorov_korish.html", {
        "req": req,
        "items": items,
        "jami_str": _money(jami),
        "status_disp": req.get_status_display(),
        # PTO (va admin) so'rovni ISTALGAN holatda tahrirlaydi: qator qo'shadi/o'chiradi,
        # hal qilinganini qayta ochadi; qaror tugmalari — faqat kutilayotganda
        "can_edit": pto,
        "can_decide": pto and pending,
        "is_pending": pending,
    })


@login_required
def material_sorov_action(request, pk):
    """PTO material so'rovini tahrirlab QABUL yoki RAD qiladi."""
    from django.utils import timezone

    from .models import MaterialRequest
    req = get_object_or_404(MaterialRequest.objects.select_related("project"), pk=pk)
    _firma_yoki_403(request, req.project)
    if not (is_pto(request.user) or request.user.is_superuser):
        raise PermissionDenied("So'rovni faqat PTO qabul/rad qiladi.")
    if request.method != "POST":
        return redirect("material_sorov_korish", pk=pk)
    action = request.POST.get("action")
    S = MaterialRequest.Status
    if action in ("accept", "reject") and req.status != S.PENDING:
        messages.error(request, "Bu so'rov allaqachon hal qilingan — avval «Qayta ochish»ni bosing.")
        return redirect("material_sorov_korish", pk=pk)
    if action == "reopen":
        # Hal qilingan so'rov yana PTO ko'rib chiqishiga qaytadi (tahrirlab qayta qaror)
        req.status = S.PENDING
        req.decided_by = None
        req.decided_at = None
        req.reject_note = ""
        req.save(update_fields=["status", "decided_by", "decided_at", "reject_note"])
        messages.info(request, "So'rov qayta ochildi — tahrirlab qabul yoki rad qilishingiz mumkin.")
        return redirect("material_sorov_korish", pk=pk)

    # PTO tahriri: qatorlarni yangilash / o'chirish / yangi qator (istalgan holatda)
    if action in ("accept", "save"):
        from .models import MaterialRequestItem
        valid_kind = set(KINDS)
        ochir = set(request.POST.getlist("del"))
        for it in req.items.all():
            if str(it.id) in ochir:
                if req.items.count() <= 1:
                    messages.error(request, "Oxirgi qatorni o'chirib bo'lmaydi — o'rniga so'rovni rad eting.")
                else:
                    it.delete()
                continue
            q = _to_dec(request.POST.get(f"qty_{it.id}"))
            nm = (request.POST.get(f"name_{it.id}") or "").strip()
            un = (request.POST.get(f"unit_{it.id}") or "").strip()[:32]
            kd = (request.POST.get(f"kind_{it.id}") or "").strip()
            pr = _to_dec(request.POST.get(f"price_{it.id}"))
            changed = False
            if q is not None and q > 0 and q != it.quantity:
                it.quantity = q; changed = True
            if nm and nm != it.name:
                it.name = nm; changed = True
            if un != it.unit:
                it.unit = un; changed = True
            if kd in valid_kind and kd != it.kind:
                it.kind = kd; changed = True
            if pr is not None and pr >= 0 and pr != it.unit_price:
                it.unit_price = pr; changed = True
            iz = (request.POST.get(f"note_{it.id}") or "").strip()[:500]
            if iz != it.note:
                it.note = iz; changed = True
            if changed:
                it.save(update_fields=["quantity", "name", "unit", "kind", "unit_price", "note"])
        # Yangi qatorlar (PTO qo'shgan)
        names = request.POST.getlist("new_name")
        yangi = []
        for i, nom in enumerate(names):
            nom = (nom or "").strip()
            q = _to_dec(request.POST.getlist("new_quantity")[i] if i < len(request.POST.getlist("new_quantity")) else None)
            if not nom or q is None or q <= 0:
                continue
            def _g(k):
                lst = request.POST.getlist(k)
                return lst[i] if i < len(lst) else ""
            narx = _to_dec(_g("new_unit_price")) or Decimal("0")
            kd = _g("new_kind")
            yangi.append(MaterialRequestItem(
                request=req, kind=(kd if kd in valid_kind else "material"), name=nom[:255],
                unit=_g("new_unit").strip()[:32], quantity=q, unit_price=max(narx, Decimal("0")),
                note=_g("new_note").strip()[:500]))
        if yangi:
            MaterialRequestItem.objects.bulk_create(yangi)

    if action == "accept":
        req.status = S.ACCEPTED
        req.decided_by = request.user
        req.decided_at = timezone.now()
        req.save(update_fields=["status", "decided_by", "decided_at"])
        messages.success(request, "So'rov qabul qilindi (ro'yxatga saqlandi).")
    elif action == "reject":
        req.status = S.REJECTED
        req.decided_by = request.user
        req.decided_at = timezone.now()
        req.reject_note = (request.POST.get("reject_note") or "").strip()[:500]
        req.save(update_fields=["status", "decided_by", "decided_at", "reject_note"])
        messages.info(request, "So'rov rad etildi.")
    elif action == "save":
        messages.success(request, "O'zgarishlar saqlandi.")
    else:
        messages.error(request, "Noma'lum amal.")
    return redirect("material_sorov_korish", pk=pk)


# ================== LIMIT JADVALI (Sirdaryo ko'rinishi) ==================

def _hafta_qatorlarini_yangila(w, yangi, ws_, we_, user):
    """Yopiq haftaning qatorlarini jadvaldagi ro'yxat bilan O'RNIDA yangilaydi.
    Bir xil nom+bo'lim qatorlar tartib bo'yicha juftlanadi; to'lov (Moliya) yozilgan
    qator o'chirilmaydi va to'langandan kam qilinmaydi. Xato matni yoki None."""
    from collections import defaultdict, deque
    from django.utils import timezone as _tz
    mavjud = defaultdict(deque)
    for it in w.items.all().order_by("id"):
        mavjud[_lj_key(it.name, it.bolim)].append(it)
    reja_yangila, reja_yarat = [], []
    for y in yangi:
        navbat = mavjud.get(_lj_key(y.name, y.bolim))
        if navbat:
            it = navbat.popleft()
            yangi_total = (y.quantity * y.unit_price).quantize(Decimal("0.01"))
            if yangi_total < it.berildi:
                return (f"«{it.name}» qatoriga {_money(it.berildi)} to'lov yozilgan — "
                        "summani to'lovdan kamaytirib bo'lmaydi.")
            reja_yangila.append((it, y))
        else:
            reja_yarat.append(y)
    ochiriladi = [it for nav in mavjud.values() for it in nav]
    for it in ochiriladi:
        if it.moliyalar.filter(bekor=False).exists():
            return (f"«{it.name}» qatoriga to'lov yozilgan — uni o'chirib bo'lmaydi. "
                    "Avval buxgalter bilan hal qiling.")
    with transaction.atomic():
        for it, y in reja_yangila:
            it.kind, it.unit = y.kind, y.unit
            it.quantity, it.unit_price, it.note = y.quantity, y.unit_price, y.note
            it.save(update_fields=["kind", "unit", "quantity", "unit_price", "note"])
        for it in ochiriladi:
            it.delete()
        for y in reja_yarat:
            y.request = w
        if reja_yarat:
            WeeklyRequestItem.objects.bulk_create(reja_yarat)
        w.week_start, w.week_end = ws_, we_
        w.edited_by, w.edited_at = user, _tz.now()
        w.tahrir_ruxsat = False          # ruxsat bir martalik
        w.save(update_fields=["week_start", "week_end", "edited_by", "edited_at", "tahrir_ruxsat"])
    return None


def _lj_key(name, bolim):
    return (name or "").strip().lower() + "|" + (bolim or "").strip().lower()


def _lj_guruhlar(items, fakt):
    """Limit qatorlari (LimitItem YOKI zanjirdagi LimitChangeItem) bo'lim guruhlari bilan.
    Bir xil nom+bo'lim BIR NECHTA qatorda bo'lsa, fakt TAQSIMLANADI:
    har qator o'z limitigacha to'ladi, ortig'i keyingisiga; eng oxirgisiga
    qolgan hammasi (haqiqiy oshish ham shu yerda ko'rinadi)."""
    items = list(items)
    dublikat_soni = {}
    for li in items:
        k0 = _lj_key(li.name, li.bolim)
        dublikat_soni[k0] = dublikat_soni.get(k0, 0) + 1
    fakt_kalgan = {k0: {"qty": v["qty"], "sum": v["sum"]} for k0, v in fakt.items()}
    korilgan_dubl = {}
    _g = {}
    for li in items:
        kal = (li.bolim or "").strip()
        g = _g.get(kal)
        if g is None:
            g = _g[kal] = {"bolim": kal, "masul": (li.masul or "").strip(), "rows": []}
        if not g["masul"] and (li.masul or "").strip():
            g["masul"] = li.masul.strip()
        k = _lj_key(li.name, li.bolim)
        rem = fakt_kalgan.get(k)
        korilgan_dubl[k] = korilgan_dubl.get(k, 0) + 1
        oxirgi_dubl = korilgan_dubl[k] == dublikat_soni.get(k, 1)
        f_qty = Decimal("0")
        f_sum = Decimal("0")
        if rem is not None and (rem["qty"] > 0 or rem["sum"] > 0):
            if oxirgi_dubl:
                f_qty, f_sum = rem["qty"], rem["sum"]          # qolgan hammasi
            else:
                f_qty = min(li.quantity, rem["qty"])
                # summa — miqdorga proporsional (o'rtacha narxda)
                f_sum = (rem["sum"] * f_qty / rem["qty"]).quantize(Decimal("0.01")) \
                    if rem["qty"] else Decimal("0")
            rem["qty"] -= f_qty
            rem["sum"] -= f_sum
        bq = getattr(li, "boshlangich_qty", None) or Decimal("0")
        bs = getattr(li, "boshlangich_sum", None) or Decimal("0")
        _ca = getattr(li, "created_at", None)
        g["rows"].append({
            "sana": f"{_ca:%d.%m.%Y}" if _ca else "",
            "id": li.pk, "asl": getattr(li, "asl_id", None),
            "key": k, "name": li.name, "izoh": li.note or "", "unit": li.unit or "",
            "kind": li.kind, "bolim": kal,
            "vol": float(li.quantity), "price": float(li.unit_price),
            # Бажарилган = limitgacha (qo'lda) + tasdiqlangan haftaliklar
            "bq": float(bq), "bs": float(bs),
            "wq": float(f_qty), "ws": float(f_sum),
            "fakt_qty": float(f_qty + bq), "fakt_sum": float(f_sum + bs),
        })
    guruhlar = [g for kk, g in _g.items() if kk] + [g for kk, g in _g.items() if not kk]
    nr = 0
    for g in guruhlar:
        for r in g["rows"]:
            nr += 1
            r["nr"] = nr
    return guruhlar


def _lj_taklif_huquq(user, req):
    """Zanjirdagi limit so'rovini (taklifni) KIM tahrirlay oladi: admin — istalgan
    bosqichda, snabjeniye — o'z (snab) bosqichida, PTO — xulosa (pto2) bosqichida.
    QORALAMA (draft) — Умумийni tahrirlash huquqi borlar: zanjir ishtirokchilari va
    admin; qatorli tasdiqlangan limit bo'lsa faqat admin ruxsati bilan."""
    from .roles import is_snab as _is_snab
    if req is None:
        return False
    if req.status == "draft":
        # Qulf (tasdiqlangan limit, ruxsatsiz) bo'lsa ham qoralama davom ettiriladi —
        # lekin mavjud (asl) qatorlar o'zgarmaydi (_lj_qulf_normalla), faqat yangilari
        zanjir = is_pto(user) or _is_snab(user) or is_director(user)
        return is_admin(user) or zanjir
    if req.status not in LIM_JARAYON:
        return False
    from .roles import is_prov as _is_prov, is_asosiy_admin as _asos
    return (is_admin(user)
            or (req.status == "snab" and _is_snab(user))
            or (req.status == "pto2" and is_pto(user))
            or (req.status == "dir" and ((is_director(user) and not user.is_superuser) or _asos(user)))
            or (req.status == "prov" and _is_prov(user)))


def _lj_taklif_token(req):
    """Taklifning joriy holati belgisi — eskirgan oynadan ustiga yozib yubormaslik uchun."""
    return "%s|%s|%s|%d" % (req.pk, req.status,
                            req.edited_at.isoformat() if req.edited_at else "",
                            req.proposed_items.count())


_LJ_CHEGARA = Decimal("1000000000000")   # 10^12 — model (max_digits=18) sig'imidan ancha past


def _lj_qator(e):
    """Jadvaldan kelgan bitta limit qatorini tozalaydi.
    Qaytaradi: (qator dict, xato matni). Nomsiz qator — (None, None) (tashlab yuboriladi)."""
    if not isinstance(e, dict):
        return None, None
    nm = " ".join(str(e.get("name") or "").split())[:255]
    if not nm:
        return None, None
    q = _to_dec(str(e.get("vol") or "0")) or Decimal("0")
    pr = _to_dec(str(e.get("price") or "0")) or Decimal("0")
    if not (q.is_finite() and pr.is_finite()) or not (0 <= q < _LJ_CHEGARA and 0 <= pr < _LJ_CHEGARA):
        return None, f"«{nm}»: hajm/narx noto'g'ri (manfiy yoki juda katta) — saqlanmadi."
    kind = str(e.get("kind") or "material")
    try:
        asl_id = int(e.get("asl")) if e.get("asl") not in (None, "", 0) else None
    except (TypeError, ValueError):
        asl_id = None
    # Limitgacha bajarilgan (qo'lda): miqdor va summa; summa yozilmasa — miqdor × narx
    bq = _to_dec(str(e.get("bq") or "0")) or Decimal("0")
    bs = _to_dec(str(e.get("bs") or "0")) or Decimal("0")
    if not (bq.is_finite() and bs.is_finite()) or not (0 <= bq < _LJ_CHEGARA and 0 <= bs < _LJ_CHEGARA):
        return None, f"«{nm}»: бажарилган қисми noto'g'ri (manfiy yoki juda katta) — saqlanmadi."
    if bq > 0 and bs == 0:
        bs = bq * pr
    return {"asl_id": asl_id, "kind": kind if kind in KINDS else "material", "name": nm,
            "unit": " ".join(str(e.get("unit") or "").split())[:32],
            # Bazadagi aniqlik bilan bir xil yaxlitlash — summa qatorlar bilan mos tursin
            "quantity": q.quantize(Decimal("0.001")), "unit_price": pr.quantize(Decimal("0.01")),
            "boshlangich_qty": bq.quantize(Decimal("0.001")), "boshlangich_sum": bs.quantize(Decimal("0.01")),
            "note": " ".join(str(e.get("note") or "").split())[:500],
            "bolim": " ".join(str(e.get("bolim") or "").split())[:200],
            "masul": " ".join(str(e.get("masul") or "").split())[:120]}, None


def _lj_qator_dict(li):
    """LimitItem -> jadval qatori dict (taqqoslash/taklif uchun)."""
    return {"asl_id": li.pk, "kind": li.kind, "name": li.name, "unit": li.unit or "",
            "quantity": li.quantity, "unit_price": li.unit_price,
            "boshlangich_qty": li.boshlangich_qty or Decimal("0"),
            "boshlangich_sum": li.boshlangich_sum or Decimal("0"),
            "note": li.note or "", "bolim": li.bolim or "", "masul": li.masul or ""}


def _lj_qulf(lim):
    """Умумий qulfmi: qatorli limit tasdiqlangan va admin ruxsat bermagan."""
    return lim.items.exists() and not lim.tahrir_ruxsat


def _lj_qulf_normalla(lim, items):
    """Qulf holatida QORALAMA: mavjud (asl) qatorlar BAZADAGIDEK qoladi va hech biri
    tushib qolmaydi — faqat YANGI qatorlar qo'shiladi (zanjir orqali). Qaytaradi:
    (items, sums)."""
    asl = {li.pk: _lj_qator_dict(li) for li in lim.items.all().order_by("id")}
    yangi = []
    bor = set()
    for it in items:
        a = it.get("asl_id")
        if a in asl:
            bor.add(a)
        else:
            it["asl_id"] = None
            yangi.append(it)
    natija = [asl[pk] for pk in asl] + yangi
    sums = {k: Decimal("0") for k in KINDS}
    for it in natija:
        sums[it["kind"]] += (it["quantity"] * it["unit_price"]).quantize(Decimal("0.01"))
    return natija, sums


def _lj_taklif_saqla(request, lim, payload, qayt_kerak=True):
    """Zanjirdagi taklif qatorlarini limit jadvalining O'ZIDAN saqlash
    (avval alohida «so'rovni tahrirlash» formasi edi)."""
    p = lim.project
    from django.utils import timezone as _tz
    qayt = redirect(_lj_url(lim))
    # Avval HUQUQ — huquqsiz foydalanuvchi tekshiruv xabarlarini ham ko'rmaydi
    req = lim.requests.filter(status__in=LIM_JARAYON + ["draft"]).order_by("-id").first()
    if req is None:
        messages.error(request, "Zanjirda limit so'rovi yo'q — sahifa yangilandi.")
        return qayt
    if not _lj_taklif_huquq(request.user, req):
        raise PermissionDenied("Bu bosqichda so'rovni siz tahrirlay olmaysiz.")
    items = []
    sums = {k: Decimal("0") for k in KINDS}
    for e in payload.get("req_items") or []:
        it, xato = _lj_qator(e)
        if xato:
            messages.error(request, xato)
            return qayt
        if it is None:
            continue
        items.append(it)
        sums[it["kind"]] += (it["quantity"] * it["unit_price"]).quantize(Decimal("0.01"))
    if not items:
        messages.error(request, "Kamida bitta qator bo'lishi kerak — saqlanmadi.")
        return qayt
    ozimniki = set(lim.items.values_list("pk", flat=True))
    for it in items:
        if it["asl_id"] not in ozimniki:
            it["asl_id"] = None
    with transaction.atomic():
        # Qayta tekshiruv: sahifa ochiq turganda so'rov boshqa bosqichga o'tgan yoki
        # boshqa foydalanuvchi tahrirlagan bo'lsa — eski oynadan ustiga yozilmaydi
        req = (LimitChangeRequest.objects.select_for_update()
               .filter(project=p, status__in=LIM_JARAYON + ["draft"]).order_by("-id").first())
        if req is None or _lj_taklif_token(req) != str(payload.get("token") or ""):
            messages.error(request, "So'rov siz ochgandan keyin o'zgargan (boshqa bosqich yoki "
                                    "boshqa foydalanuvchi tahriri) — sahifa yangilandi, qayta kiriting.")
            return qayt
        if not _lj_taklif_huquq(request.user, req):
            raise PermissionDenied("Bu bosqichda so'rovni siz tahrirlay olmaysiz.")
        if req.status == "draft" and _lj_qulf(lim):
            items, sums = _lj_qulf_normalla(lim, items)
        req.proposed_items.all().delete()
        LimitChangeItem.objects.bulk_create(
            [LimitChangeItem(request=req, **it) for it in items])
        req.new_material = sums["material"]
        req.new_labor = sums["labor"]
        req.new_machinery = sums["machinery"]
        req.new_other = sums["other"]
        req.edited_by = request.user
        req.edited_at = _tz.now()
        req.save(update_fields=["new_material", "new_labor", "new_machinery",
                                "new_other", "edited_by", "edited_at"])
    if req.status == "draft":
        messages.success(request, f"Qoralama saqlandi ({len(items)} qator, umumiy: {_money(req.new_total)}). "
                                  "Keyin davom ettirishingiz yoki «Лимитни юбориш» bilan zanjirga jo'natishingiz mumkin.")
    else:
        messages.success(request, f"Taklif saqlandi ({len(items)} qator, yangi umumiy: "
                                  f"{_money(req.new_total)}). Endi keyingi bosqichga yuborishingiz mumkin.")
    return qayt if qayt_kerak else None


def _lj_qoralama_yarat(request, lim, payload):
    """Умумий tahriri tugallanmagan bo'lsa — jadvaldagi TO'LIQ ro'yxat (mavjud
    qatorlar `asl` bilan + yangi qatorlar) serverga QORALAMA bo'lib saqlanadi;
    keyin istalgan qurilmadan o'sha joyidan davom ettiriladi."""
    p = lim.project
    qayt = redirect(_lj_url(lim))
    if lim.requests.filter(status__in=LIM_JARAYON).exists():
        messages.error(request, "Limit so'rovi zanjirda turibdi — qoralama saqlab bo'lmaydi.")
        return qayt
    items, sums = [], {k: Decimal("0") for k in KINDS}
    for e in payload.get("req_items") or []:
        it, xato = _lj_qator(e)
        if xato:
            messages.error(request, xato)
            return qayt
        if it is not None:
            items.append(it)
            sums[it["kind"]] += (it["quantity"] * it["unit_price"]).quantize(Decimal("0.01"))
    if not items:
        messages.error(request, "Qoralama uchun kamida bitta qator kiriting.")
        return qayt
    return _lj_qoralama_yoz(request, lim, items, sums)


def _lj_qoralama_yoz(request, lim, items, sums):
    """Tayyor qatorlar ro'yxatini QORALAMA bo'lib yozadi (eskisi almashadi)."""
    p = lim.project
    qayt = redirect(_lj_url(lim))
    if _lj_qulf(lim):
        items, sums = _lj_qulf_normalla(lim, items)
    ozimniki = set(lim.items.values_list("pk", flat=True))
    for it in items:
        if it["asl_id"] not in ozimniki:
            it["asl_id"] = None
    with transaction.atomic():
        lim.requests.filter(status="draft").delete()
        q = LimitChangeRequest.objects.create(
            project=p, limit=lim, status="draft",
            old_material=p.limit_material, old_labor=p.limit_labor,
            old_machinery=p.limit_machinery, old_other=p.limit_other,
            new_material=sums["material"], new_labor=sums["labor"],
            new_machinery=sums["machinery"], new_other=sums["other"],
            reason="Qoralama (limit jadvali)", requested_by=request.user)
        LimitChangeItem.objects.bulk_create([LimitChangeItem(request=q, **it) for it in items])
    messages.success(request, f"Qoralama saqlandi ({len(items)} qator). Zanjirga YUBORILMADI — "
                              "keyin shu yerdan davom ettirasiz yoki «Лимитни юбориш»ni bosasiz.")
    return qayt


def _lj_qoralama_amal(request, lim, amal):
    """Qoralama bilan amal: send — zanjirga yuborish; apply — admin to'g'ridan
    qo'llaydi; delete — o'chirish."""
    p = lim.project
    qayt = redirect(_lj_url(lim))
    q = lim.requests.filter(status="draft").order_by("-id").first()
    if q is None:
        messages.error(request, "Qoralama topilmadi — sahifa yangilandi.")
        return qayt
    if not _lj_taklif_huquq(request.user, q):
        raise PermissionDenied("Qoralama bilan ishlash huquqi yo'q.")
    if amal == "delete":
        q.delete()
        messages.info(request, "Qoralama o'chirildi.")
        return qayt
    if not q.proposed_items.exists():
        messages.error(request, "Qoralamada qator yo'q — avval qatorlarni kiritib saqlang.")
        return qayt
    if lim.requests.filter(status__in=LIM_JARAYON).exists():
        messages.error(request, "Limit so'rovi zanjirda turibdi — avval u yakunlansin.")
        return qayt
    from django.utils import timezone as _tz
    if amal == "apply":
        if not is_admin(request.user):
            raise PermissionDenied("To'g'ridan qo'llashni faqat admin qiladi.")
        if lim.items.exists() and not lim.tahrir_ruxsat:
            messages.error(request, "Tasdiqlangan limit qulf — avval «Таҳрирга рухсат бериш».")
            return qayt
        with transaction.atomic():
            q.status = "adm"
            q.requested_by = q.requested_by or request.user
            q.save(update_fields=["status", "requested_by"])
            q.approve(request.user)
            if lim.tahrir_ruxsat:
                lim.tahrir_ruxsat = False
                lim.save(update_fields=["tahrir_ruxsat"])
        p.refresh_from_db()
        messages.success(request, f"Умумий limit qo'llandi: {_money(p.budget_total)}.")
        return qayt
    if amal == "send":
        _st = _limit_boshlangich(p)
        with transaction.atomic():
            q.status = _st
            q.requested_by = request.user
            q.created_at = _tz.now()
            q.old_material, q.old_labor = p.limit_material, p.limit_labor
            q.old_machinery, q.old_other = p.limit_machinery, p.limit_other
            q.reason = "Limit jadvalidan tahrir"
            q.save()
            if lim.tahrir_ruxsat:
                lim.tahrir_ruxsat = False   # ruxsat bir martalik
                lim.save(update_fields=["tahrir_ruxsat"])
        messages.success(request, _limit_yubor_xabar(_st))
        return qayt
    messages.error(request, "Noma'lum amal.")
    return qayt


@login_required
def limit_jadval(request, pk):
    """Bitta katta jadval: UMUMIY | BAJARILGAN | QOLGAN | tanlangan HAFTA.
    Hafta tablari: tasdiqlangan/yuborilganlar qulflangan, qoralama tahrirlanadi.
    Qoralama ustuni shu yerdan to'ldiriladi -> oddiy haftalik so'rov bo'lib
    direktor-admin zanjiridan o'tadi. Narx o'zgarsa qator bo'linadi (bo'lak)."""
    import json as _json
    from .roles import is_snab as _is_snab, is_asosiy_admin as _asosiy_adm
    p = get_object_or_404(Project, pk=pk)
    _firma_yoki_403(request, p)
    lim = _lj_joriy(p, request)          # RAQAMLI limit (?limit=N; bo'lmasa joriy)
    can_edit = is_pto(request.user) or is_admin(request.user)
    # УМУМИЙ ustunini tahrirlash huquqi — zanjirning HAR BIR ishtirokchisi
    # (PTO/snab/direktor). LEKIN limit ADMIN TOMONIDAN TASDIQLANGACH qulflanadi:
    # faqat admin `limit_tahrir_ruxsat` bergandagina qайта тahrirlanadi
    # (ruxsat bir martalik — so'rov yuborilgach o'zi o'chadi). Admin doim erkin.
    zanjir_ishtirokchi = (is_pto(request.user) or _is_snab(request.user)
                          or is_director(request.user))
    limit_bor = (p.budget_total or Decimal("0")) > 0
    # Limit QATORLARI bormi. Eski usulda faqat SUMMA kiritilgan obyektlarda
    # (budget > 0, qator yo'q) qulflaydigan narsa yo'q — qatorlar shu jadvalda
    # kiritiladi va tasdiqlangach eski summa o'rnini bosadi.
    amaldagi_bor = lim.items.exists()
    # Tasdiqlangan limit HAMMA uchun (admin ham) qulf — faqat admin bergan
    # «Таҳрирга рухсат» bilan ochiladi
    lim_edit_huquq = (is_admin(request.user) or zanjir_ishtirokchi) and (
        not amaldagi_bor or lim.tahrir_ruxsat)
    # Tasdiqlangan (qulf) limitga ham YANGI blok/qator qo'shish mumkin — faqat
    # zanjir orqali (admin ham), mavjud qatorlar tegilmaydi
    lim_qosh_huquq = (is_admin(request.user) or zanjir_ishtirokchi) and not lim_edit_huquq

    haftalar = list(WeeklyRequest.objects.filter(limit=lim)
                    .exclude(status="rejected")
                    .order_by("week_start", "id")
                    .prefetch_related("items"))
    draft = next((w for w in haftalar if w.status == "draft"), None)

    # Zanjirdagi limit so'rovi (taklif) — shu jadvalning o'zida ko'rinadi/tahrirlanadi
    lim_pend_obj = lim.requests.filter(status__in=LIM_JARAYON).order_by("-id").first()
    # Zanjirga yuborilmagan QORALAMA (tugallanmagan kiritish) — xuddi taklif kabi ochiladi
    qoralama_obj = (None if lim_pend_obj
                    else lim.requests.filter(status="draft").order_by("-id").first())
    taklif_obj = lim_pend_obj or qoralama_obj
    taklif_huquq = _lj_taklif_huquq(request.user, taklif_obj)
    taklif_bor = bool(taklif_obj) and taklif_obj.proposed_items.exists()
    # Ko'rinish: zanjir payti odatda TAKLIF ko'rsatiladi; amaldagi limit qatorlari
    # bo'lsa «?korinish=amaldagi» bilan haftalik ishga o'tiladi
    taklif_view = bool(taklif_obj) and (taklif_bor or taklif_huquq) and (
        request.GET.get("korinish") != "amaldagi")
    _qs = "" if taklif_view or not taklif_obj else "?korinish=amaldagi"

    # Admin ruxsati bilan YOPIQ hafta tahrirlansa — saqlangach (yoki xato bo'lsa) sahifa
    # o'sha haftada ochiladi; aks holda qoralama haftaga o'tib, «saqlanmadi» tuyulardi
    _qayt_hafta = []

    def _qayt():
        url = _lj_url(lim) + (_qs.replace("?", "&") if _qs else "")
        if _qayt_hafta:
            url += ("&" if "?" in url else "?") + f"hafta={_qayt_hafta[0]}"
        return redirect(url)

    # ---------- POST: qoralamani saqlash / tasdiqqa yuborish ----------
    if request.method == "POST":
        if request.POST.get("qoralama_amal"):
            return _lj_qoralama_amal(request, lim, request.POST.get("qoralama_amal"))
        try:
            payload = _json.loads(request.POST.get("payload") or "{}")
        except ValueError:
            messages.error(request, "Ma'lumot o'qilmadi — qayta urinib ko'ring.")
            return _qayt()
        if not isinstance(payload, dict):
            payload = {}
        # Zanjirdagi TAKLIFni saqlash — bosqich egasi (snab/PTO xulosa/admin)
        if payload.get("action") == "req_save":
            return _lj_taklif_saqla(request, lim, payload)
        # Taklif/qoralama tahririda yashil «Сақлаш» / «Ҳафтани ёпиш»: avval taklif
        # qatorlari saqlanadi, keyin HAFTALIK (xato bo'lsa — to'xtaydi)
        if (payload.get("action") in ("save", "submit") and taklif_view and taklif_huquq
                and isinstance(payload.get("req_items"), list)):
            r_ = _lj_taklif_saqla(request, lim, payload, qayt_kerak=False)
            if r_ is not None:
                return r_
        if payload.get("action") == "draft_save":
            if not (lim_edit_huquq or lim_qosh_huquq):
                raise PermissionDenied("Умумий limitni tahrirlash huquqi yo'q.")
            return _lj_qoralama_yarat(request, lim, payload)
        if not (can_edit or lim_edit_huquq):
            raise PermissionDenied("Bu jadvalni tahrirlash huquqi yo'q.")
        # Haftalik qatorlar/yopish — faqat PTO yoki admin
        if (payload.get("items") or payload.get("action") == "submit") and not can_edit:
            raise PermissionDenied("Haftalik ustunini faqat PTO yoki admin to'ldiradi.")
        # Faqat Умумий tahrirlanayotgan bo'lsa (snab/direktor) — hafta sanasi shart emas
        faqat_limit = not payload.get("items") and payload.get("action") != "submit"
        # Admin ruxsat bergan YOPIQ hafta tahrirlanmoqdami (qoralama o'rniga)?
        ruxsat_hafta = None
        try:
            _hid = int(payload.get("hafta_id") or 0)
        except (TypeError, ValueError):
            _hid = 0
        if _hid:
            ruxsat_hafta = next((w for w in haftalar if w.id == _hid and w.status != "draft"
                                 and w.tahrir_ruxsat), None)
            if ruxsat_hafta is not None:
                _qayt_hafta.append(ruxsat_hafta.id)
        ws_ = we_ = None
        if not faqat_limit:
            try:
                ws_ = datetime.date.fromisoformat(str(payload.get("week_start") or ""))
                we_ = datetime.date.fromisoformat(str(payload.get("week_end") or ""))
            except ValueError:
                messages.error(request, "Hafta boshi va oxiri sanasini kiriting.")
                return _qayt()
            xato = _hafta_sana_xatosi(lim, ws_, we_, exclude_id=(ruxsat_hafta.id if ruxsat_hafta
                                                                else (draft.id if draft else None)))
            if xato:
                messages.error(request, xato)
                return _qayt()

        # ---- Umumiy limit DAVRI (boshlanish/tugash sanasi) — pul emas, zanjirsiz ----
        sana_ozgardi = False
        if (lim_edit_huquq or can_edit) and ("lim_start" in payload or "lim_end" in payload):
            def _sana(v):
                try:
                    return datetime.date.fromisoformat(str(v)) if v else None
                except ValueError:
                    return None
            ls_, le_ = _sana(payload.get("lim_start")), _sana(payload.get("lim_end"))
            if ls_ and le_ and le_ < ls_:
                messages.error(request, "Umumiy limit tugash sanasi boshlanishidan oldin bo'lmasin.")
                return _qayt()
            if ls_ != lim.start or le_ != lim.end:
                lim.start, lim.end = ls_, le_
                lim.save(update_fields=["start", "end"])
                sana_ozgardi = True
                messages.success(request, "Umumiy limit davri saqlandi.")

        # ---- Умумий ustunini SHU jadvaldan tahrirlash + yangi blok/qator ----
        # Admin — to'g'ridan-to'g'ri qo'llanadi; PTO — snab→PTO→dir→admin
        # zanjiriga LimitChangeRequest bo'lib ketadi (eski forma bilan bir xil).
        # limit_edits: [{id, name, unit, kind, note, bolim, masul, vol, price}] — mavjud
        # qator TO'LIQ tahrirlanadi (yangi qatorlar bilan bir xil); limit_del: [id, ...]
        lim_edits = [e for e in (payload.get("limit_edits") or []) if isinstance(e, dict)]
        lim_new = [e for e in (payload.get("limit_new") or []) if isinstance(e, dict)]
        lim_del = set()
        for x in payload.get("limit_del") or []:
            try:
                lim_del.add(int(x))
            except (TypeError, ValueError):
                pass
        # QO'SHISH rejimi: qulf, lekin yangi qatorlar — zanjir orqali (tahrir/o'chirish yo'q)
        qosh_rejim = (not lim_edit_huquq) and lim_qosh_huquq and bool(lim_new)
        if qosh_rejim:
            if lim_edits or lim_del:
                messages.warning(request, "Mavjud qatorlar qulf — ularning o'zgarishi e'tiborga olinmadi, "
                                          "faqat YANGI qatorlar zanjirga yuborildi.")
            lim_edits, lim_del = [], set()
        lim_ozgarish = bool(lim_edits or lim_new or lim_del)
        lim_ozgardi = sana_ozgardi
        if lim_ozgarish and (lim_edit_huquq or qosh_rejim):
            if lim.requests.filter(status__in=LIM_JARAYON).exists():
                messages.error(request, "Limit o'zgartirish so'rovi zanjirda turibdi — "
                                        "avval u yakunlansin, keyin Умумийni tahrirlaysiz.")
                return _qayt()
        if lim_ozgarish and not (lim_edit_huquq or qosh_rejim):
            messages.error(request, "Умумий limit tasdiqlangan (qulf) — tahrirlash uchun "
                                    "admin «Таҳрирга рухсат бериш»ni bosishi kerak.")
        # Admin ham «Занжирга юбориш»ни tanlasa — to'g'ridan emas, so'rov bo'lib ketadi
        admin_zanjir = bool(payload.get("lim_zanjir")) and is_admin(request.user)
        # Eski usulda (faqat summa) TASDIQLANGAN limit ham qulf qoidasiga bo'ysunadi:
        # admin ruxsat bermagan bo'lsa, uning tahriri ham to'g'ridan emas — zanjir orqali
        if is_admin(request.user) and limit_bor and not amaldagi_bor and not lim.tahrir_ruxsat:
            admin_zanjir = True
        if qosh_rejim:
            admin_zanjir = True   # qulfdagi limitga qo'shish — admin ham zanjir orqali
        edits, yangilar = {}, []
        qayta_nom = {}   # eski nom|bo'lim -> yangi {name, bolim, unit, kind} (admin to'g'ridan)
        if lim_ozgarish and (lim_edit_huquq or qosh_rejim):
            # Tozalash — xato bo'lsa hech narsa yozilmaydi
            for e in lim_edits:
                try:
                    eid = int(e.get("id"))
                except (TypeError, ValueError):
                    continue
                it, xato = _lj_qator(e)
                if xato:
                    messages.error(request, xato)
                    return _qayt()
                if it is not None:
                    edits[eid] = it
            for e in lim_new:
                it, xato = _lj_qator(e)
                if xato:
                    messages.error(request, xato)
                    return _qayt()
                if it is not None:
                    yangilar.append(it)
        lim_qoralama = bool(payload.get("lim_qoralama")) and not payload.get("lim_zanjir")
        if lim_ozgarish and (lim_edit_huquq or qosh_rejim) and lim_qoralama:
            # Yashil «Сақлаш»: Умумий o'zgarishlari (joriy tarkib + tahrir + yangi) ZANJIRGA
            # EMAS — QORALAMA bo'lib saqlanadi, keyin davom ettiriladi / yuboriladi
            t_items, t_sums = [], {k: Decimal("0") for k in KINDS}
            for li in lim.items.all().order_by("id"):
                if li.id in lim_del:
                    continue
                it = _lj_qator_dict(li)
                if li.id in edits:
                    edits[li.id]["asl_id"] = li.id
                    it = edits[li.id]
                t_items.append(it)
                t_sums[it["kind"]] += (it["quantity"] * it["unit_price"]).quantize(Decimal("0.01"))
            for it in yangilar:
                it["asl_id"] = None
                t_items.append(it)
                t_sums[it["kind"]] += (it["quantity"] * it["unit_price"]).quantize(Decimal("0.01"))
            if not t_items:
                messages.error(request, "Qoralama uchun kamida bitta qator kiriting.")
                return _qayt()
            _lj_qoralama_yoz(request, lim, t_items, t_sums)
            lim_ozgardi = True
            if payload.get("items"):
                # haftalik ham saqlanmoqda — o'sha ko'rinishda qolsin, lekin Умумий qayerdaligini aytamiz
                _qs = "?korinish=amaldagi"
                messages.info(request, "Умумий устундаги ўзгаришлар ҚОРАЛАМАга сақланди (жадвалда кўринмайди) — "
                                       "кўриш/давом эттириш учун тепадаги «📝 Қораламани очиш →» тугмасини босинг.")
        elif lim_ozgarish and (lim_edit_huquq or qosh_rejim) and (not is_admin(request.user) or admin_zanjir):
            # PTO/snab/direktor (yoki admin «Занжирга»): joriy tarkib + o'zgarishlar =
            # TAKLIF -> tasdiqlash zanjiri
            t_items, t_sums = [], {k: Decimal("0") for k in KINDS}
            ozgardi_soni = 0
            for li in lim.items.all().order_by("id"):
                if li.id in lim_del:
                    ozgardi_soni += 1
                    continue
                it = _lj_qator_dict(li)
                if li.id in edits:
                    edits[li.id]["asl_id"] = li.id
                    if edits[li.id] != it:
                        it = edits[li.id]
                        ozgardi_soni += 1
                t_items.append(it)
                t_sums[it["kind"]] += (it["quantity"] * it["unit_price"]).quantize(Decimal("0.01"))
            for it in yangilar:
                it["asl_id"] = None
                t_items.append(it)
                t_sums[it["kind"]] += (it["quantity"] * it["unit_price"]).quantize(Decimal("0.01"))
                ozgardi_soni += 1
            if not t_items:
                messages.error(request, "Kamida bitta limit qatori bo'lishi kerak.")
            elif not ozgardi_soni:
                messages.info(request, "Умумий limitda o'zgarish yo'q — so'rov yuborilmadi.")
            else:
                _st = _limit_boshlangich(p)
                with transaction.atomic():
                    req0 = LimitChangeRequest.objects.create(
                        project=p, limit=lim,
                        old_material=p.limit_material, old_labor=p.limit_labor,
                        old_machinery=p.limit_machinery, old_other=p.limit_other,
                        new_material=t_sums["material"], new_labor=t_sums["labor"],
                        new_machinery=t_sums["machinery"], new_other=t_sums["other"],
                        reason="Limit jadvalidan tahrir",
                        requested_by=request.user, status=_st,
                    )
                    LimitChangeItem.objects.bulk_create(
                        [LimitChangeItem(request=req0, **it) for it in t_items])
                    if lim.tahrir_ruxsat:
                        # Admin ruxsati BIR MARTALIK — so'rov yuborilgach yopiladi
                        lim.tahrir_ruxsat = False
                        lim.save(update_fields=["tahrir_ruxsat"])
                messages.success(request, _limit_yubor_xabar(_st))
        elif lim_ozgarish and is_admin(request.user) and lim_edit_huquq:
            # Admin — to'g'ridan-to'g'ri qo'llanadi
            qoladi = lim.items.exclude(id__in=lim_del).count() + len(yangilar)
            if qoladi == 0:
                messages.error(request, "Kamida bitta limit qatori qolishi kerak — o'chirilmadi.")
                return _qayt()
            with transaction.atomic():
                for li in lim.items.all().order_by("id"):
                    if li.id in lim_del:
                        li.delete()
                        lim_ozgardi = True
                        continue
                    it = edits.get(li.id)
                    if it is not None:
                        it["asl_id"] = li.id
                    if it is not None and it != _lj_qator_dict(li):
                        eski = {"name": li.name, "bolim": li.bolim or "", "unit": li.unit or "", "kind": li.kind}
                        for f, v in it.items():
                            if f != "asl_id":
                                setattr(li, f, v)
                        li.save()
                        # Qayta nomlangan qatorga bog'liq haftalik qatorlar ham ergashadi;
                        # shu POSTdagi (eski nomli) haftalik qatorlar ham yangi nomga o'tadi
                        yangi_nom = {k: it[k] for k in ("name", "bolim", "unit", "kind")}
                        hafta_qatorlarini_qayta_nomla(lim, eski, yangi_nom)
                        qayta_nom[_lj_key(eski["name"], eski["bolim"])] = yangi_nom
                        lim_ozgardi = True
                if yangilar:
                    LimitItem.objects.bulk_create([LimitItem(project=p, limit=lim, **{k: v for k, v in it.items() if k != "asl_id"})
                                                   for it in yangilar])
                    lim_ozgardi = True
                if lim_ozgardi:
                    p.recompute_limits()
                    if lim.tahrir_ruxsat:
                        lim.tahrir_ruxsat = False   # ruxsat bir martalik
                        lim.save(update_fields=["tahrir_ruxsat"])
            if lim_ozgardi:
                messages.success(request, "Умумий limit yangilandi.")

        # Qoldiq (tasdiqlanganlar bo'yicha) — server tomonda qayta tekshiramiz
        qoldiq = {}
        for li in lim.items.all():
            d = qoldiq.setdefault(_lj_key(li.name, li.bolim),
                                  {"qty": Decimal("0"), "unit": li.unit, "nom": li.name})
            d["qty"] += li.quantity - (li.boshlangich_qty or 0)
        if taklif_view and taklif_obj is not None:
            # Hali tasdiqlanmagan taklif qatorlari — chegara sifatida ularning hajmi
            for it in taklif_obj.proposed_items.all():
                k_ = _lj_key(it.name, it.bolim)
                if k_ not in qoldiq:
                    qoldiq[k_] = {"qty": it.quantity - (it.boshlangich_qty or 0),
                                  "unit": it.unit, "nom": it.name}
        for w in haftalar:
            if w.status == "rejected" or (draft is not None and w.id == draft.id) or w.tahrir_ruxsat:
                continue
            for it in w.items.all():
                k = _lj_key(it.name, it.bolim)
                if k in qoldiq:
                    qoldiq[k]["qty"] -= it.quantity

        valid_kind = set(KINDS)
        yangi_items, sorov = [], {}
        for row in payload.get("items") or []:
            if not isinstance(row, dict):
                continue
            nm = " ".join(str(row.get("name") or "").split())[:300]
            if not nm:
                continue
            yn = qayta_nom.get(_lj_key(nm, " ".join(str(row.get("bolim") or "").split())))
            if yn:
                row = dict(row, name=yn["name"], bolim=yn["bolim"], unit=yn["unit"], kind=yn["kind"])
                nm = yn["name"]
            q = _to_dec(str(row.get("qty") or "0")) or Decimal("0")
            pr = _to_dec(str(row.get("price") or "0")) or Decimal("0")
            if q <= 0:
                continue
            if pr <= 0:
                messages.error(request, f"«{nm}»: narx kiritilmagan — saqlanmadi.")
                return _qayt()
            bolim = " ".join(str(row.get("bolim") or "").split())[:200]
            kind = str(row.get("kind") or "material")
            if kind not in valid_kind:
                kind = "material"
            yangi_items.append(WeeklyRequestItem(
                kind=kind, name=nm,
                unit=" ".join(str(row.get("unit") or "").split())[:50],
                quantity=q, unit_price=pr,
                note=" ".join(str(row.get("izoh") or "").split())[:500],
                bolim=bolim))
            k = _lj_key(nm, bolim)
            sorov[k] = sorov.get(k, Decimal("0")) + q
        # Qolgandan oshmasin (shu sahifaning qat'iy nazorati)
        for k, jq in sorov.items():
            d = qoldiq.get(k)
            if d is not None and jq > d["qty"] + Decimal("0.001"):
                messages.error(request,
                    f"«{d['nom']}»: so'ralgan {_qty(jq)} {d['unit']} — qolgan "
                    f"{_qty(d['qty'])} {d['unit']} dan ORTIQ. Saqlanmadi.")
                return _qayt()
        if not yangi_items:
            if (lim_ozgardi or lim_ozgarish) and payload.get("action") != "submit":
                # Faqat Умумий tahrirlandi (natija xabari yuqorida) — haftalikka tegilmaydi
                return _qayt()
            messages.error(request, "Kamida bitta qatorga miqdor kiriting.")
            return _qayt()

        if ruxsat_hafta is not None:
            # YOPIQ haftani (admin ruxsati bilan) tahrirlash: qatorlar O'RNIDA yangilanadi —
            # to'lov yozilgan qator o'chirilmaydi/kamaytirilmaydi; status o'zgarmaydi
            xato = _hafta_qatorlarini_yangila(ruxsat_hafta, yangi_items, ws_, we_, request.user)
            if xato:
                messages.error(request, xato)
            else:
                messages.success(request, f"{ruxsat_hafta.week_start:%d.%m}–{ruxsat_hafta.week_end:%d.%m} "
                                          f"haftasi tahrirlandi ({len(yangi_items)} qator). Ruxsat yopildi.")
            return _qayt()
        with transaction.atomic():
            if draft is None:
                draft = WeeklyRequest.objects.create(
                    project=p, limit=lim, week_start=ws_, week_end=we_,
                    status="draft", created_by=request.user)
            else:
                draft.week_start, draft.week_end = ws_, we_
                draft.save(update_fields=["week_start", "week_end"])
                draft.items.all().delete()   # qoralama — unga to'lov yozilmaydi
            for it in yangi_items:
                it.request = draft
            WeeklyRequestItem.objects.bulk_create(yangi_items)
            if payload.get("action") == "submit":
                draft.status = _wk_keyingi(p, "draft")
                draft.save(update_fields=["status"])
        if payload.get("action") == "submit":
            _xb = _wk_xabar(request, draft, draft.status,
                            f"📋 {p.code} — {draft.week_start:%d.%m}–{draft.week_end:%d.%m} haftalik so'rovi "
                            f"sizga keldi ({WK_BOSQICH_NOM.get(draft.status, draft.status)} bosqichi). Yubordi: {request.user.username}.")
            messages.success(request, f"Hafta yopildi — so'rov {WK_BOSQICH_NOM.get(draft.status, draft.status)}ga yuborildi." + _xb)
        else:
            messages.success(request, f"Qoralama saqlandi ({len(yangi_items)} qator).")
        return _qayt()

    # ---------- GET ----------
    # Fakt nom+bo'lim kesimida: yozilgan haftalik = olingan (rad etilgandan tashqari
    # hammasi); hozirgi QORALAMA hafta jadvalda jonli qo'shiladi (serverda emas)
    fakt = {}
    for w in haftalar:
        if w.status == "rejected" or (draft is not None and w.id == draft.id) or w.tahrir_ruxsat:
            continue
        for it in w.items.all():
            k = _lj_key(it.name, it.bolim)
            d = fakt.setdefault(k, {"qty": Decimal("0"), "sum": Decimal("0")})
            d["qty"] += it.quantity
            d["sum"] += it.total

    # Limit qatorlari bo'lim guruhlari bilan (fakt taqsimoti — _lj_guruhlar ichida).
    # TAKLIF ko'rinishida amaldagi limit o'rniga ZANJIRDAGI taklif qatorlari chiqadi.
    taklif_json, taklif_ochirilgan = None, []
    taklif_keys = set()
    if taklif_view:
        guruhlar = _lj_guruhlar(taklif_obj.proposed_items.all().order_by("id"), fakt)
        if amaldagi_bor:
            # Amaldagi limitdan farqi: yangi / o'zgargan / o'chirilayotgan qatorlar
            joriy = {}
            for li in lim.items.all().order_by("id"):
                joriy.setdefault(_lj_key(li.name, li.bolim),
                                 (li.name, float(li.quantity), float(li.unit_price)))
            t_keys = set()
            for g in guruhlar:
                for r in g["rows"]:
                    t_keys.add(r["key"])
                    j = joriy.get(r["key"])
                    if j is None:
                        r["ozg"] = "yangi"
                    elif (j[1], j[2]) != (r["vol"], r["price"]):
                        r["ozg"] = "ozgardi"
            taklif_ochirilgan = [j[0] for k0, j in joriy.items() if k0 not in t_keys]
        if taklif_huquq:
            # Bosqich egasi — qatorlar JS tomonda to'liq tahrirlanadigan bloklar bo'ladi
            taklif_json = [{
                "bolim": g["bolim"], "masul": g["masul"],
                "rows": [{"name": r["name"], "unit": r["unit"], "kind": r["kind"],
                          "vol": r["vol"], "price": r["price"], "izoh": r["izoh"],
                          "asl": r.get("asl"), "bq": r["bq"], "bs": r["bs"], "sana": r.get("sana", ""),
                          "fq": r["wq"], "fs": r["ws"]} for r in g["rows"]],
            } for g in guruhlar]
            # Haftalik qatorlar taklif qatorlariga KALIT bo'yicha bog'lanadi (aks holda ular
            # «Лимитда йўқ ишлар»ga ham tushib, jadvalda ikki marta chiqib/saqlanib qolardi)
            taklif_keys = {r["key"] for g in guruhlar for r in g["rows"]}
            guruhlar = []
    else:
        guruhlar = _lj_guruhlar(lim.items.all().order_by("id"), fakt)

    limit_keys = {r["key"] for g in guruhlar for r in g["rows"]} | taklif_keys

    # Hafta tablari ma'lumoti (JSONga)
    hafta_data = []
    for i, w in enumerate(haftalar, start=1):
        items_map, boshqa = {}, []
        for it in w.items.all():
            k = _lj_key(it.name, it.bolim)
            row = {"qty": float(it.quantity), "price": float(it.unit_price),
                   "sum": float(it.total), "izoh": it.note or "",
                   "name": it.name, "bolim": it.bolim or "",
                   "unit": it.unit or "", "kind": it.kind}
            if k in limit_keys:
                items_map.setdefault(k, []).append(row)
            else:
                boshqa.append(row)
        from django.utils.timezone import localtime as _lt0
        hafta_data.append({
            "id": w.id, "raqam": i,
            "sana": f"{w.week_start:%d.%m}-{w.week_end:%d.%m.%Y}",
            "ws": w.week_start.isoformat(), "we": w.week_end.isoformat(),
            "status": w.status, "locked": w.status != "draft" and not w.tahrir_ruxsat,
            "ruxsat": bool(w.tahrir_ruxsat),
            "number": w.number or "", "items": items_map, "boshqa": boshqa,
            # Zanjir holati banneri uchun imzolar
            "kiritdi": w.created_by.username if w.created_by_id else "",
            "kiritdi_at": f"{_lt0(w.created_at):%d.%m %H:%M}" if w.created_at else "",
            "snab_by": w.snab_by.username if w.snab_by_id else "",
            "pto2_by": w.pto2_by.username if w.pto2_by_id else "",
            "dir_by": w.director_by.username if w.director_by_id else "",
            "dir_at": f"{_lt0(w.director_at):%d.%m %H:%M}" if w.director_at else "",
            "prov_by": w.prov_by.username if w.prov_by_id else "",
            "adm_by": w.approved_by.username if w.approved_by_id else "",
            "adm_at": f"{_lt0(w.approved_at):%d.%m %H:%M}" if w.approved_at else "",
            "qaytarish": (w.reject_note or "") if (w.reject_note or "").startswith("↩") and w.status != "approved" else "",
        })

    # Yangi hafta uchun taklif sanalar
    if haftalar:
        oxirgi = max(w.week_end for w in haftalar)
        t_ws = oxirgi + datetime.timedelta(days=1)
    else:
        t_ws = datetime.date.today()
    t_we = t_ws + datetime.timedelta(days=6)

    # UMUMIY LIMIT zanjir holati — sahifa tepasida doim ko'rinadi
    from django.utils.timezone import localtime as _lt1

    def _v(dt):
        return f"{_lt1(dt):%d.%m %H:%M}" if dt else ""

    lim_holat = None
    if lim_pend_obj:
        r0 = lim_pend_obj
        # Bosqichlar TARTIB bo'yicha: joriy bosqichdan KEYINGILARI — hali kutilmoqda
        # (orqaga qaytarilgan bo'lsa, ilgari imzolangan bo'lsa ham kulrang)
        TARTIB = ["snab", "pto2", "dir", "prov", "adm"]
        joriy_i = TARTIB.index(r0.status) if r0.status in TARTIB else -1
        steps = [{"nom": "ПТО киритди", "holat": "ok",
                  "kim": r0.requested_by.username if r0.requested_by_id else "",
                  "vaqt": _v(r0.created_at)}]
        BOSQ = [("snab", "Снабжение нархлади", "СНАБЖЕНИЕ нархлашида", r0.snab_by, r0.snab_at),
                ("pto2", "ПТО хулосаси", "ПТО хулосасида", r0.pto2_by, r0.pto2_at),
                ("dir", "Директор", "ДИРЕКТОР тасдиғида", r0.director_by, r0.director_at),
                ("prov", "Проверчик", "ПРОВЕРЧИК текширувида", r0.prov_by, r0.prov_at),
                ("adm", "Админ", "АДМИН тасдиғида", None, None)]
        prov_bor = _prov_bor(p.firma) or bool(r0.prov_by_id)
        for st_, ok_nom, kut_nom, kim, vaqt in BOSQ:
            i_ = TARTIB.index(st_)
            if i_ < joriy_i and (kim is not None or st_ == "pto2"):
                steps.append({"nom": ok_nom, "holat": "ok",
                              "kim": kim.username if kim else "", "vaqt": _v(vaqt)})
            elif i_ == joriy_i:
                steps.append({"nom": kut_nom, "holat": "joriy", "kim": "", "vaqt": ""})
            elif i_ > joriy_i and (st_ != "snab" or _snab_bor(p)) and (st_ != "prov" or prov_bor):
                steps.append({"nom": ok_nom, "holat": "keyin", "kim": "", "vaqt": ""})
        lim_holat = {"mode": "pending", "steps": steps,
                     # orqaga qaytarilgan bo'lsa — sababi bannerda
                     "qaytarish": (r0.decision_note or "")
                     if (r0.decision_note or "").startswith("↩") else ""}
        from .roles import is_snab as _is_snab0, is_prov as _is_prov_u, is_asosiy_prov as _asos_prov_u
        # Shu bosqich egasi jadvalning O'ZIDA oldinga o'tkazadi yoki ORQAGA qaytaradi
        _u, _st = request.user, r0.status
        _dir_actor = (is_director(_u) and not _u.is_superuser) or _asosiy_adm(_u)
        _amal = None
        if _st == "snab" and (_is_snab0(_u) or is_admin(_u)):
            _amal = {"oldinga": ("snab_return", "✓ Нархланди — ПТОга юбориш"), "orqaga": []}
        elif _st == "pto2" and (is_pto(_u) or is_admin(_u)):
            _amal = {"oldinga": ("pto_send_dir", "▶ Директорга юбориш"),
                     "orqaga": [("snab", "Снабжениега")] if r0.snab_by_id else []}
        elif _st == "dir" and _dir_actor:
            _amal = {"oldinga": ("dir_approve", "✓ Тасдиқлаш (директор)" + (" — проверчикга" if _prov_bor(p.firma) else "")),
                     "orqaga": [("pto2", "ПТОга")]}
        elif _st == "prov" and (_asos_prov_u(_u) or is_admin(_u)):
            _amal = {"oldinga": ("prov_approve", "✓ Текширилди — админга"),
                     "orqaga": [("dir", "Директорга")]}
        elif _st == "adm" and is_admin(_u):
            _amal = {"oldinga": ("approve", "✓ Тасдиқлаш (админ)"),
                     "orqaga": [("pto2", "ПТОга"), ("dir", "Директорга")]}
        if _amal and is_admin(_u):
            # Admin — snab / PTO / direktor / proverchikning istalganiga qaytaradi (joriy bosqichdan tashqari)
            _amal["orqaga"] = [(k, n) for k, n in (("pto2", "ПТОга"), ("snab", "Снабжениега"), ("dir", "Директорга"), ("prov", "Проверчикга"))
                               if k != _st and (k != "prov" or prov_bor)]
        if _amal:
            _amal["id"] = r0.id
        lim_holat["amal"] = _amal
    else:
        oxt = (lim.requests.filter(status="approved")
               .order_by("-decided_at", "-id").first())
        if oxt:
            lim_holat = {"mode": "ok",
                         "kim": oxt.decided_by.username if oxt.decided_by_id else "admin",
                         "vaqt": _v(oxt.decided_at)}
        elif limit_bor:
            lim_holat = {"mode": "ok", "kim": "тўғридан киритилган", "vaqt": ""}
    # Eski usul: faqat SUMMA kiritilgan (qatorsiz) limit — jadvalda ogohlantirish chiqadi
    eski_sum = None
    if limit_bor and not amaldagi_bor:
        eski_sum = [(nom, _money(v)) for nom, v in (
            ("Материал", p.limit_material), ("Иш ҳақи", p.limit_labor),
            ("Машина части", p.limit_machinery), ("Кўзда тутилмаган", p.limit_other),
        ) if v]
    # Taklif qatorsiz (eski usuldagi, faqat summali) so'rov bo'lsa — summasi ko'rsatiladi
    taklif_sum = (_money(lim_pend_obj.new_total)
                  if lim_pend_obj and not taklif_bor else "")
    qoralama = None
    if qoralama_obj:
        qoralama = {"kim": qoralama_obj.requested_by.username if qoralama_obj.requested_by_id else "",
                    "vaqt": _v(qoralama_obj.edited_at or qoralama_obj.created_at),
                    "soni": qoralama_obj.proposed_items.count(),
                    "huquq": taklif_huquq, "apply": is_admin(request.user) and taklif_huquq}
    # ---- Sahifa tepasidagi KARTALAR: zanjir qadamlari, keyingi qabul qiluvchi, holat ----
    def _ism(u):
        return u.username if u else ""

    def _rol_user(rol, firma_boyicha=False):
        from django.contrib.auth import get_user_model
        qs_ = get_user_model().objects.filter(is_active=True, profile__role=rol)
        qs_ = qs_.filter(profile__firma=p.firma) if firma_boyicha else qs_.filter(profile__projects=p)
        return qs_.order_by("username").first()

    _nomlar = [("pto", "ПТО"), ("snab", "Снабжения"), ("pto2", "ПТО хулосаси"),
               ("dir", "Директор"), ("prov", "Проверчик"), ("adm", "Админ")]
    # Zanjir DOIM to'liq ko'rsatiladi (foydalanuvchi talabi): ПТО → Снабжения → ПТО хулосаси →
    # Директор → Проверчик → Админ; xodimi yo'q bosqich o'tkazib yuborilsa ham qadam ko'rinadi
    _tartib = [k for k, _ in _nomlar]
    lj_qadamlar, lj_pill, lj_keyingi = [], None, ""
    _oxt = (lim.requests.filter(status="approved").order_by("-decided_at", "-id").first())
    if lim_pend_obj:
        r0_ = lim_pend_obj
        meta = {"pto": (_ism(r0_.requested_by), _v(r0_.created_at)),
                "snab": (_ism(r0_.snab_by), _v(r0_.snab_at)),
                "pto2": (_ism(r0_.pto2_by), _v(r0_.pto2_at)),
                "dir": (_ism(r0_.director_by), _v(r0_.director_at)),
                "prov": (_ism(r0_.prov_by), _v(r0_.prov_at)), "adm": ("", "")}
        ji = _tartib.index(r0_.status) if r0_.status in _tartib else len(_tartib)
        for i_, (k, n) in enumerate(_nomlar):
            holat = "ok" if i_ < ji else ("joriy" if i_ == ji else "keyin")
            kim, vaqt = meta.get(k, ("", ""))
            lj_qadamlar.append({"nom": n, "holat": holat, "kim": kim if holat == "ok" else "",
                                "vaqt": vaqt if holat == "ok" else ""})
        _jnom = dict(_nomlar).get(r0_.status, r0_.get_status_display())
        lj_pill = {"tur": "chain", "matn": "Занжирда · " + _jnom}
        if ji + 1 < len(_tartib):
            k2 = _tartib[ji + 1]
            u2 = (_rol_user("snab") if k2 == "snab" else r0_.requested_by if k2 == "pto2"
                  else _rol_user("director", True) if k2 == "dir" else None)
            if k2 == "prov":
                _pn = ", ".join(u.username for u in _provlar())
                lj_keyingi = "Проверчик" + (" · " + _pn if _pn else "")
            else:
                lj_keyingi = dict(_nomlar)[k2] + (" · " + _ism(u2) if u2 else "")
    else:
        for k, n in _nomlar:
            lj_qadamlar.append({"nom": n, "holat": "keyin", "kim": "", "vaqt": ""})
        if qoralama_obj:
            lj_qadamlar[0]["holat"] = "joriy"
            meniki = qoralama_obj.requested_by_id == request.user.id
            lj_pill = {"tur": "draft", "matn": "Қоралама сизда" if meniki else "Қоралама · " + _ism(qoralama_obj.requested_by)}
        elif _oxt or limit_bor:
            for q_ in lj_qadamlar:
                q_["holat"] = "ok"
            if _oxt:
                lj_qadamlar[0]["kim"], lj_qadamlar[0]["vaqt"] = _ism(_oxt.requested_by), _v(_oxt.created_at)
                lj_qadamlar[-1]["kim"], lj_qadamlar[-1]["vaqt"] = (_ism(_oxt.decided_by) or "admin"), _v(_oxt.decided_at)
            lj_pill = {"tur": "ok", "matn": "Тасдиқланган"}
        else:
            lj_qadamlar[0]["holat"] = "joriy"
            lj_pill = {"tur": "none", "matn": "Лимит киритилмаган"}
        k1 = _limit_boshlangich(p)
        u1 = _rol_user("snab") if k1 == "snab" else _rol_user("director", True)
        lj_keyingi = ("Снабжения" if k1 == "snab" else "Директор") + (" · " + _ism(u1) if u1 else "")
    _dir_u = _rol_user("director", True)
    from django.conf import settings as _st_
    from django.contrib.auth import get_user_model as _gum
    _adm_u = (_gum().objects.filter(is_active=True, is_superuser=True,
                                    username=getattr(_st_, "ASOSIY_ADMIN_LOGIN", "admin")).first())
    from .roles import is_asosiy_prov as _is_prov_w, is_snab as _is_snab_w
    _wk1 = _wk_keyingi(p, "draft")
    _wk1_u = (_rol_user("snab") if _wk1 == "snab" else _dir_u if _wk1 == "dir" else _adm_u)
    # Obyektning RAQAMLI limitlari — tablar (jami summa bilan); keyingisini faqat admin ochadi
    limitlar = [{"raqam": l_.raqam, "nom": l_.nom, "joriy": l_.pk == lim.pk,
                 "jami_q": _money(l_.jami())} for l_ in p.limitlar.order_by("raqam")]
    return render(request, "projects/limit_jadval.html", {
        "lim": lim, "limitlar": limitlar, "is_asosiy_adm": _asosiy_adm(request.user),
        "lj_qadamlar": lj_qadamlar, "lj_pill": lj_pill, "lj_keyingi": lj_keyingi,
        "wk_dir_nom": _ism(_dir_u), "wk_adm_nom": _ism(_adm_u),
        "wk_keyingi": WK_BOSQICH_NOM.get(_wk1, _wk1) + (" · " + _ism(_wk1_u) if _wk1_u else ""),
        "wk_snab_actor": _is_snab_w(request.user) and not request.user.is_superuser,
        "wk_prov_actor": _is_prov_w(request.user) and not request.user.is_superuser,
        "p": p, "guruhlar": guruhlar,
        # Taklif ko'rinishida haftalik va Умумий (amaldagi) tahriri o'chiq
        "can_edit": can_edit,
        "lim_edit_huquq": lim_edit_huquq and not taklif_view,
        "lim_qosh_huquq": lim_qosh_huquq and not taklif_view and not lim_pend_obj,
        # Qoralama tahririda qulf: asl qatorlar faqat o'qiladi
        "taklif_qulf": bool(taklif_view and qoralama_obj and _lj_qulf(lim)),
        "taklif_view": taklif_view, "taklif_edit": taklif_view and taklif_huquq,
        "taklif_json": taklif_json, "taklif_ochirilgan": taklif_ochirilgan,
        "taklif_token": _lj_taklif_token(taklif_obj) if taklif_obj else "",
        "qoralama": qoralama,
        # Admin tahriri to'g'ridan qo'llanadimi (eski summali limitda — faqat ruxsat bilan)
        "adm_togri": is_admin(request.user) and not (
            limit_bor and not amaldagi_bor and not lim.tahrir_ruxsat),
        "taklif_sum": taklif_sum,
        "amaldagi_bor": amaldagi_bor, "eski_sum": eski_sum,
        "lim_ruxsat": lim.tahrir_ruxsat,
        "lim_start": lim.start.isoformat() if lim.start else "",
        "lim_end": lim.end.isoformat() if lim.end else "",
        "lim_qulf": ((zanjir_ishtirokchi or is_admin(request.user))
                     and amaldagi_bor and not lim.tahrir_ruxsat),
        "wk_dir_actor": ((is_director(request.user) and not request.user.is_superuser)
                         or _asosiy_adm(request.user)),
        "lim_holat": lim_holat,
        "is_adm": is_admin(request.user),
        "lim_pending": bool(lim_pend_obj),
        "hafta_json": hafta_data,
        "draft_id": draft.id if draft else None,
        "taklif_ws": t_ws.isoformat(), "taklif_we": t_we.isoformat(),
    })


def _lj_xabar_yubor(matn, oluvchi, yubordi):
    """Zanjir bildirishnomasi — FAQAT xodimning O'Z Telegram botiga (tizim banneri YO'Q:
    foydalanuvchi qarori, 2026-10-21 — «бундай хабарнома келиши шарт эмас»; sahifa
    tepasidagi banner faqat adminning «Xabar yuborish» xabarlari uchun). TG ketdi-mi -> bool."""
    x = None
    tg = False
    try:
        from .auth2fa import tg_send_fon
        prof = getattr(oluvchi, "profile", None)
        chat = (getattr(prof, "telegram_chat_id", "") or "").strip() if prof else ""
        if chat:
            tg = bool(tg_send_fon(chat, "❗ " + matn +
                                  "\n\nTizimga kirib «O'qidim» tugmasini bosing."))
    except Exception:
        pass
    return x, tg


@login_required
def limit_navbat_eski(request, pk):
    """«Масъулга юбориш» (ketma-ket ko'rib chiqish navbati) OLIB TASHLANDI — u tasdiqlash
    zanjiri bilan chalkashtirilar edi. Eski Telegram havolalari jadvalga olib boradi."""
    p = get_object_or_404(Project, pk=pk)
    _firma_yoki_403(request, p)
    messages.info(request, "«Масъулга юбориш» навбати олиб ташланган — лимит фақат тасдиқлаш занжири орқали юради.")
    return redirect("limit_jadval", pk=pk)


@login_required
def limit_jadval_export(request, pk):
    """Limit jadvalini Excelga yuklab olish — sahifadagi ko'rinishda:
    УМУМИЙ | БАЖАРИЛГАН | ҚОЛГАН | (tanlangan ҲАФТА), JONLI formulalar bilan:
    Сумма=Объём*Нархи, Қолган=Умумий-Бажарилган, Блок якуни/ЖАМИ=SUM."""
    p = get_object_or_404(Project, pk=pk)
    _firma_yoki_403(request, p)
    lim = _lj_joriy(p, request)

    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as _L
    from openpyxl.formatting.rule import FormulaRule
    from django.utils.timezone import localtime as _lt

    # --- BARCHA haftalar (saytdagidek); tanlangani ochiq, qolganlari YASHIRIN ---
    haftalar = list(WeeklyRequest.objects.filter(limit=lim)
                    .order_by("week_start", "id"))
    hid = request.GET.get("hafta") or ""
    vis_id = int(hid) if hid.isdigit() else None
    if haftalar:
        if vis_id not in {w.pk for w in haftalar}:
            vis_id = haftalar[-1].pk    # tanlanmagan bo'lsa — eng oxirgi hafta ochiq
    HOLAT = {"draft": "қоралама", "snab": "снабженияда", "pto2": "ПТО хулосасида", "dir": "директорда",
             "prov": "проверчикда", "submitted": "админда", "approved": "тасдиқланган"}
    # Ustun tartibi: ҚОЛГАН ... [eski haftalar — YASHIRIN] ... [tanlangan hafta]
    raqam = {w.pk: i + 1 for i, w in enumerate(haftalar)}   # xronologik raqam
    haftalar = ([w for w in haftalar if w.pk != vis_id]
                + [w for w in haftalar if w.pk == vis_id])
    hmaps = []
    for w in haftalar:
        m = {}
        for wi in w.items.all():
            k = _lj_key(wi.name, wi.bolim)
            d = m.setdefault(k, [Decimal("0"), Decimal("0")])
            d[0] += wi.quantity
            d[1] += wi.total
        hmaps.append(m)
    hafta = next((w for w in haftalar if w.pk == vis_id), None)

    # --- fakt (tasdiqlangan haftalar) + dublikatlarga taqsimot (sahifadagidek) ---
    fakt = {}
    for wi in (WeeklyRequestItem.objects.filter(request__limit=lim)
               .exclude(request__status__in=["rejected", "draft"])):
        k = _lj_key(wi.name, wi.bolim)
        d = fakt.setdefault(k, {"qty": Decimal("0"), "sum": Decimal("0")})
        d["qty"] += wi.quantity
        d["sum"] += wi.total
    dubl = {}
    for li in lim.items.all():
        k0 = _lj_key(li.name, li.bolim)
        dubl[k0] = dubl.get(k0, 0) + 1
    rem = {k0: {"qty": v["qty"], "sum": v["sum"]} for k0, v in fakt.items()}
    korilgan = {}
    guruhlar = {}
    for li in lim.items.all().order_by("id"):
        kal = (li.bolim or "").strip()
        g = guruhlar.get(kal)
        if g is None:
            g = guruhlar[kal] = {"bolim": kal, "masul": (li.masul or "").strip(),
                                 "rows": []}
        if not g["masul"] and (li.masul or "").strip():
            g["masul"] = li.masul.strip()
        k = _lj_key(li.name, li.bolim)
        korilgan[k] = korilgan.get(k, 0) + 1
        oxirgi = korilgan[k] == dubl.get(k, 1)
        r0 = rem.get(k)
        f_qty = Decimal("0")
        f_sum = Decimal("0")
        if r0 is not None and (r0["qty"] > 0 or r0["sum"] > 0):
            if oxirgi:
                f_qty, f_sum = r0["qty"], r0["sum"]
            else:
                f_qty = min(li.quantity, r0["qty"])
                f_sum = (r0["sum"] * f_qty / r0["qty"]).quantize(Decimal("0.01")) \
                    if r0["qty"] else Decimal("0")
            r0["qty"] -= f_qty
            r0["sum"] -= f_sum
        g["rows"].append({"name": li.name, "izoh": li.note or "",
                          "unit": li.unit or "", "key": k,
                          "qty": li.quantity, "price": li.unit_price,
                          "f_qty": f_qty + (li.boshlangich_qty or 0),
                          "f_sum": f_sum + (li.boshlangich_sum or 0)})

    # --- Excel ---
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Лимит"
    AR = "Arial"
    thin = Side(style="thin", color="C8CCD0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    money = '#,##0'
    qtyf = '#,##0.###'
    right = Alignment(horizontal="right")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    F_MAIN = PatternFill("solid", fgColor="1A5FB4")
    F_MAIN2 = PatternFill("solid", fgColor="3584E4")
    F_DONE = PatternFill("solid", fgColor="26A269")
    F_DONE2 = PatternFill("solid", fgColor="33B579")
    F_LEFT = PatternFill("solid", fgColor="E66100")
    F_LEFT2 = PatternFill("solid", fgColor="F57900")
    F_HAF = PatternFill("solid", fgColor="813D9C")
    F_HAF2 = PatternFill("solid", fgColor="9450AB")
    F_BLOK = PatternFill("solid", fgColor="FDE8B0")
    F_SUB = PatternFill("solid", fgColor="E8F0FE")
    F_GRAND = PatternFill("solid", fgColor="D1E7DD")
    hdr_font = Font(name=AR, bold=True, color="FFFFFF", size=10)
    blok_font = Font(name=AR, bold=True, size=11)
    tot_font = Font(name=AR, bold=True, size=10)

    NCOL = 11 + 4 * len(haftalar)

    ws["A1"] = f"{p.code} — {p.name}"
    ws["A1"].font = Font(name=AR, bold=True, size=14)
    sub = "Умумий лимит ва ҳафталик лимитлар: УМУМИЙ · БАЖАРИЛГАН · ҚОЛГАН"
    if lim.start or lim.end:
        sub += (f" · Умумий лимит даври: "
                f"{lim.start:%d.%m.%Y}" if lim.start else " · Умумий лимит даври: ...")
        sub += f" — {lim.end:%d.%m.%Y}" if lim.end else " — ..."
    if haftalar:
        sub += (f" · {len(haftalar)} ҳафта (эскилари яширин устунларда,"
                " «+» билан очилади)")
    sub += f" · юкланди: {_lt().strftime('%d.%m.%Y %H:%M')}"
    ws["A2"] = sub
    ws["A2"].font = Font(name=AR, size=10, color="64748B")

    h1, h2 = 4, 5
    ws.cell(h1, 1, "№")
    ws.cell(h1, 2, "Иш тури")
    ws.cell(h1, 3, "Изоҳ")
    ws.cell(h1, 4, "Умумий")
    ws.cell(h1, 8, "Бажарилган")
    ws.cell(h1, 10, "Қолган")
    for wi_, w in enumerate(haftalar):
        c0 = 12 + 4 * wi_
        ws.cell(h1, c0, (f"{raqam[w.pk]}-ҳафта {w.week_start:%d.%m}-{w.week_end:%d.%m}"
                         f" · {HOLAT.get(w.status, w.status)}"))
        for j, t in enumerate(("Бирл.", "Объём", "Нархи", "Сумма")):
            ws.cell(h2, c0 + j, t)
        ws.merge_cells(start_row=h1, start_column=c0, end_row=h1, end_column=c0 + 3)
    for j, t in enumerate(("Бирл.", "Объём", "Нархи", "Сумма")):
        ws.cell(h2, 4 + j, t)
    ws.cell(h2, 8, "Объём"); ws.cell(h2, 9, "Сумма")
    ws.cell(h2, 10, "Объём"); ws.cell(h2, 11, "Сумма")
    for cc in ("A", "B", "C"):
        ws.merge_cells(f"{cc}{h1}:{cc}{h2}")
    ws.merge_cells(f"D{h1}:G{h1}")
    ws.merge_cells(f"H{h1}:I{h1}")
    ws.merge_cells(f"J{h1}:K{h1}")
    GF = {**{c: (F_MAIN, F_MAIN2) for c in range(1, 8)},
          **{c: (F_DONE, F_DONE2) for c in (8, 9)},
          **{c: (F_LEFT, F_LEFT2) for c in (10, 11)},
          **{c: (F_HAF, F_HAF2) for c in range(12, NCOL + 1)}}
    for c in range(1, NCOL + 1):
        f1, f2 = GF[c]
        x = ws.cell(h1, c); x.fill = f1; x.font = hdr_font
        x.border = border; x.alignment = center
        x = ws.cell(h2, c); x.fill = f2; x.font = hdr_font
        x.border = border; x.alignment = center

    r = h2
    yakun_qatorlar = []
    hafta_korildi = [set() for _ in haftalar]
    for g in guruhlar.values():
        r += 1
        nom = g["bolim"] or "Бўлимсиз қаторлар"
        if g["masul"]:
            nom += f"  ·  Масъул: {g['masul']}"
        ws.cell(r, 2, nom)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=NCOL)
        for c in range(1, NCOL + 1):
            ws.cell(r, c).fill = F_BLOK
            ws.cell(r, c).font = blok_font
            ws.cell(r, c).border = border
        boshi = r + 1
        for i, it in enumerate(g["rows"], start=1):
            r += 1
            ws.cell(r, 1, i)
            ws.cell(r, 2, it["name"])
            ws.cell(r, 3, it["izoh"])
            ws.cell(r, 4, it["unit"])
            ws.cell(r, 5, float(it["qty"]))
            ws.cell(r, 6, float(it["price"]))
            ws.cell(r, 7).value = f"=E{r}*F{r}"
            ws.cell(r, 8, float(it["f_qty"]))
            ws.cell(r, 9, float(it["f_sum"]))
            ws.cell(r, 10).value = f"=E{r}-H{r}"
            ws.cell(r, 11).value = f"=G{r}-I{r}"
            for wi_ in range(len(haftalar)):
                c0 = 12 + 4 * wi_
                ws.cell(r, c0).value = f"=D{r}"
                hd = (hmaps[wi_].get(it["key"])
                      if it["key"] not in hafta_korildi[wi_] else None)
                hafta_korildi[wi_].add(it["key"])
                if hd:
                    hq, hs = hd
                    ws.cell(r, c0 + 1, float(hq))
                    narx = (hs / hq) if hq else Decimal("0")
                    ws.cell(r, c0 + 2, float(narx))
                ws.cell(r, c0 + 3).value = f"={_L(c0+1)}{r}*{_L(c0+2)}{r}"
            for c in range(1, NCOL + 1):
                cell = ws.cell(r, c)
                cell.border = border
                cell.font = Font(name=AR, size=10)
                qism = (c - 12) % 4 if c >= 12 else None
                if c in (5, 8, 10) or qism == 1:
                    cell.number_format = qtyf; cell.alignment = right
                if c in (6, 7, 9, 11) or qism in (2, 3):
                    cell.number_format = money; cell.alignment = right
                if c in (1, 4) or qism == 0:
                    cell.alignment = Alignment(horizontal="center")
        # Blok yakuni
        r += 1
        ws.cell(r, 2, "Блок якуни:").font = tot_font
        ws.cell(r, 2).alignment = right
        yak_cols = [(7, "G"), (9, "I"), (11, "K")] + \
            [(15 + 4 * i, _L(15 + 4 * i)) for i in range(len(haftalar))]
        for c, harf in yak_cols:
            cell = ws.cell(r, c)
            cell.value = f"=SUM({harf}{boshi}:{harf}{r-1})"
            cell.number_format = money
            cell.font = tot_font
            cell.alignment = right
        for c in range(1, NCOL + 1):
            ws.cell(r, c).fill = F_SUB
            ws.cell(r, c).border = border
        yakun_qatorlar.append(r)

    # ЖАМИ
    r += 1
    ws.cell(r, 2, "ЖАМИ:").font = Font(name=AR, bold=True, size=12)
    ws.cell(r, 2).alignment = right
    jami_cols = [(7, "G"), (9, "I"), (11, "K")] + \
        [(15 + 4 * i, _L(15 + 4 * i)) for i in range(len(haftalar))]
    for c, harf in jami_cols:
        cell = ws.cell(r, c)
        cell.value = ("=" + "+".join(f"{harf}{j}" for j in yakun_qatorlar)) \
            if yakun_qatorlar else 0
        cell.number_format = money
        cell.font = Font(name=AR, bold=True, size=12, color="0B6E4F")
        cell.alignment = right
    for c in range(1, NCOL + 1):
        ws.cell(r, c).fill = F_GRAND
        ws.cell(r, c).border = border

    # Shartli formatlar: Qolgan minus -> qizil; Bajarilgan Umumiydan oshsa -> qizil
    if r > h2 + 1:
        d0, d1 = h2 + 1, r - 1
        ws.conditional_formatting.add(
            f"J{d0}:K{d1}",
            FormulaRule(formula=[f"AND(ISNUMBER(J{d0}),J{d0}<0)"],
                        font=Font(name=AR, color="CC0000", bold=True)))
        ws.conditional_formatting.add(
            f"H{d0}:H{d1}",
            FormulaRule(formula=[f"AND(ISNUMBER(H{d0}),ISNUMBER(E{d0}),H{d0}>E{d0})"],
                        font=Font(name=AR, color="CC0000", bold=True),
                        fill=PatternFill("solid", bgColor="FBE4E6")))

    kengliklar = [5, 34, 22, 8, 10, 11, 14, 10, 14, 10, 14] + \
        [8, 10, 11, 14] * len(haftalar)
    for i, w_ in enumerate(kengliklar, start=1):
        ws.column_dimensions[_L(i)].width = w_
    # Tanlangan haftadan boshqa haftalar YASHIRIN guruh (Excelda «+» bilan ochiladi)
    for wi_, w in enumerate(haftalar):
        if w.pk != vis_id:
            c0 = 12 + 4 * wi_
            ws.column_dimensions.group(_L(c0), _L(c0 + 3), hidden=True)
    ws.freeze_panes = f"D{h2+1}"

    import io as _io
    buf = _io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    # Fayl nomi: Лимит_<obyekt nomi>_<hafta sanasi>.xlsx (kirillcha nom RFC 5987)
    import re as _re
    from urllib.parse import quote as _q
    sana = ""
    ox = hafta or (WeeklyRequest.objects.filter(limit=lim)
                   .order_by("-week_start", "-id").first())
    if ox:
        sana = f"_{ox.week_start:%d.%m}-{ox.week_end:%d.%m.%Y}"
    toza = _re.sub(r'[\\/:*?"<>|]+', " ", p.name)
    toza = "_".join(toza.split())
    fn = f"Лимит_{toza}{sana}.xlsx"
    zaxira = f"limit_{p.code}{sana}.xlsx".replace(" ", "_")
    resp["Content-Disposition"] = (
        f'attachment; filename="{zaxira}"; ' + f"filename*=UTF-8''{_q(fn)}")
    return resp


@login_required
def limit_tahrir_ruxsat_toggle(request, pk):
    """ADMIN: tasdiqlangan limitni tahrirlashga BIR MARTALIK ruxsat berish/olish.
    Ruxsat berilganda zanjir ishtirokchilari jadvalda Умумийni tahrirlab
    «Лимитни юбориш» bilan zanjirga so'rov yuboradi; so'rov yaratilgach
    ruxsat avtomatik yopiladi."""
    p = _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
    lim = _lj_joriy(p, request)
    if not is_admin(request.user):
        raise PermissionDenied("Ruxsatni faqat admin beradi.")
    if request.method == "POST":
        lim.tahrir_ruxsat = not lim.tahrir_ruxsat
        lim.save(update_fields=["tahrir_ruxsat"])
        if lim.tahrir_ruxsat:
            messages.success(request, "🔓 Limit tahririga ruxsat berildi — zanjir "
                                      "ishtirokchilari Умумийni tahrirlab yuborishi mumkin.")
        else:
            messages.info(request, "🔒 Limit tahriri yana yopildi.")
    return redirect(_lj_url(lim))


@login_required
def limit_yangi(request, pk):
    """ASOSIY ADMIN obyektda KEYINGI raqamli umumiy limitni ochadi — Telegram tasdiqlash
    KODI bilan (xuddi PTOga qaytarish kabi). Yangi limit bo'sh — PTO kiritadi;
    oldingi limitlar faol qoladi."""
    from .models import Limit
    from .roles import is_asosiy_admin
    p = _firma_yoki_403(request, get_object_or_404(Project, pk=pk))
    if not is_asosiy_admin(request.user):
        raise PermissionDenied("Keyingi limitni faqat ASOSIY admin ochadi.")
    joriy = p.joriy_limit()
    keyingi_raqam = (p.limitlar.order_by("-raqam").values_list("raqam", flat=True).first() or 0) + 1
    nomi = f"{p.code} — {p.name}"
    ctx0 = {"sarlavha": f"{keyingi_raqam}-умумий лимитни очиш", "obj_nomi": nomi,
            "action_url": reverse("limit_yangi", args=[pk]), "bekor_url": _lj_url(joriy),
            "rejim": "limit_yangi",
            "sub_sabab": f"Янги лимит номини (ихтиёрий, масалан «{keyingi_raqam}-босқич») ёзинг — кейин Telegram'ингизга тасдиқлаш коди юборилади. "
                         f"Ҳозирги {joriy.raqam}-лимит фаол қолади.",
            "label_sabab": "Янги лимит номи (ихтиёрий)", "tugma": f"{keyingi_raqam}-лимитни очиш"}
    if request.method != "POST":
        return render(request, "projects/qaytarish_kod.html", dict(ctx0, sabab="", bosqich="sabab"))
    kod = (request.POST.get("kod") or "").strip()
    if not kod:
        nom = " ".join((request.POST.get("sabab") or request.POST.get("nom") or "").split())[:120]
        if not _qayt_kod_yubor(request, "limit_yangi", pk, nomi, nom,
                               matn=f"{nomi} — {keyingi_raqam}-умумий лимитни очиш"
                                    + (f" («{nom}»)" if nom else "") + "."):
            messages.error(request, "Telegramga kod yuborib bo'lmadi — bot bog'lanishini tekshiring.")
            return redirect(_lj_url(joriy))
        return render(request, "projects/qaytarish_kod.html", dict(ctx0, sabab=nom, bosqich="kod"))
    nom, xato = _qayt_kod_tekshir(request, "limit_yangi", pk, kod)
    if xato:
        messages.error(request, xato)
        if "qaytadan" in xato:
            return redirect(_lj_url(joriy))
        return render(request, "projects/qaytarish_kod.html",
                      dict(ctx0, sabab=request.POST.get("sabab", ""), bosqich="kod"))
    with transaction.atomic():
        raqam = (p.limitlar.order_by("-raqam").values_list("raqam", flat=True).first() or 0) + 1
        lim = Limit.objects.create(project=p, raqam=raqam, nom=nom or "", created_by=request.user)
    messages.success(request, f"{raqam}-лимит ochildi (bo'sh). PTO qatorlarni kiritib zanjirga yuboradi; "
                              f"{joriy.raqam}-лимит ham faol qoladi.")
    return redirect(_lj_url(lim))


@login_required
def limit_request_action(request, pk):
    """Asl amal + «next=jadval» bo'lsa limit jadvaliga qaytarish."""
    resp = _limit_request_action_asl(request, pk)
    if request.POST.get("next") == "jadval" and getattr(resp, "status_code", 0) in (301, 302):
        r0 = LimitChangeRequest.objects.filter(pk=pk).first()
        if r0 is not None:
            return redirect(_lj_url(r0.limit or r0.project.joriy_limit()))
    return resp


@login_required
def weekly_action(request, pk):
    """Asl amal + «next=jadval» bo'lsa limit jadvaliga qaytarish."""
    w0 = WeeklyRequest.objects.filter(pk=pk).first()
    pid = w0.project_id if w0 else None
    resp = _weekly_action_asl(request, pk)
    if (request.POST.get("next") == "jadval" and pid
            and getattr(resp, "status_code", 0) in (301, 302)):
        return redirect(_lj_url(w0.limit or w0.project.joriy_limit()))
    return resp


@login_required
def limit_request_edit(request, pk):
    """Eski alohida tahrir formasi o'rniga — taklif endi limit jadvalining o'zida
    tahrirlanadi (eski havolalar/xatcho'plar ishlashda davom etsin)."""
    r0 = get_object_or_404(LimitChangeRequest, pk=pk)
    _firma_yoki_403(request, r0.project)
    return redirect(_lj_url(r0.limit or r0.project.joriy_limit()))


@login_required
def weekly_edit(request, pk):
    """Eski alohida haftalik tahrir formasi o'rniga — limit jadvali."""
    w0 = get_object_or_404(WeeklyRequest, pk=pk)
    _firma_yoki_403(request, w0.project)
    return redirect(_lj_url(w0.limit or w0.project.joriy_limit(), korinish="amaldagi"))
