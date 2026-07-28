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
