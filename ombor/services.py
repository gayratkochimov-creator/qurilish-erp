from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from .models import Issue, Receipt, StockBalance, StockMovement

ALLOW_NEGATIVE = getattr(settings, "OMBOR_ALLOW_NEGATIVE", False)
CENT = Decimal("0.01")


def _get_balance_for_update(warehouse_id, material_id):
    balance, _ = (
        StockBalance.objects.select_for_update()
        .get_or_create(
            warehouse_id=warehouse_id,
            material_id=material_id,
            defaults={"quantity": Decimal("0"), "total_value": Decimal("0.00")},
        )
    )
    return balance


@transaction.atomic
def post_receipt(receipt: Receipt):
    if receipt.is_posted:
        raise ValidationError("Bu prixod allaqachon qayd qilingan.")
    items = list(receipt.items.select_related("material"))
    if not items:
        # Materialsiz, faqat NAKLADNOY rasmi (itogo bilan) kiritilgan hujjat —
        # ombor qoldig'iga ta'sir qilmaydi, lekin qayd qilinadi (arxiv uchun)
        if receipt.images.filter(turi="nakladnoy").exists():
            receipt.is_posted = True
            receipt.save(update_fields=["is_posted"])
            return
        raise ValidationError("Prixodda birorta material yo'q.")

    project_id = receipt.warehouse.project_id
    for item in items:
        balance = _get_balance_for_update(receipt.warehouse_id, item.material_id)
        line_total = (item.quantity * item.unit_price).quantize(CENT)

        balance.quantity += item.quantity
        balance.total_value += line_total
        balance.save(update_fields=["quantity", "total_value"])

        StockMovement.objects.create(
            warehouse_id=receipt.warehouse_id,
            material_id=item.material_id,
            project_id=project_id,
            direction=StockMovement.IN,
            quantity=item.quantity,
            unit_cost=item.unit_price,
            total_cost=line_total,
            date=receipt.date,
            doc_type="receipt",
            doc_id=receipt.id,
        )

    receipt.is_posted = True
    receipt.save(update_fields=["is_posted"])


@transaction.atomic
def unpost_receipt(receipt: Receipt):
    if not receipt.is_posted:
        raise ValidationError("Bu prixod qayd qilinmagan.")
    # Qaytarish ombordagi qoldiqni MINUSGA tushirmasin: kirim allaqachon
    # rasxod bilan ishlatilgan bo'lsa — avval o'sha rasxod qaydi bekor qilinsin.
    # Bir xil material bir necha qatorda bo'lishi mumkin — material bo'yicha
    # YIG'IB tekshiramiz (qator-qator tekshirish minusni o'tkazib yuborardi);
    # qiymat (total_value) ham minusga tushmasin — o'rtacha narx buzilmasin.
    jami = {}
    for mv in StockMovement.objects.filter(doc_type="receipt", doc_id=receipt.id):
        k = (mv.warehouse_id, mv.material_id)
        q, v, nom = jami.get(k, (Decimal("0"), Decimal("0"), ""))
        jami[k] = (q + mv.quantity, v + mv.total_cost, mv.material.name)
    for (wh_id, mat_id), (q, v, nom) in jami.items():
        bal = StockBalance.objects.filter(
            warehouse_id=wh_id, material_id=mat_id
        ).first()
        joriy_q = bal.quantity if bal else Decimal("0")
        joriy_v = bal.total_value if bal else Decimal("0")
        if joriy_q - q < 0 or joriy_v - v < Decimal("-0.05"):
            raise ValidationError(
                f"Qaydni bekor qilib bo'lmaydi: «{nom}» qoldig'i "
                f"{joriy_q} — bu kirim ({q}) allaqachon rasxodda ishlatilgan. "
                "Avval tegishli rasxod qaydini bekor qiling."
            )
    _reverse_movements("receipt", receipt.id)
    receipt.is_posted = False
    receipt.save(update_fields=["is_posted"])


@transaction.atomic
def post_issue(issue: Issue):
    if issue.is_posted:
        raise ValidationError("Bu rasxod allaqachon qayd qilingan.")
    items = list(issue.items.select_related("material"))
    if not items:
        raise ValidationError("Rasxodda birorta material yo'q.")

    project_id = issue.warehouse.project_id
    for item in items:
        balance = _get_balance_for_update(issue.warehouse_id, item.material_id)

        if balance.quantity < item.quantity and not ALLOW_NEGATIVE:
            raise ValidationError(
                f"Omborda yetarli emas: «{item.material}» — "
                f"qoldiq {balance.quantity}, so'ralgan {item.quantity}."
            )

        avg = balance.avg_cost
        line_cost = (item.quantity * avg).quantize(CENT)

        balance.quantity -= item.quantity
        balance.total_value -= line_cost
        if balance.quantity <= 0:
            balance.quantity = Decimal("0")
            balance.total_value = Decimal("0.00")
        balance.save(update_fields=["quantity", "total_value"])

        item.unit_cost = avg
        item.save(update_fields=["unit_cost"])

        StockMovement.objects.create(
            warehouse_id=issue.warehouse_id,
            material_id=item.material_id,
            project_id=project_id,
            work_section_id=issue.work_section_id,
            direction=StockMovement.OUT,
            quantity=-item.quantity,
            unit_cost=avg,
            total_cost=-line_cost,
            date=issue.date,
            doc_type="issue",
            doc_id=issue.id,
        )

    issue.is_posted = True
    issue.save(update_fields=["is_posted"])


@transaction.atomic
def unpost_issue(issue: Issue):
    if not issue.is_posted:
        raise ValidationError("Bu rasxod qayd qilinmagan.")
    _reverse_movements("issue", issue.id)
    issue.is_posted = False
    issue.save(update_fields=["is_posted"])


def _reverse_movements(doc_type, doc_id):
    movements = StockMovement.objects.select_for_update().filter(
        doc_type=doc_type, doc_id=doc_id
    )
    for mv in movements:
        balance = _get_balance_for_update(mv.warehouse_id, mv.material_id)
        balance.quantity -= mv.quantity
        balance.total_value -= mv.total_cost
        balance.save(update_fields=["quantity", "total_value"])
    movements.delete()


@transaction.atomic
def recalculate_balance(warehouse_id, material_id):
    balance = _get_balance_for_update(warehouse_id, material_id)
    agg = StockMovement.objects.filter(
        warehouse_id=warehouse_id, material_id=material_id
    ).aggregate(q=Sum("quantity"), v=Sum("total_cost"))
    balance.quantity = agg["q"] or Decimal("0")
    balance.total_value = agg["v"] or Decimal("0.00")
    balance.save(update_fields=["quantity", "total_value"])
    return balance


def ostatka(warehouse_id=None, project_id=None, only_nonzero=True):
    qs = StockBalance.objects.select_related("warehouse", "material", "warehouse__project")
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)
    if project_id:
        qs = qs.filter(warehouse__project_id=project_id)
    if only_nonzero:
        qs = qs.exclude(quantity=0)
    return qs.order_by("warehouse__name", "material__name")


def ostatka_on_date(target_date, warehouse_id=None, material_id=None):
    qs = StockMovement.objects.filter(date__lte=target_date)
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)
    if material_id:
        qs = qs.filter(material_id=material_id)
    rows = (
        qs.values("warehouse_id", "material_id")
        .annotate(qty=Sum("quantity"), value=Sum("total_cost"))
    )
    return {
        (r["warehouse_id"], r["material_id"]): {
            "qty": r["qty"] or Decimal("0"),
            "value": r["value"] or Decimal("0.00"),
        }
        for r in rows
    }