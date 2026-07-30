def approvals(request):
    """Sidebar uchun kutilayotgan tasdiqlar soni.

    DIREKTOR o'z navbatini (dir) ko'radi, ADMIN o'z navbatini (adm/submitted).
    Superuser ikkalasini ham hisoblaydi.
    """
    u = getattr(request, "user", None)
    if not (u and u.is_authenticated):
        return {}
    from .roles import is_admin, is_director, visible_projects
    _dir = is_director(u)
    _adm = is_admin(u)
    if not (_dir or _adm):
        return {}
    from .models import LimitChangeRequest, WeeklyRequest
    # Firma izolyatsiyasi: direktor faqat o'z firmasidagi navbatni ko'radi (admin=hammasi)
    _vp = visible_projects(u)
    lc = wc = 0
    if _dir:
        lc += LimitChangeRequest.objects.filter(status="dir", project__in=_vp).count()
        wc += WeeklyRequest.objects.filter(status="dir", project__in=_vp).count()
    if _adm:
        lc += LimitChangeRequest.objects.filter(status="adm", project__in=_vp).count()
        wc += WeeklyRequest.objects.filter(status="submitted", project__in=_vp).count()
    return {"pending_lc": lc, "pending_wc": wc, "pending_total": lc + wc}
