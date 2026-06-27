from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from . import services
from .models import (
    Issue, IssueItem, Material, MaterialCategory, Receipt, ReceiptItem,
    StockBalance, StockMovement, Supplier, Warehouse,
)


@admin.register(MaterialCategory)
class MaterialCategoryAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "unit", "category"]
    list_filter = ["category", "unit"]
    search_fields = ["code", "name"]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "inn", "phone"]
    search_fields = ["name", "inn"]


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ["name", "project"]
    list_filter = ["project"]
    search_fields = ["name"]


class ReceiptItemInline(admin.TabularInline):
    model = ReceiptItem
    extra = 3
    autocomplete_fields = ["material"]


@admin.action(description="✓ Qayd qilish (ostatkaga kiritish)")
def action_post_receipt(modeladmin, request, queryset):
    ok = 0
    for receipt in queryset:
        try:
            services.post_receipt(receipt)
            ok += 1
        except ValidationError as e:
            messages.error(request, f"Prixod #{receipt.id}: {e.message}")
    if ok:
        messages.success(request, f"{ok} ta prixod qayd qilindi.")


@admin.action(description="✗ Qaydni bekor qilish")
def action_unpost_receipt(modeladmin, request, queryset):
    for receipt in queryset:
        try:
            services.unpost_receipt(receipt)
        except ValidationError as e:
            messages.error(request, f"Prixod #{receipt.id}: {e.message}")


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    inlines = [ReceiptItemInline]
    list_display = ["id", "date", "warehouse", "supplier", "doc_number", "total", "is_posted"]
    list_filter = ["is_posted", "warehouse", "supplier"]
    search_fields = ["doc_number"]
    date_hierarchy = "date"
    actions = [action_post_receipt, action_unpost_receipt]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_posted:
            return [f.name for f in self.model._meta.fields]
        return []


class IssueItemInline(admin.TabularInline):
    model = IssueItem
    extra = 3
    autocomplete_fields = ["material"]
    readonly_fields = ["unit_cost"]


@admin.action(description="✓ Qayd qilish (ostatkadan chiqarish)")
def action_post_issue(modeladmin, request, queryset):
    ok = 0
    for issue in queryset:
        try:
            services.post_issue(issue)
            ok += 1
        except ValidationError as e:
            messages.error(request, f"Rasxod #{issue.id}: {e.message}")
    if ok:
        messages.success(request, f"{ok} ta rasxod qayd qilindi.")


@admin.action(description="✗ Qaydni bekor qilish")
def action_unpost_issue(modeladmin, request, queryset):
    for issue in queryset:
        try:
            services.unpost_issue(issue)
        except ValidationError as e:
            messages.error(request, f"Rasxod #{issue.id}: {e.message}")


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    inlines = [IssueItemInline]
    list_display = ["id", "date", "warehouse", "work_section", "recipient", "total", "is_posted"]
    list_filter = ["is_posted", "warehouse", "work_section__project"]
    search_fields = ["doc_number", "recipient"]
    date_hierarchy = "date"
    actions = [action_post_issue, action_unpost_issue]
    autocomplete_fields = ["work_section"]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.is_posted:
            return [f.name for f in self.model._meta.fields]
        return []


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ["warehouse", "material", "quantity", "avg_cost", "total_value"]
    list_filter = ["warehouse", "warehouse__project"]
    search_fields = ["material__name", "material__code"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["date", "direction", "warehouse", "material", "quantity", "unit_cost", "total_cost", "doc_type", "doc_id"]
    list_filter = ["direction", "warehouse", "date"]
    search_fields = ["material__name"]
    date_hierarchy = "date"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False