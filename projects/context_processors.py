def approvals(request):
    """Sidebar uchun kutilayotgan tasdiqlar soni.

    DIREKTOR o'z navbatini (dir) ko'radi, ADMIN o'z navbatini (adm/submitted).
    Superuser ikkalasini ham hisoblaydi.
    """
    u = getattr(request, "user", None)
    if not (u and u.is_authenticated):
        return {}
    from .roles import is_admin, is_director
    _dir = is_director(u)
    _adm = is_admin(u)
    if not (_dir or _adm):
        return {}
    from .models import LimitChangeRequest, WeeklyRequest
    lc = wc = 0
    if _dir:
        lc += LimitChangeRequest.objects.filter(status="dir").count()
        wc += WeeklyRequest.objects.filter(status="dir").count()
    if _adm:
        lc += LimitChangeRequest.objects.filter(status="adm").count()
        wc += WeeklyRequest.objects.filter(status="submitted").count()
    return {"pending_lc": lc, "pending_wc": wc, "pending_total": lc + wc}
