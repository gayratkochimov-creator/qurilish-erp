"""
projects/admin.py — Obyektlar (Project) va ish bo'limlari uchun admin.
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import Project, WorkSection


def _money(value):
    return f"{value:,.2f}".replace(",", " ")


class WorkSectionInline(admin.TabularInline):
    model = WorkSection
    extra = 1
    fields = ["code", "name", "parent"]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    inlines = [WorkSectionInline]
    list_display = [
        "code", "name", "status",
        "budget_total_display", "sarflangan_display",
        "qolgan_display", "limit_holati_badge",
    ]
    list_filter = ["status"]
    search_fields = ["code", "name"]
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
class WorkSectionAdmin(admin.ModelAdmin):
    list_display = ["__str__", "project", "code", "parent"]
    list_filter = ["project"]
    search_fields = ["name", "code", "project__code", "project__name"]
    autocomplete_fields = ["project", "parent"]
