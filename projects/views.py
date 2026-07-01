from django.shortcuts import render
from .models import Project


def _money(value):
    return f"{value:,.0f}".replace(",", " ")


RANGLAR = {
    "limitsiz": "#6c757d",
    "normal": "#198754",
    "limitga yaqin": "#fd7e14",
    "RUXSAT KERAK": "#dc3545",
}


def dashboard(request):
    obyektlar = Project.objects.all().order_by("code")
    qatorlar = []
    jami_limit = 0
    jami_sarf = 0
    for p in obyektlar:
        limit = p.budget_total or 0
        sarf = p.sarflangan()
        qoldiq = p.qolgan()
        holat = p.limit_holati()
        foiz = float(sarf) / float(limit) * 100 if limit else 0
        jami_limit += limit
        jami_sarf += sarf
        qatorlar.append({
            "obj": p,
            "limit_str": _money(limit),
            "sarf_str": _money(sarf),
            "qoldiq_str": _money(qoldiq),
            "holat": holat,
            "rang": RANGLAR.get(holat, "#6c757d"),
            "foiz": round(foiz),
            "foiz_bar": min(round(foiz), 100),
        })
    kontekst = {
        "qatorlar": qatorlar,
        "jami_limit_str": _money(jami_limit),
        "jami_sarf_str": _money(jami_sarf),
        "jami_qoldiq_str": _money(jami_limit - jami_sarf),
        "soni": len(qatorlar),
    }
    return render(request, "projects/dashboard.html", kontekst)
