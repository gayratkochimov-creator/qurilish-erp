def approvals(request):
    """Sidebar uchun kutilayotgan tasdiqlar soni.

    DIREKTOR o'z navbatini (dir) ko'radi, ADMIN o'z navbatini (adm/submitted).
    Superuser ikkalasini ham hisoblaydi.
    """
    u = getattr(request, "user", None)
    if not (u and u.is_authenticated):
        return {}
    from .roles import is_admin, is_director, is_prorab, is_pto, visible_projects
    _dir = is_director(u)
    _adm = is_admin(u)
    _pto = is_pto(u)
    _prorab = is_prorab(u)
    from .models import LimitChangeRequest, MaterialRequest, WeeklyRequest
    # Firma/obyekt izolyatsiyasi: har kim faqat o'z navbatini ko'radi (admin=hammasi)
    _vp = visible_projects(u)
    ctx = {"nav_is_pto": _pto, "nav_is_prorab": _prorab,
           "nav_is_director": _dir, "nav_is_admin": _adm}
    # ADMIN xabarlari — o'qilmaganlari har sahifa tepasida ko'rinadi
    try:
        from django.db.models import Q
        from .models import Xabar
        oqilgan_ids = list(u.xabar_oqilgan.values_list("xabar_id", flat=True))
        ctx["mening_xabarlarim"] = list(
            Xabar.objects.filter(Q(hammaga=True) | Q(kimga=u))
            .exclude(id__in=oqilgan_ids).exclude(yubordi=u)
            .select_related("yubordi").distinct().order_by("-created_at")[:10])
    except Exception:
        ctx["mening_xabarlarim"] = []
    # Moliyalashtirish: menyu faqat PTO/admin/buxgalterga; qarz belgisi
    try:
        from .roles import is_bux
        ctx["nav_moliya"] = bool(u.is_superuser or is_bux(u) or _pto)
        if u.is_superuser or is_bux(u):
            from decimal import Decimal
            from django.db.models import DecimalField, ExpressionWrapper, F, Sum
            from .models import Moliya, WeeklyRequestItem
            _lt = ExpressionWrapper(F("quantity") * F("unit_price"),
                                    output_field=DecimalField(max_digits=20, decimal_places=2))
            tas = (WeeklyRequestItem.objects
                   .filter(request__project__in=_vp, request__status="approved")
                   .aggregate(s=Sum(_lt))["s"] or Decimal("0"))
            ber = (Moliya.objects
                   .filter(item__request__project__in=_vp, item__request__status="approved")
                   .aggregate(s=Sum("summa"))["s"] or Decimal("0"))
            ctx["nav_qarz_bor"] = tas - ber > Decimal("0.5")
    except Exception:
        pass
    # Prorab -> PTO material so'rovlari (PTO uchun kutayotgan soni)
    if _pto:
        ctx["pending_mat"] = MaterialRequest.objects.filter(
            project__in=_vp, status="pending").count()
    if not (_dir or _adm):
        return ctx
    lc = wc = 0
    if _dir:
        lc += LimitChangeRequest.objects.filter(status="dir", project__in=_vp).count()
        wc += WeeklyRequest.objects.filter(status="dir", project__in=_vp).count()
    if _adm:
        lc += LimitChangeRequest.objects.filter(status="adm", project__in=_vp).count()
        wc += WeeklyRequest.objects.filter(status="submitted", project__in=_vp).count()
    ctx.update({"pending_lc": lc, "pending_wc": wc, "pending_total": lc + wc})
    return ctx
