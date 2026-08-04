"""
projects/admin.py — Obyektlar (Project) va ish bo'limlari uchun admin.
"""
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.html import format_html

from .models import (
    Firma, LimitChangeItem, LimitChangeRequest, LimitItem, MaterialRequest,
    MaterialRequestItem, Project, UserProfile, WeeklyRequest, WeeklyRequestItem,
    WorkSection,
)


@admin.register(Firma)
class FirmaAdmin(admin.ModelAdmin):
    list_display = ["name", "inn", "director", "phone", "obyektlar_soni"]
    search_fields = ["name", "inn", "director"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        from .roles import visible_firmas
        return visible_firmas(request.user, qs)

    @admin.display(description="Obyektlar")
    def obyektlar_soni(self, obj):
        return obj.projects.count()


def _money(value):
    return f"{value:,.2f}".replace(",", " ")


class FirmaScopedAdmin(admin.ModelAdmin):
    """Admin panelda ham izolyatsiya: superuser bo'lmagan staff faqat o'z
    doirasidagi (visible_projects) yozuvlarni ko'radi. project_lookup —
    modeldan Project'ga boradigan lookup yo'li ("pk"=modelning o'zi Project)."""

    project_lookup = "project"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        from .roles import visible_projects
        return qs.filter(**{f"{self.project_lookup}__in": visible_projects(request.user)})


class WorkSectionInline(admin.TabularInline):
    model = WorkSection
    extra = 1
    fields = ["code", "name", "parent"]


@admin.register(Project)
class ProjectAdmin(FirmaScopedAdmin):
    project_lookup = "pk"
    inlines = [WorkSectionInline]
    list_display = [
        "code", "name", "firma", "status",
        "budget_total_display", "sarflangan_display",
        "qolgan_display", "limit_holati_badge",
    ]
    list_filter = ["status", "firma"]
    search_fields = ["code", "name"]
    autocomplete_fields = ["firma"]
    readonly_fields = ["sarflangan_display", "qolgan_display", "limit_holati_badge", "created_at"]

    @admin.display(description="Umumiy limit")
    def budget_total_display(self, obj):
        if obj.budget_total <= 0:
            return "—"
        return _money(obj.budget_total)

    @admin.display(description="Sarflangan")
    def sarflangan_display(self, obj):
        return _money(obj.sarflangan())

    @admin.display(description="Qolgan")
    def qolgan_display(self, obj):
        if obj.budget_total <= 0:
            return "—"
        return _money(obj.qolgan())

    @admin.display(description="Limit holati")
    def limit_holati_badge(self, obj):
        holat = obj.limit_holati()
        ranglar = {"limitsiz": "#6c757d", "normal": "#198754", "limitga yaqin": "#fd7e14", "RUXSAT KERAK": "#dc3545"}
        rang = ranglar.get(holat, "#6c757d")
        return format_html('<b style="color:white; background:{}; padding:2px 8px; border-radius:6px; white-space:nowrap;">{}</b>', rang, holat)


@admin.register(WorkSection)
class WorkSectionAdmin(FirmaScopedAdmin):
    list_display = ["__str__", "project", "code", "parent"]
    list_filter = ["project"]
    search_fields = ["name", "code", "project__code", "project__name"]
    autocomplete_fields = ["project", "parent"]


class WeeklyRequestItemInline(admin.TabularInline):
    model = WeeklyRequestItem
    extra = 3
    autocomplete_fields = ["work_section"]
    fields = ["kind", "work_section", "name", "note", "unit", "quantity", "unit_price", "qator_summasi"]
    readonly_fields = ["qator_summasi"]

    @admin.display(description="Qator summasi")
    def qator_summasi(self, obj):
        if obj and obj.pk:
            return _money(obj.total)
        return "-"


@admin.action(description="Tasdiqlash (limitdan ayirish)")
def action_approve(modeladmin, request, queryset):
    """Saytdagi tasdiqlash bilan BIR XIL nazorat: limitdan oshish tekshiruvi + audit iz.

    Oldin bu yerda oddiy queryset.update() bor edi — u auditsiz va tekshiruvsiz
    tasdiqlab, saytdagi qoidalarni chetlab o'tardi.
    """
    from django.utils import timezone

    from .views import _items_by_kind, _limit_oshish

    n, oshganlar = 0, []
    for req in queryset.filter(status__in=[WeeklyRequest.Status.DRAFT, WeeklyRequest.Status.SUBMITTED]):
        oshgan = _limit_oshish(req.project, _items_by_kind(req.items.all()),
                               exclude_request_id=req.id)
        if oshgan:
            oshganlar.append(f"{req.project.code} ({req.week_start:%d.%m}): " + "; ".join(oshgan))
            continue
        req.status = WeeklyRequest.Status.APPROVED
        req.approved_by = request.user
        req.approved_at = timezone.now()
        req.pto_notified = False
        req.save(update_fields=["status", "approved_by", "approved_at", "pto_notified"])
        n += 1
    if n:
        messages.success(request, f"{n} ta so'rov tasdiqlandi.")
    if oshganlar:
        messages.error(
            request,
            "Limitdan oshgani uchun tasdiqlanmadi: " + " | ".join(oshganlar)
            + ". Baribir tasdiqlash kerak bo'lsa — saytdagi Tasdiqlar bo'limida "
            "«Limitdan oshsa ham tasdiqlash» tugmasini ishlating.",
        )


@admin.action(description="Tasdiqni bekor qilish (qoralamaga qaytarish)")
def action_to_draft(modeladmin, request, queryset):
    n = queryset.update(status=WeeklyRequest.Status.DRAFT, approved_by=None, approved_at=None)
    messages.success(request, f"{n} ta so'rov qoralamaga qaytarildi.")


@admin.action(description="Tasdiqlash (limitni o'zgartirish)")
def action_approve_limit(modeladmin, request, queryset):
    n = 0
    for req in queryset.filter(status=LimitChangeRequest.Status.PENDING):
        req.approve(request.user)
        n += 1
    if n:
        messages.success(request, f"{n} ta so'rov tasdiqlandi, limit o'zgartirildi.")


@admin.action(description="Rad etish")
def action_reject_limit(modeladmin, request, queryset):
    # Rad etish SABAB talab qiladi (PTO nima uchunligini bilishi kerak).
    # Admin bulk amalida sabab kiritib bo'lmaydi — saytdagi oqimga yo'naltiramiz.
    messages.error(
        request,
        "Rad etish uchun sabab yozish shart. Saytdagi «Limitlar → Tasdiqlar» "
        "bo'limida «Rad etish sababi» maydonini to'ldirib rad eting.",
    )


@admin.register(LimitItem)
class LimitItemAdmin(FirmaScopedAdmin):
    """Umumiy limit tarkibi — FAQAT KO'RISH. Tahrirlash saytda qilinadi
    (PTO kiritadi, admin tasdiqlaydi) — admin panel orqali bu nazoratni
    chetlab o'tib bo'lmasligi kerak."""

    list_display = ["project", "kind", "name", "unit", "quantity", "narx", "summa",
                    "note", "created_at", "updated_at"]
    list_filter = ["kind", "project"]
    search_fields = ["name", "project__code", "project__name"]

    @admin.display(description="Narxi")
    def narx(self, obj):
        return _money(obj.unit_price)

    @admin.display(description="Summa")
    def summa(self, obj):
        return _money(obj.total)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LimitChangeItemInline(admin.TabularInline):
    """So'rovdagi taklif qatorlari — faqat ko'rish uchun."""
    model = LimitChangeItem
    extra = 0
    can_delete = False
    readonly_fields = ["kind", "name", "note", "unit", "quantity", "unit_price", "summa"]
    fields = readonly_fields

    @admin.display(description="Summa")
    def summa(self, obj):
        if obj and obj.pk:
            return _money(obj.total)
        return "-"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(LimitChangeRequest)
class LimitChangeRequestAdmin(FirmaScopedAdmin):
    list_display = ["project", "yangi_material", "yangi_ish", "yangi_mashina", "yangi_jami", "status", "requested_by", "created_at"]
    list_filter = ["status", "project"]
    search_fields = ["project__code", "project__name"]
    readonly_fields = ["old_material", "old_labor", "old_machinery", "requested_by", "created_at",
                       "decided_by", "decided_at", "decision_note"]
    inlines = [LimitChangeItemInline]
    actions = [action_approve_limit, action_reject_limit]

    @admin.display(description="Yangi material")
    def yangi_material(self, obj):
        return _money(obj.new_material)

    @admin.display(description="Yangi ish haqi")
    def yangi_ish(self, obj):
        return _money(obj.new_labor)

    @admin.display(description="Yangi mashina")
    def yangi_mashina(self, obj):
        return _money(obj.new_machinery)

    @admin.display(description="Yangi jami")
    def yangi_jami(self, obj):
        return _money(obj.new_total)


@admin.register(WeeklyRequest)
class WeeklyRequestAdmin(FirmaScopedAdmin):
    inlines = [WeeklyRequestItemInline]
    list_display = ["id", "project", "week_start", "week_end", "number", "jami_summa", "status",
                    "created_by", "approved_by", "approved_at"]
    list_filter = ["status", "project"]
    search_fields = ["number", "project__code", "project__name"]
    date_hierarchy = "week_start"
    autocomplete_fields = ["project"]
    readonly_fields = ["created_by", "created_at", "approved_by", "approved_at"]
    actions = [action_approve, action_to_draft]

    @admin.display(description="Jami summa")
    def jami_summa(self, obj):
        return _money(obj.jami)


# ---- Foydalanuvchini firmaga biriktirish (login yaratganda) ----
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    fk_name = "user"
    verbose_name = "Lavozim va biriktirish"
    verbose_name_plural = "Lavozim va biriktirish (Direktor→firma; PTO/Prorab→obyektlar)"
    fields = ["role", "firma", "projects"]
    autocomplete_fields = ["firma"]
    filter_horizontal = ["projects"]


class UserAdmin(DjangoUserAdmin):
    inlines = [UserProfileInline]
    list_display = DjangoUserAdmin.list_display + ("firma_nomi", "sifatida_kirish_tugma")

    # Eslatma: login/parol jonli tekshiruvi (admin_userform.js) endi jazzmin
    # "custom_js" orqali BARCHA admin sahifalariga ulanadi (settings.py) —
    # shu jumladan «Parolni o'zgartirish» sahifasiga ham.

    def get_queryset(self, request):
        # Foydalanuvchilarni faqat superuser boshqaradi; boshqa staff — faqat o'zini ko'radi
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(pk=request.user.pk)

    @admin.display(description="Firma")
    def firma_nomi(self, obj):
        prof = getattr(obj, "profile", None)
        return prof.firma if prof and prof.firma_id else "—"

    @admin.display(description="Tekshirish")
    def sifatida_kirish_tugma(self, obj):
        """Admin xodim ko'zi bilan tizimni tekshiradi (parolsiz kirish)."""
        if obj.is_superuser or not obj.is_active:
            return "—"
        return format_html(
            '<a class="button" href="/sifatida-kirish/{}/" '
            'onclick="return confirm(\'«{}» sifatida kirasizmi?\');">👤 Sifatida kirish</a>',
            obj.pk, obj.username)


User = get_user_model()
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


class MaterialRequestItemInline(admin.TabularInline):
    model = MaterialRequestItem
    extra = 0
    fields = ["name", "unit", "quantity", "note"]


@admin.register(MaterialRequest)
class MaterialRequestAdmin(FirmaScopedAdmin):
    list_display = ["id", "project", "created_by", "status", "created_at", "decided_by", "decided_at"]
    list_filter = ["status", "project"]
    search_fields = ["project__code", "project__name"]
    readonly_fields = ["created_by", "created_at", "decided_by", "decided_at"]
    inlines = [MaterialRequestItemInline]


# ---------------------------------------------------------------------------
# ADMIN PANEL — FAQAT SUPERUSER (asosiy admin) uchun.
# Xodim (staff) belgisi bo'lsa ham superuser bo'lmasa kira olmaydi.
# Adminka logini ham umumiy /login/ sahifasi orqali o'tadi — shunda
# Telegram 2FA (agar admin bog'lagan bo'lsa) adminkani ham himoya qiladi.
# ---------------------------------------------------------------------------
from urllib.parse import urlencode

from django.shortcuts import redirect


def _admin_ruxsat(request):
    return bool(request.user.is_active and request.user.is_superuser)


def _admin_login_redirect(request, extra_context=None):
    nxt = request.GET.get("next") or "/admin/"
    return redirect("/login/?" + urlencode({"next": nxt}))


admin.site.has_permission = _admin_ruxsat
admin.site.login = _admin_login_redirect
