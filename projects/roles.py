"""Rol tekshiruvlari: PTO, Direktor, Admin.

Tasdiqlash zanjiri: PTO kiritadi → Direktor tasdiqlaydi → Admin tasdiqlaydi.
"""

PTO_GROUP = "PTO"
DIREKTOR_GROUP = "Direktor"


def is_pto(user):
    """PTO — limitni kiritadi / o'zgartirishga so'rov yuboradi."""
    return bool(
        user.is_authenticated
        and (user.is_superuser or user.groups.filter(name=PTO_GROUP).exists())
    )


def is_director(user):
    """Direktor — PTO so'rovini birinchi bo'lib tasdiqlaydi (admindan oldin)."""
    return bool(
        user.is_authenticated
        and (user.is_superuser or user.groups.filter(name=DIREKTOR_GROUP).exists())
    )


def is_admin(user):
    """Admin — direktordan o'tgan so'rovni yakuniy tasdiqlaydi (superuser)."""
    return bool(user.is_authenticated and user.is_superuser)


# ---------------------------------------------------------------------------
# Firma bo'yicha ko'rish chegarasi (multi-tenant)
# Admin (superuser) — hammasini ko'radi. Direktor/PTO — faqat o'z firmasini.
# Firma biriktirilmagan oddiy user — hech narsa ko'rmaydi (xavfsiz default).
# ---------------------------------------------------------------------------

def user_firma(user):
    """Foydalanuvchining firmasi (yo'q bo'lsa None)."""
    if not (user and getattr(user, "is_authenticated", False)):
        return None
    prof = getattr(user, "profile", None)
    return prof.firma if prof else None


def visible_projects(user, qs=None):
    """Foydalanuvchi ko'ra oladigan obyektlar (Project) queryset."""
    from .models import Project
    if qs is None:
        qs = Project.objects.all()
    if user.is_superuser:
        return qs
    f = user_firma(user)
    return qs.filter(firma=f) if f else qs.none()


def visible_firmas(user, qs=None):
    """Foydalanuvchi ko'ra oladigan firmalar (dropdown/filtr uchun)."""
    from .models import Firma
    if qs is None:
        qs = Firma.objects.all()
    if user.is_superuser:
        return qs
    f = user_firma(user)
    return qs.filter(pk=f.pk) if f else qs.none()


def can_access_project(user, project):
    """Berilgan obyektga kirish huquqi bormi (pk oluvchi view'lar uchun)."""
    if user.is_superuser:
        return True
    if project is None:
        return False
    f = user_firma(user)
    return bool(f and getattr(project, "firma_id", None) == f.pk)
