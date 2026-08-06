from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

QTY = dict(max_digits=18, decimal_places=3)
MONEY = dict(max_digits=18, decimal_places=2)


class MaterialCategory(models.Model):
    name = models.CharField("Kategoriya", max_length=255, unique=True)

    class Meta:
        verbose_name = "Material kategoriyasi"
        verbose_name_plural = "Material kategoriyalari"

    def __str__(self):
        return self.name


class Material(models.Model):
    code = models.CharField("Kod / artikul", max_length=64, unique=True)
    name = models.CharField("Nomi", max_length=255)
    unit = models.CharField("O'lchov birligi", max_length=32, help_text="dona, kg, m3, m2, t ...")
    category = models.ForeignKey(
        MaterialCategory, null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name="Kategoriya",
    )

    class Meta:
        verbose_name = "Material"
        verbose_name_plural = "Materiallar"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"


class Supplier(models.Model):
    name = models.CharField("Nomi", max_length=255)
    inn = models.CharField("STIR", max_length=20, blank=True)
    phone = models.CharField("Telefon", max_length=32, blank=True)

    class Meta:
        verbose_name = "Yetkazib beruvchi"
        verbose_name_plural = "Yetkazib beruvchilar"

    def __str__(self):
        return self.name


class Warehouse(models.Model):
    code = models.CharField("Kod", max_length=64, blank=True)
    name = models.CharField("Nomi", max_length=255)
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True,
        on_delete=models.PROTECT, related_name="warehouses",
        verbose_name="Obyekt", help_text="Bo'sh = markaziy ombor",
    )

    class Meta:
        verbose_name = "Ombor"
        verbose_name_plural = "Omborlar"

    @property
    def kod_nom(self):
        return f"{self.code} — {self.name}" if self.code else self.name

    def __str__(self):
        base = self.kod_nom
        if self.project_id:
            return f"{base} ({self.project.code})"
        return f"{base} (markaziy)"


class Receipt(models.Model):
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT,
        related_name="receipts", verbose_name="Ombor",
    )
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT,
        related_name="receipts", verbose_name="Yetkazib beruvchi",
    )
    doc_number = models.CharField("Hujjat / nakladnoy raqami", max_length=64, blank=True)
    date = models.DateField("Sana")
    note = models.CharField("Izoh", max_length=255, blank=True)
    image = models.ImageField("Mahsulot rasmi", upload_to="prixod/%Y/%m/", null=True, blank=True)
    # Obyektga TO'G'RIDAN-TO'G'RI kelgan material: qayd qilinganda rasxod
    # qoralamasi avto ochiladi — prorab miqdorni tasdiqlagach rasxod qayd bo'ladi
    obyektga_avto = models.BooleanField(
        "Obyektga to'g'ridan-to'g'ri (rasxodga avto o'tadi)", default=False)
    # Prorab qabul qilmasdan RAD etsa — sababi (snab ko'rib to'g'irlaydi/o'chiradi)
    rad_note = models.CharField("Prorab rad sababi", max_length=500, blank=True)
    rad_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="prixod_rad_etgan", verbose_name="Rad etgan prorab",
    )
    rad_at = models.DateTimeField("Rad vaqti", null=True, blank=True)
    is_posted = models.BooleanField("Qayd qilingan", default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Prixod (kirim)"
        verbose_name_plural = "Prixodlar (kirim)"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Prixod #{self.id} — {self.date}"

    @property
    def total(self):
        return sum((i.total for i in self.items.all()), Decimal("0.00"))


class ReceiptImage(models.Model):
    """Prixod rasmlari: mahsulot rasmlari (5 tagacha) va nakladnoy rasmlari.

    Nakladnoy rasmi bilan birga uning ITOGO summasi ham yoziladi —
    ketma-ket bir nechta nakladnoy biriktirsa bo'ladi."""

    class Turi(models.TextChoices):
        PRODUCT = "product", "Mahsulot rasmi"
        NAKLADNOY = "nakladnoy", "Nakladnoy rasmi"

    receipt = models.ForeignKey(
        Receipt, on_delete=models.CASCADE,
        related_name="images", verbose_name="Prixod",
    )
    turi = models.CharField("Turi", max_length=16, choices=Turi.choices, default=Turi.PRODUCT)
    image = models.ImageField("Rasm", upload_to="prixod/%Y/%m/")
    summa = models.DecimalField(
        "Nakladnoy itogo summasi", null=True, blank=True, **MONEY,
        help_text="Faqat nakladnoy rasmi uchun",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Prixod rasmi"
        verbose_name_plural = "Prixod rasmlari"

    def __str__(self):
        return f"Prixod #{self.receipt_id} — {self.get_turi_display()}"


class ReceiptItem(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="items")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, verbose_name="Material")
    quantity = models.DecimalField("Miqdor", validators=[MinValueValidator(Decimal("0.001"))], **QTY)
    unit_price = models.DecimalField("Kirim narxi (1 birlik)", validators=[MinValueValidator(0)], **MONEY)

    class Meta:
        verbose_name = "Prixod qatori"
        verbose_name_plural = "Prixod qatorlari"

    def __str__(self):
        return f"{self.material} × {self.quantity}"

    @property
    def total(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))


class Issue(models.Model):
    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT,
        related_name="issues", verbose_name="Ombor",
    )
    work_section = models.ForeignKey(
        "projects.WorkSection", null=True, blank=True,
        on_delete=models.PROTECT, related_name="issues",
        verbose_name="Ish bo'limi", help_text="Qaysi ishga sarflandi",
    )
    recipient = models.CharField("Kim oldi (brigada / mas'ul)", max_length=255, blank=True)
    doc_number = models.CharField("Hujjat raqami", max_length=64, blank=True)
    date = models.DateField("Sana")
    note = models.CharField("Izoh", max_length=255, blank=True)
    # Prixoddan AVTO ochilgan rasxod (obyektga to'g'ridan-to'g'ri kelgan material).
    # Bunday rasxodni faqat PRORAB miqdorni tasdiqlagach qayd qilish mumkin.
    source_receipt = models.ForeignKey(
        Receipt, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="auto_issues", verbose_name="Manba prixod (avto)",
    )
    prorab_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="rasxod_tasdiqlagan", verbose_name="Miqdorni tasdiqlagan prorab",
    )
    prorab_at = models.DateTimeField("Prorab tasdig'i vaqti", null=True, blank=True)
    is_posted = models.BooleanField("Qayd qilingan", default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Rasxod (chiqim)"
        verbose_name_plural = "Rasxodlar (chiqim)"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Rasxod #{self.id} — {self.date}"

    @property
    def total(self):
        return sum((i.total for i in self.items.all()), Decimal("0.00"))


class IssueItem(models.Model):
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="items")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, verbose_name="Material")
    quantity = models.DecimalField("Miqdor", validators=[MinValueValidator(Decimal("0.001"))], **QTY)
    unit_cost = models.DecimalField("Hisobiy narx (1 birlik)", default=0, editable=False, **MONEY)

    class Meta:
        verbose_name = "Rasxod qatori"
        verbose_name_plural = "Rasxod qatorlari"

    def __str__(self):
        return f"{self.material} × {self.quantity}"

    @property
    def total(self):
        return (self.quantity * self.unit_cost).quantize(Decimal("0.01"))


class StockBalance(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="balances")
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="balances")
    quantity = models.DecimalField("Qoldiq miqdori", default=0, **QTY)
    total_value = models.DecimalField("Qoldiq qiymati", default=0, **MONEY)

    class Meta:
        verbose_name = "Ostatka"
        verbose_name_plural = "Ostatkalar"
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "material"], name="uniq_balance_wh_material")
        ]

    def __str__(self):
        return f"{self.warehouse} / {self.material}: {self.quantity}"

    @property
    def avg_cost(self):
        if self.quantity and self.quantity != 0:
            return (self.total_value / self.quantity).quantize(Decimal("0.01"))
        return Decimal("0.00")


class StockMovement(models.Model):
    IN = "in"
    OUT = "out"
    DIRECTIONS = [(IN, "Kirim"), (OUT, "Chiqim")]

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="movements")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="movements")
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True,
        on_delete=models.PROTECT, related_name="movements",
    )
    work_section = models.ForeignKey(
        "projects.WorkSection", null=True, blank=True,
        on_delete=models.PROTECT, related_name="movements",
    )
    direction = models.CharField(max_length=3, choices=DIRECTIONS)
    quantity = models.DecimalField("Miqdor (ishorali)", **QTY)
    unit_cost = models.DecimalField("Birlik narxi", **MONEY)
    total_cost = models.DecimalField("Summa (ishorali)", **MONEY)
    date = models.DateField()
    doc_type = models.CharField(max_length=16)
    doc_id = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Harakat"
        verbose_name_plural = "Harakatlar jurnali"
        ordering = ["date", "id"]
        indexes = [
            models.Index(fields=["warehouse", "material", "date"]),
            models.Index(fields=["doc_type", "doc_id"]),
        ]

    def __str__(self):
        return f"{self.get_direction_display()} {self.material} {self.quantity}"