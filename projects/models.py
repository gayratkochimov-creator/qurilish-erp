"""
projects/models.py
"""
from decimal import Decimal
from django.conf import settings
from django.db import models, transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum

MONEY = dict(max_digits=18, decimal_places=2)
QTY = dict(max_digits=18, decimal_places=3)

# quantity * unit_price ni bitta so'rovda hisoblash uchun
_LINE_TOTAL = ExpressionWrapper(
    F("quantity") * F("unit_price"),
    output_field=DecimalField(max_digits=20, decimal_places=2),
)


class Firma(models.Model):
    """Qurilish firmasi (MChJ)."""

    name = models.CharField("Nomi", max_length=255, unique=True)
    inn = models.CharField("STIR", max_length=20, blank=True)
    director = models.CharField("Rahbar", max_length=255, blank=True)
    phone = models.CharField("Telefon", max_length=64, blank=True)
    address = models.CharField("Manzil", max_length=255, blank=True)
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)

    class Meta:
        verbose_name = "Firma"
        verbose_name_plural = "Firmalar"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Project(models.Model):
    """Qurilish obyekti (loyiha)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        PAUSED = "paused", "To'xtatilgan"
        DONE = "done", "Yakunlangan"

    firma = models.ForeignKey(
        Firma, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="projects", verbose_name="Firma",
    )
    code = models.CharField("Kod", max_length=64, unique=True)
    name = models.CharField("Nomi", max_length=255)
    location = models.CharField("Manzil / joy", max_length=255, blank=True)
    start_date = models.DateField("Boshlanish sanasi", null=True, blank=True)
    end_date = models.DateField("Tugash sanasi", null=True, blank=True)
    status = models.CharField(
        "Holati", max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    limit_material = models.DecimalField("Material limiti", max_digits=18, decimal_places=2, default=0)
    limit_labor = models.DecimalField("Ish haqi limiti", max_digits=18, decimal_places=2, default=0)
    limit_machinery = models.DecimalField("Mashina chasti limiti", max_digits=18, decimal_places=2, default=0)
    limit_other = models.DecimalField("Ko'zda tutilmagan xarajatlar limiti", max_digits=18, decimal_places=2, default=0)
    # Limit TASDIQLANGACH zanjir ishtirokchilari uni tahrirlay olmaydi;
    # admin shu bayroq bilan BIR MARTALIK ruxsat beradi (so'rov yuborilgach o'chadi)
    limit_tahrir_ruxsat = models.BooleanField("Limit tahririga admin ruxsati", default=False)
    # Umumiy limit amal qilish davri (jadval sarlavhasida ko'rinadi)
    limit_start = models.DateField("Umumiy limit boshlanishi", null=True, blank=True)
    limit_end = models.DateField("Umumiy limit tugashi", null=True, blank=True)
    grafik_start = models.DateField("Grafik boshlanish sanasi", null=True, blank=True)
    grafik_end = models.DateField("Grafik tugash sanasi", null=True, blank=True)
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)

    class Meta:
        verbose_name = "Obyekt"
        verbose_name_plural = "Obyektlar"

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def budget_total(self):
        """Umumiy limit = material + ish haqi + mashina chasti + ko'zda tutilmagan."""
        return ((self.limit_material or Decimal("0")) + (self.limit_labor or Decimal("0"))
                + (self.limit_machinery or Decimal("0")) + (self.limit_other or Decimal("0")))

    def recompute_limits(self, save=True):
        """Kategoriya limitlarini «limit ichi» (LimitItem) yig'indisidan qayta hisoblash."""
        agg = (LimitItem.objects.filter(project=self)
               .values("kind").annotate(s=Sum(_LINE_TOTAL)))
        d = {"material": Decimal("0.00"), "labor": Decimal("0.00"),
             "machinery": Decimal("0.00"), "other": Decimal("0.00")}
        for r in agg:
            if r["kind"] in d:          # «avans» — limit byudjetiga kirmaydi
                d[r["kind"]] = r["s"] or Decimal("0.00")
        self.limit_material = d["material"]
        self.limit_labor = d["labor"]
        self.limit_machinery = d["machinery"]
        self.limit_other = d["other"]
        if save:
            self.save(update_fields=["limit_material", "limit_labor", "limit_machinery", "limit_other"])
        return d

    def sarf_by_kind(self):
        """Turlar bo'yicha alohida sarf (bitta so'rov)."""
        d = {"material": Decimal("0.00"), "labor": Decimal("0.00"),
             "machinery": Decimal("0.00"), "other": Decimal("0.00")}
        rows = (
            WeeklyRequestItem.objects.filter(
                request__project=self,
                request__status=WeeklyRequest.Status.APPROVED,
            )
            .values("kind")
            .annotate(s=Sum(_LINE_TOTAL))
        )
        for r in rows:
            if r["kind"] in d:          # «avans» sarf toifalariga kirmaydi
                d[r["kind"]] = r["s"] or Decimal("0.00")
        return d

    def sarflangan(self):
        """Tasdiqlangan haftalik so'rovlar yig'indisi (bitta so'rov). «Avans» turi limit
        byudjetiga kirmagani uchun bu yerda ham hisoblanmaydi (haftalik jamida ko'rinadi)."""
        agg = WeeklyRequestItem.objects.filter(
            request__project=self,
            request__status=WeeklyRequest.Status.APPROVED,
        ).exclude(kind="avans").aggregate(s=Sum(_LINE_TOTAL))
        return agg["s"] or Decimal("0.00")

    def qolgan(self):
        return self.budget_total - self.sarflangan()

    def moliya_by_kind(self):
        """Turlar bo'yicha HAQIQATDA berilgan (buxgalter to'lovlari) — fakt."""
        d = {"material": Decimal("0.00"), "labor": Decimal("0.00"),
             "machinery": Decimal("0.00"), "other": Decimal("0.00")}
        rows = (Moliya.objects.filter(item__request__project=self, bekor=False,
                                      item__request__status=WeeklyRequest.Status.APPROVED)
                .values("item__kind").annotate(s=Sum("summa")))
        for r in rows:
            d[r["item__kind"]] = (d.get(r["item__kind"]) or Decimal("0.00")) + (r["s"] or Decimal("0.00"))
        # Limit qatoriga yozilgan to'lovlar (moliyalashtirish jadvali)
        rows = (Moliya.objects.filter(limit_item__project=self, bekor=False)
                .values("limit_item__kind").annotate(s=Sum("summa")))
        for r in rows:
            d[r["limit_item__kind"]] = (d.get(r["limit_item__kind"]) or Decimal("0.00")) + (r["s"] or Decimal("0.00"))
        return d

    def berildi(self):
        """Haqiqatda berilgan jami (fakt)."""
        return sum(self.moliya_by_kind().values(), Decimal("0.00"))

    def qarz(self):
        """Tasdiqlangan − berilgan = berilmagan qarz."""
        q = self.sarflangan() - self.berildi()
        return q if q > 0 else Decimal("0.00")

    def limit_holati(self):
        spent = self.sarflangan()
        if self.budget_total <= 0:
            return "limitsiz"
        if spent > self.budget_total:
            return "RUXSAT KERAK"
        if spent / self.budget_total >= Decimal("0.9"):
            return "limitga yaqin"
        return "normal"


class Limit(models.Model):
    """Obyektning RAQAMLANGAN umumiy limiti (1-лимит, 2-лимит, …).

    Bitta obyektda bir nechta limit bo'lishi mumkin — keyingisini faqat ADMIN ochadi
    (2026-10-12). Har limitning o'z qatorlari (LimitItem), o'z haftaliklari va o'z
    tasdiqlash so'rovlari bor; hammasi faol. Obyektning umumiy raqamlari
    (Project.limit_* / sarf) — barcha limitlar yig'indisi."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name="limitlar", verbose_name="Obyekt")
    raqam = models.PositiveIntegerField("Raqami", default=1)
    nom = models.CharField("Nomi (ixtiyoriy)", max_length=120, blank=True)
    # Limit TASDIQLANGACH qulf; admin shu bayroq bilan BIR MARTALIK tahrirga ruxsat beradi
    tahrir_ruxsat = models.BooleanField("Tahririga admin ruxsati", default=False)
    start = models.DateField("Boshlanishi", null=True, blank=True)
    end = models.DateField("Tugashi", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+", verbose_name="Ochdi")
    created_at = models.DateTimeField("Ochilgan", auto_now_add=True, null=True)

    class Meta:
        verbose_name = "Umumiy limit (raqamli)"
        verbose_name_plural = "Umumiy limitlar (raqamli)"
        unique_together = [("project", "raqam")]
        ordering = ["project", "raqam"]

    def __str__(self):
        return f"{self.project.code} · {self.raqam}-лимит"

    def jami(self):
        """Shu limit qatorlari summasi («avans» qatorlari KIRMAYDI)."""
        return (self.items.exclude(kind="avans").aggregate(s=Sum(_LINE_TOTAL))["s"] or Decimal("0.00"))

    def avans_jami(self):
        """«Avans» turidagi qatorlar summasi — alohida ko'rsatiladi."""
        return (self.items.filter(kind="avans").aggregate(s=Sum(_LINE_TOTAL))["s"] or Decimal("0.00"))

    def sarflangan(self):
        """Shu limitga yozilgan TASDIQLANGAN haftaliklar summasi."""
        return (WeeklyRequestItem.objects
                .filter(request__limit=self, request__status=WeeklyRequest.Status.APPROVED)
                .exclude(kind="avans").aggregate(s=Sum(_LINE_TOTAL))["s"] or Decimal("0.00"))


def _joriy_limit(self, yarat=True):
    """Obyektning JORIY (eng oxirgi raqamli) limiti; yo'q bo'lsa 1-лимит yaratiladi."""
    lim = self.limitlar.order_by("-raqam").first()
    if lim is None and yarat:
        lim = Limit.objects.create(project=self, raqam=1,
                                   tahrir_ruxsat=self.limit_tahrir_ruxsat,
                                   start=self.limit_start, end=self.limit_end)
    return lim


Project.joriy_limit = _joriy_limit


class WorkSection(models.Model):
    """Obyekt ichidagi ish bo'limi / bosqich."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="work_sections", verbose_name="Obyekt",
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE,
        related_name="children", verbose_name="Yuqori bo'lim",
    )
    code = models.CharField("Kod", max_length=64, blank=True)
    name = models.CharField("Nomi", max_length=255)

    class Meta:
        verbose_name = "Ish bo'limi"
        verbose_name_plural = "Ish bo'limlari"

    def __str__(self):
        return f"{self.project.code} / {self.name}"


class LimitChangeRequest(models.Model):
    """Mavjud limitni o'zgartirish so'rovi — admin tasdiqlaydi."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Qoralama"              # jadvalda saqlangan, hali zanjirga yuborilmagan
        SNAB = "snab", "Snabjeniye ko'rigida"   # PTO yuborgach snabjeniye narx/tarkibni ko'radi
        PTO2 = "pto2", "PTO xulosasida"          # snabjeniyedan qaytgan — PTO yakuniy xulosa beradi
        DIR = "dir", "Direktor tasdig'ida"
        PROV = "prov", "Proverchik tekshiruvida"   # direktordan keyin, admindan oldin
        ADM = "adm", "Admin tasdig'ida"
        PENDING = "pending", "Kutilmoqda"   # eski yozuvlar uchun (migratsiyada 'dir'ga o'tadi)
        APPROVED = "approved", "Tasdiqlangan"
        REJECTED = "rejected", "Rad etilgan"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="limit_requests", verbose_name="Obyekt",
    )
    limit = models.ForeignKey(
        "Limit", null=True, blank=True, on_delete=models.CASCADE,
        related_name="requests", verbose_name="Raqamli limit",
    )
    old_material = models.DecimalField("Eski material", max_digits=18, decimal_places=2, default=0)
    old_labor = models.DecimalField("Eski ish haqi", max_digits=18, decimal_places=2, default=0)
    old_machinery = models.DecimalField("Eski mashina chasti", max_digits=18, decimal_places=2, default=0)
    old_other = models.DecimalField("Eski ko'zda tutilmagan", max_digits=18, decimal_places=2, default=0)
    new_material = models.DecimalField("Yangi material", max_digits=18, decimal_places=2, default=0)
    new_labor = models.DecimalField("Yangi ish haqi", max_digits=18, decimal_places=2, default=0)
    new_machinery = models.DecimalField("Yangi mashina chasti", max_digits=18, decimal_places=2, default=0)
    new_other = models.DecimalField("Yangi ko'zda tutilmagan", max_digits=18, decimal_places=2, default=0)
    reason = models.CharField("Sabab / izoh", max_length=255, blank=True)
    status = models.CharField(
        "Holati", max_length=16, choices=Status.choices, default=Status.DIR,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="limit_requests_made", verbose_name="So'rovchi (PTO)",
    )
    snab_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="limit_requests_snab", verbose_name="Snabjeniye ko'rdi",
    )
    snab_at = models.DateTimeField("Snabjeniye ko'rgan sana", null=True, blank=True)
    # PTO XULOSASI bosqichini kim o'tkazgan (pto2 -> dir)
    pto2_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="limit_requests_pto2", verbose_name="PTO xulosasini yubordi",
    )
    pto2_at = models.DateTimeField("PTO xulosasi sanasi", null=True, blank=True)
    # So'rovni OXIRGI marta kim tahrirlagan (istalgan bosqichda)
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="limit_requests_edited", verbose_name="Oxirgi tahrir",
    )
    edited_at = models.DateTimeField("Oxirgi tahrir sanasi", null=True, blank=True)
    director_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="limit_requests_dir", verbose_name="Direktor tasdiqladi",
    )
    director_at = models.DateTimeField("Direktor tasdig'i sanasi", null=True, blank=True)
    prov_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="limit_requests_prov", verbose_name="Proverchik tekshirdi",
    )
    prov_at = models.DateTimeField("Proverchik tekshiruvi sanasi", null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="limit_requests_decided", verbose_name="Qaror qilgan (admin)",
    )
    decision_note = models.CharField("Rad etish sababi", max_length=500, blank=True)
    pto_notified = models.BooleanField("PTO xabarni ko'rdi", default=True)
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)
    decided_at = models.DateTimeField("Qaror sanasi", null=True, blank=True)

    class Meta:
        verbose_name = "Limit o'zgartirish so'rovi"
        verbose_name_plural = "Limit o'zgartirish so'rovlari"
        ordering = ["-created_at"]

    @property
    def old_total(self):
        return self.old_material + self.old_labor + self.old_machinery + self.old_other

    @property
    def new_total(self):
        return self.new_material + self.new_labor + self.new_machinery + self.new_other

    def __str__(self):
        return f"{self.project.code}: {self.old_total} → {self.new_total} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if self.limit_id is None and self.project_id:
            self.limit = self.project.joriy_limit()
        super().save(*args, **kwargs)

    def approve(self, admin_user):
        from django.utils import timezone
        p = self.project
        lim = self.limit or p.joriy_limit()
        # Limitni qo'llash + statusni yozish BITTA tranzaksiyada — yarim holat qolmasin
        with transaction.atomic():
            # Qulf + status qayta tekshiruvi: ikki admin bir vaqtda bossa faqat bittasi o'tadi
            joriy = (LimitChangeRequest.objects.select_for_update()
                     .values_list("status", flat=True).get(pk=self.pk))
            if joriy != self.Status.ADM:
                return
            # Qulfdan KEYIN o'qiladi — bir vaqtdagi tahrir bilan farq qolmasin
            proposed = list(self.proposed_items.all())
            if proposed:
                # «Limit ichi» tarkibi bo'yicha so'rov — taklif etilgan tarkibni qo'llash
                # (o'chirib-qayta yaratmaymiz — o'zgarmagan qatorlarning sanasi saqlansin)
                qatorlar = [
                    {"kind": it.kind, "name": it.name, "unit": it.unit,
                     "quantity": it.quantity, "unit_price": it.unit_price, "note": it.note,
                     "bolim": it.bolim, "masul": it.masul, "asl_id": it.asl_id,
                     "boshlangich_qty": it.boshlangich_qty, "boshlangich_sum": it.boshlangich_sum}
                    for it in proposed
                ]
                if any(q["asl_id"] for q in qatorlar):
                    limit_qatorlarini_qolla(lim, qatorlar)    # jadvaldan: ID bo'yicha
                else:
                    for q in qatorlar:
                        q.pop("asl_id")
                    sync_limit_items(lim, qatorlar)           # eski so'rovlar: nom bo'yicha
                p.recompute_limits()
            else:
                # Eski usul — faqat raqamli limit
                p.limit_material = self.new_material
                p.limit_labor = self.new_labor
                p.limit_machinery = self.new_machinery
                p.limit_other = self.new_other
                p.save(update_fields=["limit_material", "limit_labor", "limit_machinery", "limit_other"])
            self.status = self.Status.APPROVED
            self.decided_by = admin_user
            self.decided_at = timezone.now()
            self.pto_notified = False   # PTO'ga "Tasdiqlandi" xabari chiqsin
            self.save(update_fields=["status", "decided_by", "decided_at", "pto_notified"])

    def reject(self, admin_user, izoh=""):
        from django.utils import timezone
        self.status = self.Status.REJECTED
        self.decided_by = admin_user
        self.decided_at = timezone.now()
        self.decision_note = (izoh or "").strip()[:500]
        self.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])


class WeeklyRequest(models.Model):
    """Haftalik so'rov (zayavka) — umumiy limitdan ayiriladi."""

    class Status(models.TextChoices):
        # Zanjir — UMUMIY LIMIT bilan bir xil (2026-10-12): PTO → snab → PTO xulosasi →
        # direktor → proverchik → admin. Xodimi yo'q bosqich (snab/prov) o'tkazib yuboriladi.
        DRAFT = "draft", "Qoralama"
        SNAB = "snab", "Snabjeniye ko'rigida"
        PTO2 = "pto2", "PTO xulosasida"
        DIR = "dir", "Direktor tasdig'ida"
        PROV = "prov", "Proverchik tekshiruvida"
        SUBMITTED = "submitted", "Admin tasdig'ida"   # direktor/proverchikdan o'tgan
        APPROVED = "approved", "Tasdiqlangan"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="weekly_requests", verbose_name="Obyekt",
    )
    limit = models.ForeignKey(
        "Limit", null=True, blank=True, on_delete=models.CASCADE,
        related_name="weekly_requests", verbose_name="Raqamli limit",
    )
    week_start = models.DateField("Hafta boshi")
    week_end = models.DateField("Hafta oxiri")
    number = models.CharField("Hujjat raqami", max_length=64, blank=True)
    status = models.CharField(
        "Holati", max_length=16, choices=Status.choices, default=Status.DRAFT,
        help_text="Faqat «Tasdiqlangan» so'rovlar limitdan ayiriladi.",
    )
    note = models.CharField("Izoh", max_length=255, blank=True)
    created_at = models.DateTimeField("Kiritilgan sana", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_created", verbose_name="Kiritdi (PTO)",
    )
    snab_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_snab", verbose_name="Snabjeniye ko'rdi",
    )
    snab_at = models.DateTimeField("Snabjeniye ko'rgan sana", null=True, blank=True)
    pto2_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_pto2", verbose_name="PTO xulosasini yubordi",
    )
    pto2_at = models.DateTimeField("PTO xulosasi sanasi", null=True, blank=True)
    director_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_director", verbose_name="Direktor tasdiqladi",
    )
    director_at = models.DateTimeField("Direktor tasdig'i sanasi", null=True, blank=True)
    prov_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_prov", verbose_name="Proverchik tekshirdi",
    )
    prov_at = models.DateTimeField("Proverchik tekshiruvi sanasi", null=True, blank=True)
    # ADMIN bergan BIR MARTALIK ruxsat: yopiq (yuborilgan/tasdiqlangan) haftani jadvalda
    # qayta tahrirlash — har hafta uchun alohida; saqlangach o'zi yopiladi
    tahrir_ruxsat = models.BooleanField("Tahrirga admin ruxsati", default=False)
    # So'rovni OXIRGI marta kim tahrirlagan (admin/PTO)
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_edited", verbose_name="Oxirgi tahrir",
    )
    edited_at = models.DateTimeField("Oxirgi tahrir sanasi", null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_approved", verbose_name="Tasdiqladi (admin)",
    )
    approved_at = models.DateTimeField("Tasdiqlangan sana", null=True, blank=True)
    reject_note = models.CharField("Rad etish sababi", max_length=500, blank=True)
    # Tasdiqlangach PTO'ga bir martalik "Tasdiqlandi" xabari ko'rsatiladi (False = hali ko'rmagan)
    pto_notified = models.BooleanField("PTO xabarni ko'rdi", default=True)

    class Meta:
        verbose_name = "Haftalik so'rov"
        verbose_name_plural = "Haftalik so'rovlar"
        ordering = ["-week_start", "-id"]

    def __str__(self):
        return f"{self.project.code}: {self.week_start} — {self.week_end}"

    def save(self, *args, **kwargs):
        if self.limit_id is None and self.project_id:
            self.limit = self.project.joriy_limit()
        super().save(*args, **kwargs)

    @property
    def jami(self):
        agg = self.items.aggregate(s=Sum(_LINE_TOTAL))
        return agg["s"] or Decimal("0.00")


class WeeklyRequestItem(models.Model):
    """Haftalik so'rov qatori — material yoki ish haqi."""

    class Kind(models.TextChoices):
        MATERIAL = "material", "Material"
        LABOR = "labor", "Ish haqi"
        MACHINERY = "machinery", "Mashina chasti"
        OTHER = "other", "Ko'zda tutilmagan xarajatlar"
        AVANS = "avans", "Avans"   # 2026-10-23: umumiy limit jamiga KIRMAYDI, haftalikda hisoblanadi

    request = models.ForeignKey(
        WeeklyRequest, on_delete=models.CASCADE,
        related_name="items", verbose_name="So'rov",
    )
    kind = models.CharField(
        "Turi", max_length=16, choices=Kind.choices, default=Kind.MATERIAL,
    )
    work_section = models.ForeignKey(
        WorkSection, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_items", verbose_name="Ish bo'limi",
    )
    name = models.CharField("Material / ish nomi", max_length=255)
    unit = models.CharField("O'lchov birligi", max_length=32, blank=True)
    quantity = models.DecimalField("Miqdor", **QTY)
    unit_price = models.DecimalField("Narxi (1 birlik)", **MONEY)
    note = models.CharField("Primechaniye (qator izohi)", max_length=500, blank=True)
    # Qaysi BO'LIM uchun (umumiy limitdagi bo'lim bilan bog'lanadi) —
    # bir xil material har bo'limda o'z qoldig'i bilan yuriladi
    bolim = models.CharField("Bo'lim", max_length=200, blank=True)
    created_at = models.DateTimeField("Kiritilgan sana", auto_now_add=True, null=True)
    # AVANS (kind="avans", 2026-10-23): ustaga oldindan berilgan pul. Umumiy limit jamiga
    # KIRMAYDI, haftalik jamiga KIRADI. Bog'lanish: avans_qator — aniq limit qatoriga
    # (masalan ish haqi), bo'sh bo'lsa — butun BLOKka (bolim + mas'ul, «materiali ichida» usta).
    # Nazorat: bajarilgan + so'ralgan + avans ≤ qator/blok limiti (dastur o'zi ayiradi).
    avans_qator = models.ForeignKey(
        "LimitItem", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="avanslar", verbose_name="Avans qaysi qatorga",
    )

    class Meta:
        verbose_name = "So'rov qatori"
        verbose_name_plural = "So'rov qatorlari"

    def __str__(self):
        return f"{self.name} × {self.quantity}"

    @property
    def total(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    # ---- Moliyalashtirish (fakt) ----
    @property
    def berildi(self):
        """Buxgalter haqiqatda bergan summa (barcha to'lovlar yig'indisi)."""
        s = Decimal("0")
        for m in self.moliyalar.all():
            if not m.bekor:
                s += m.summa
        return s.quantize(Decimal("0.01"))

    @property
    def berildi_miqdor(self):
        s = Decimal("0")
        for m in self.moliyalar.all():
            if m.miqdor is not None and not m.bekor:
                s += m.miqdor
        return s

    @property
    def qarz(self):
        """Tasdiqlangan − berilgan (berilmagan qarz)."""
        q = self.total - self.berildi
        return q if q > 0 else Decimal("0.00")


class LimitItem(models.Model):
    """Umumiy limit ichi — kategoriya bo'yicha tarkib (material/ish haqi/mashina)."""

    class Kind(models.TextChoices):
        MATERIAL = "material", "Material"
        LABOR = "labor", "Ish haqi"
        MACHINERY = "machinery", "Mashina chasti"
        OTHER = "other", "Ko'zda tutilmagan xarajatlar"
        AVANS = "avans", "Avans"   # 2026-10-23: umumiy limit jamiga KIRMAYDI, haftalikda hisoblanadi

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="limit_items", verbose_name="Obyekt",
    )
    limit = models.ForeignKey(
        "Limit", null=True, blank=True, on_delete=models.CASCADE,
        related_name="items", verbose_name="Raqamli limit",
    )
    kind = models.CharField("Turi", max_length=16, choices=Kind.choices, default=Kind.MATERIAL)
    name = models.CharField("Nomi", max_length=255)
    unit = models.CharField("O'lchov birligi", max_length=32, blank=True)
    quantity = models.DecimalField("Miqdor", **QTY)
    unit_price = models.DecimalField("Narxi (1 birlik)", **MONEY)
    note = models.CharField("Primechaniye (qator izohi)", max_length=500, blank=True)
    # Obyekt ichidagi ish bo'limi (masalan «Ayvon 20m») — qatorlar bo'lim
    # bo'yicha ketma-ket guruhlanadi; mas'ul shaxs bo'limga yozib qo'yiladi
    bolim = models.CharField("Bo'lim", max_length=200, blank=True)
    masul = models.CharField("Mas'ul shaxs", max_length=120, blank=True)
    # Limit kiritilguncha ALLAQACHON bajarilgan/olingan qism (PTO qo'lda kiritadi);
    # «Бажарилган» = shu + tasdiqlangan haftaliklar
    boshlangich_qty = models.DecimalField("Limitgacha bajarilgan (miqdor)", default=0, **QTY)
    boshlangich_sum = models.DecimalField("Limitgacha bajarilgan (summa)", default=0, **MONEY)
    # Moliyalashtirish jadvalidagi «Izoh» ustuni — buxgalter yozadi (to'lov izohidan alohida)
    moliya_izoh = models.CharField("Moliya izohi (buxgalter)", max_length=300, blank=True)
    # Admin QATORGA alohida tahrir ruxsati (2026-10-24): tasdiqlangan (qulf) limitda faqat shu qator
    # tahrirlanadi; so'rov yuborilgach / qo'llangach o'zi yopiladi (Limit.tahrir_ruxsat kabi bir martalik)
    tahrir_ruxsat = models.BooleanField("Qatorni tahrirlashga ruxsat (admin)", default=False)
    created_at = models.DateTimeField("Qo'shilgan sana", auto_now_add=True, null=True)
    updated_at = models.DateTimeField("O'zgartirilgan sana", auto_now=True, null=True)

    class Meta:
        verbose_name = "Limit tarkibi"
        verbose_name_plural = "Limit tarkibi (ichi)"

    def __str__(self):
        return f"{self.name} x {self.quantity}"

    @property
    def total(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        if self.limit_id is None and self.project_id:
            self.limit = self.project.joriy_limit()
        super().save(*args, **kwargs)


class GrafikRow(models.Model):
    """Ish grafigi qatori (veb) — namunadagi «график работа» kabi."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="grafik_rows", verbose_name="Obyekt",
    )
    tartib = models.PositiveIntegerField("Tartib", default=0)
    name = models.CharField("Наименование (ish nomi)", max_length=500, blank=True)
    unit = models.CharField("Ед.изм", max_length=32, blank=True)
    qty = models.DecimalField("Кол-во", max_digits=18, decimal_places=3, default=0)
    plan = models.DecimalField("Кун план", max_digits=18, decimal_places=3, default=0)
    # Ish muddati: boshlanish sanasi (bo'sh = grafik boshi) + necha kun.
    # Shu davr kataklari jadvalda och ko'k rangda belgilanadi.
    muddat_boshi = models.DateField("Ish boshlanishi", null=True, blank=True)
    muddat = models.PositiveIntegerField("Muddat (kun)", default=0)
    # Muddat ichida BELGILANMAYDIGAN kunlar (masalan dam olish) — ustun indekslari
    srok_off = models.JSONField("Srokdan chiqarilgan kunlar", default=list, blank=True)
    # Muddatdan TASHQARIDA qo'shimcha belgilangan kunlar (3 marta bosish bilan)
    srok_on = models.JSONField("Qo'shimcha srok kunlari", default=list, blank=True)
    responsible = models.CharField("Маъсул шахс", max_length=255, blank=True)
    note = models.CharField("Иш прагнози / izoh", max_length=500, blank=True)
    # Izoh TARIXI: izoh o'zgartirilganda eskisi shu yerga tushadi
    # [{"s": "2026-08-14", "t": "eski matn"}, ...] — eng eskisi birinchi
    note_tarix = models.JSONField("Izoh tarixi", default=list, blank=True)
    days = models.JSONField("Kunlik bajarilgan", default=dict, blank=True)

    class Meta:
        verbose_name = "Grafik qatori"
        verbose_name_plural = "Grafik qatorlari"
        ordering = ["tartib", "id"]

    def __str__(self):
        return f"{self.project.code}: {self.name[:40]}"

    @property
    def bajarilgan(self):
        s = Decimal("0")
        for v in (self.days or {}).values():
            try:
                s += Decimal(str(v))
            except (TypeError, ArithmeticError):
                pass
        return s

    @property
    def foiz(self):
        b = self.bajarilgan
        return round(float(b) / float(self.qty) * 100) if self.qty else 0

    @property
    def qoldiq(self):
        return self.qty - self.bajarilgan


class GrafikTasdiq(models.Model):
    """Ish grafigini TASDIQLASH so'rovi — limit zanjiriga o'xshash:
    PTO yuboradi -> Direktor (yoki asosiy admin) -> Admin -> Tasdiqlangan."""

    class Status(models.TextChoices):
        DIR = "dir", "Direktor tasdig'ida"
        ADM = "adm", "Admin tasdig'ida"
        APPROVED = "approved", "Tasdiqlangan"
        REJECTED = "rejected", "Rad etilgan"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="grafik_tasdiqlar", verbose_name="Obyekt",
    )
    status = models.CharField("Holati", max_length=16, choices=Status.choices,
                              default=Status.DIR)
    yubordi = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="grafik_tasdiq_yuborgan", verbose_name="Yubordi (PTO)",
    )
    created_at = models.DateTimeField("Yuborilgan sana", auto_now_add=True)
    director_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="grafik_tasdiq_dir", verbose_name="Direktor tasdiqladi",
    )
    director_at = models.DateTimeField("Direktor tasdig'i sanasi", null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="grafik_tasdiq_adm", verbose_name="Admin tasdiqladi",
    )
    decided_at = models.DateTimeField("Admin qarori sanasi", null=True, blank=True)
    decision_note = models.CharField("Rad etish sababi", max_length=500, blank=True)

    class Meta:
        verbose_name = "Grafik tasdig'i"
        verbose_name_plural = "Grafik tasdiqlari"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project.code} grafik — {self.get_status_display()}"


def hafta_qatorlarini_qayta_nomla(lim, eski, yangi):
    """Limit qatori qayta nomlansa/boshqa bo'limga o'tsa — unga bog'liq HAFTALIK
    qatorlar ham ergashadi (bog'lanish nom+bo'lim orqali; aks holda bajarilgan/qolgan
    tarixi uziladi). `eski`/`yangi` — {name, bolim, unit, kind}."""
    if all(eski[k] == yangi[k] for k in ("name", "bolim", "unit", "kind")):
        return 0
    return (WeeklyRequestItem.objects
            .filter(request__limit=lim, name=eski["name"], bolim=eski["bolim"], kind=eski["kind"])
            .update(name=yangi["name"], bolim=yangi["bolim"],
                    unit=yangi["unit"], kind=yangi["kind"]))


def tolovli_limit_qatori(li):
    """Qatorga faol (bekor qilinmagan) to'lov yozilganmi — bunday qator O'CHIRILMAYDI."""
    return li.moliyalar.filter(bekor=False).exists()


def limit_qatorlarini_qolla(lim, items):
    """Limit jadvalidan kelgan TO'LIQ ro'yxatni qo'llaydi: `asl_id` bor qator — o'sha
    LimitItem o'rnida yangilanadi (sana/ID saqlanadi, haftalik tarixi ergashadi),
    `asl_id` yo'q — yangi qator, ro'yxatda yo'q mavjud qatorlar o'chiriladi."""
    mavjud = {li.pk: li for li in lim.items.all()}
    MAYDON = ("kind", "name", "unit", "quantity", "unit_price", "note", "bolim", "masul",
              "boshlangich_qty", "boshlangich_sum")
    qoldi = set()
    for it in items:
        li = mavjud.get(it.get("asl_id"))
        if li is None:
            LimitItem.objects.create(project=lim.project, limit=lim, **{k: it[k] for k in MAYDON})
            continue
        qoldi.add(li.pk)
        if any(getattr(li, k) != it[k] for k in MAYDON):
            eski = {"name": li.name, "bolim": li.bolim, "unit": li.unit, "kind": li.kind}
            for k in MAYDON:
                setattr(li, k, it[k])
            li.save()          # auto_now -> updated_at yangilanadi
            hafta_qatorlarini_qayta_nomla(lim, eski, {k: it[k] for k in ("name", "bolim", "unit", "kind")})
    for pk, li in mavjud.items():
        if pk not in qoldi:
            if tolovli_limit_qatori(li):
                continue      # to'lovli qator saqlanib qoladi (jurnal yo'qolmasin)
            li.delete()


def sync_limit_items(lim, items):
    """Limit tarkibini yangilaydi — o'zgarmagan qatorlarning SANASI saqlanib qoladi.

    Avval har saqlashda hamma qator o'chirilib qayta yaratilardi; u holda
    «qo'shilgan sana» har safar yangilanib, tarix yo'qolardi.
    Bir xil nomli bir necha qator BO'LISHI MUMKIN (masalan «Гранит» ikki xil
    narxda) — tartib bo'yicha juftlanadi, ortiqcha takror yo'qolmaydi.
    `items` — [{kind, name, unit, quantity, unit_price, note}, ...]
    """
    from collections import defaultdict, deque

    mavjud = defaultdict(deque)
    for li in lim.items.all().order_by("id"):
        mavjud[(li.kind, (li.name or "").strip().lower())].append(li)

    for it in items:
        kalit = (it["kind"], (it["name"] or "").strip().lower())
        navbat = mavjud.get(kalit)
        if not navbat:
            LimitItem.objects.create(project=lim.project, limit=lim, **it)
            continue
        li = navbat.popleft()
        ozgardi = (
            li.unit != it.get("unit", "")
            or li.quantity != it["quantity"]
            or li.unit_price != it["unit_price"]
            or li.note != it.get("note", "")
            or li.bolim != it.get("bolim", "")
            or li.masul != it.get("masul", "")
            or li.boshlangich_qty != it.get("boshlangich_qty", 0)
            or li.boshlangich_sum != it.get("boshlangich_sum", 0)
        )
        if ozgardi:
            li.unit = it.get("unit", "")
            li.quantity = it["quantity"]
            li.unit_price = it["unit_price"]
            li.note = it.get("note", "")
            li.bolim = it.get("bolim", "")
            li.masul = it.get("masul", "")
            li.boshlangich_qty = it.get("boshlangich_qty", 0)
            li.boshlangich_sum = it.get("boshlangich_sum", 0)
            li.save()          # auto_now -> updated_at yangilanadi
    # ro'yxatda qolmaganlarni o'chiramiz (to'lovli qatorlar saqlanadi)
    for navbat in mavjud.values():
        for li in navbat:
            if tolovli_limit_qatori(li):
                continue
            li.delete()


class LimitChangeItem(models.Model):
    """Limit o'zgartirish so'rovidagi taklif etilgan tarkib (tasdiqlangach qo'llanadi)."""

    request = models.ForeignKey(
        LimitChangeRequest, on_delete=models.CASCADE,
        related_name="proposed_items", verbose_name="So'rov",
    )
    kind = models.CharField("Turi", max_length=16, choices=LimitItem.Kind.choices, default=LimitItem.Kind.MATERIAL)
    name = models.CharField("Nomi", max_length=255)
    unit = models.CharField("O'lchov birligi", max_length=32, blank=True)
    quantity = models.DecimalField("Miqdor", **QTY)
    unit_price = models.DecimalField("Narxi (1 birlik)", **MONEY)
    note = models.CharField("Primechaniye (qator izohi)", max_length=500, blank=True)
    bolim = models.CharField("Bo'lim", max_length=200, blank=True)
    masul = models.CharField("Mas'ul shaxs", max_length=120, blank=True)
    boshlangich_qty = models.DecimalField("Limitgacha bajarilgan (miqdor)", default=0, **QTY)
    boshlangich_sum = models.DecimalField("Limitgacha bajarilgan (summa)", default=0, **MONEY)
    # Qaysi MAVJUD limit qatorining o'zgartirilgan nusxasi (bo'sh — yangi qator).
    # Shu orqali qayta nomlangan qator tasdiqlanganda o'rnida yangilanadi, haftalik
    # tarixi (bajarilgan/qolgan) uzilmaydi.
    asl = models.ForeignKey(
        LimitItem, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="takliflar", verbose_name="Asl qator",
    )
    created_at = models.DateTimeField("Kiritilgan sana", auto_now_add=True, null=True)

    class Meta:
        verbose_name = "Taklif tarkibi"
        verbose_name_plural = "Taklif tarkibi"

    @property
    def total(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))


class UserProfile(models.Model):
    """Foydalanuvchini bitta firmaga biriktiradi.

    Superuser (admin) — hamma firmani ko'radi (profil shart emas).
    Direktor/PTO — faqat o'z firmasining ma'lumotini ko'radi.
    Firma biriktirilmagan oddiy user — hech narsa ko'rmaydi (xavfsiz default).
    """

    class Role(models.TextChoices):
        NONE = "", "— (lavozim yo'q)"
        DIRECTOR = "director", "Direktor"
        PTO = "pto", "PTO"
        PRORAB = "prorab", "Prorab"
        SNAB = "snab", "Snabjeniye"
        BUX = "bux", "Buxgalter"   # firma bo'yicha FAQAT KO'RISH + Excel
        PROV = "prov", "Proverchik (tekshiruvchi)"   # limit zanjiri: direktordan keyin, admindan oldin

    # Rol -> Django guruh nomi (is_pto/is_director/is_prorab guruh bo'yicha ishlaydi)
    ROLE_GROUP = {"director": "Direktor", "pto": "PTO", "prorab": "Prorab",
                  "snab": "Snabjeniye", "bux": "Buxgalter", "prov": "Proverchik"}

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="profile", verbose_name="Foydalanuvchi",
    )
    role = models.CharField("Lavozim", max_length=16, choices=Role.choices, blank=True)
    firma = models.ForeignKey(
        Firma, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="users", verbose_name="Firma (Direktor uchun)",
    )
    projects = models.ManyToManyField(
        Project, blank=True, related_name="assigned_users",
        verbose_name="Biriktirilgan obyektlar (PTO/Prorab uchun)",
    )
    # ASOSIY proverchik — tekshirilgan so'rovni adminga o'tkazadi/qaytaradi; qolgan
    # proverchiklar faqat tahrirlab saqlaydi (2026-10-12). Hech kim belgilanmasa — hammasi o'tkazadi.
    prov_asosiy = models.BooleanField("Asosiy proverchik", default=False,
                                      help_text="Faqat proverchik roli uchun: adminga o'tkazish huquqi.")
    telegram_chat_id = models.CharField(
        "Telegram chat ID (2FA)", max_length=32, blank=True, default="",
        help_text="Bo'sh bo'lsa — oddiy login/parol bilan kiradi. "
                  "To'ldirilgan bo'lsa — kirishda shu chatga tasdiqlash kodi boradi. "
                  "Foydalanuvchi botga /start yozib login/parol bilan o'zi bog'lanadi.",
    )

    class Meta:
        verbose_name = "Foydalanuvchi biriktirish"
        verbose_name_plural = "Foydalanuvchi biriktirish"

    def __str__(self):
        return f"{self.user} — {self.get_role_display() or 'lavozimsiz'}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.sync_groups()

    def sync_groups(self):
        """Tanlangan lavozimga qarab userni to'g'ri Django guruhiga qo'yadi
        (boshqa rol-guruhlaridan chiqaradi)."""
        from django.contrib.auth.models import Group
        for gname in self.ROLE_GROUP.values():
            g = Group.objects.filter(name=gname).first()
            if g:
                self.user.groups.remove(g)
        target = self.ROLE_GROUP.get(self.role)
        if target:
            g, _ = Group.objects.get_or_create(name=target)
            self.user.groups.add(g)


class TelegramBindState(models.Model):
    """Bot bilan ro'yxatdan o'tish suhbati holati (har bir chat uchun).

    /start -> login so'raladi -> parol so'raladi -> tekshiriladi ->
    chat_id UserProfile.telegram_chat_id ga yoziladi.
    """

    chat_id = models.CharField("Chat ID", max_length=32, unique=True)
    step = models.CharField("Bosqich", max_length=16, blank=True)  # '' | 'login' | 'parol'
    login_tmp = models.CharField("Vaqtinchalik login", max_length=150, blank=True)
    fails = models.PositiveSmallIntegerField("Xato urinishlar", default=0)
    blocked_until = models.DateTimeField("Bloklangan (gacha)", null=True, blank=True)
    # Login/parol to'g'ri chiqqach — ADMIN tasdig'i kutiladi (xavfsizlik):
    # admin Telegramda ✅/❌ bosadi, shundan keyingina chat profilga bog'lanadi
    pending_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="tg_bind_pending", verbose_name="Admin tasdig'ini kutayotgan foydalanuvchi",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Telegram bog'lash holati"
        verbose_name_plural = "Telegram bog'lash holatlari"

    def __str__(self):
        return f"{self.chat_id} ({self.step or 'boshlanmagan'})"


class Moliya(models.Model):
    """MOLIYALASHTIRISH (fakt): buxgalter tasdiqlangan haftalik qatoriga
    HAQIQATDA berilgan pulni yozadi. Bir qatorga bir necha to'lov bo'lishi mumkin
    (bugun 60%, kelasi hafta qolgani). Yozuv o'chirilmaydi — jurnal."""

    item = models.ForeignKey(
        "WeeklyRequestItem", on_delete=models.CASCADE, null=True, blank=True,
        related_name="moliyalar", verbose_name="Haftalik qatori",
    )
    # 2026-10-12: moliyalashtirish UMUMIY LIMIT jadvali bo'yicha yuritiladi —
    # to'lov limit qatoriga yoziladi (eski haftalik to'lovlar item orqali saqlanib qoladi
    # va mos limit qatoriga qo'shib hisoblanadi)
    # PROTECT (2026-10-22 audit): to'lov yozilgan limit qatori o'chirilsa pul jurnali
    # izsiz yo'qolmasin — o'chirish urinishi xato beradi, ko'rinishlar oldindan tekshiradi
    limit_item = models.ForeignKey(
        "LimitItem", on_delete=models.PROTECT, null=True, blank=True,
        related_name="moliyalar", verbose_name="Limit qatori",
    )
    summa = models.DecimalField("Berilgan summa", **MONEY)
    miqdor = models.DecimalField("Berilgan miqdor", null=True, blank=True, **QTY)
    USUL = (("kassa", "Kassa"), ("karta", "Karta"), ("avans", "Avans"))
    usul = models.CharField("To'lov usuli", max_length=10, choices=USUL, blank=True)
    izoh = models.CharField("Izoh (kassa/karta/avans...)", max_length=300, blank=True)
    sana = models.DateField("To'lov sanasi")
    yozdi = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="moliya_yozgan", verbose_name="Yozdi (buxgalter)",
    )
    created_at = models.DateTimeField("Yozilgan vaqt", auto_now_add=True)
    # Admin ruxsati bilan TAHRIR (2026-10-12): asl summa saqlanadi; xato to'lov o'chirilmaydi —
    # BEKOR qilinadi (jurnalda qoladi, hisobga olinmaydi)
    asl_summa = models.DecimalField("Asl summa (tahrirgacha)", null=True, blank=True, **MONEY)
    tahrir_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="moliya_tahrirlagan", verbose_name="Tahrirladi")
    tahrir_at = models.DateTimeField("Tahrir vaqti", null=True, blank=True)
    bekor = models.BooleanField("Bekor qilingan", default=False)
    bekor_sabab = models.CharField("Bekor sababi", max_length=300, blank=True)
    bekor_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="moliya_bekor_qilgan", verbose_name="Bekor qildi")
    bekor_at = models.DateTimeField("Bekor vaqti", null=True, blank=True)

    class Meta:
        verbose_name = "Moliyalashtirish (to'lov)"
        verbose_name_plural = "Moliyalashtirish (to'lovlar)"
        ordering = ["-sana", "-id"]

    @property
    def qator(self):
        """To'lov yozilgan qator: limit qatori yoki (eski) haftalik qatori."""
        return self.limit_item if self.limit_item_id else self.item

    @property
    def project(self):
        q = self.qator
        if q is None:
            return None
        return q.project if self.limit_item_id else q.request.project

    def __str__(self):
        q = self.qator
        return f"{q.name if q else '—'}: {self.summa} ({self.sana:%d.%m.%Y})"


class MoliyaTahrirSorov(models.Model):
    """Buxgalter yozilgan to'lovlarni TAHRIRLASH uchun admindan ruxsat so'raydi —
    har POZITSIYA (limit qatori yoki haftalik qatori) uchun alohida. Admin ruxsat bersa
    buxgalter BIR MARTA tahrirlaydi (summa/sana/usul/izoh, xato to'lovni bekor qilish),
    so'ng ruxsat yopiladi."""

    class Status(models.TextChoices):
        SOROV = "sorov", "Ruxsat so'ralgan"
        RUXSAT = "ruxsat", "Ruxsat berilgan"
        RAD = "rad", "Rad etilgan"
        BAJARILDI = "bajarildi", "Tahrirlandi (yopiq)"

    limit_item = models.ForeignKey("LimitItem", null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="tahrir_sorovlari", verbose_name="Limit qatori")
    item = models.ForeignKey("WeeklyRequestItem", null=True, blank=True, on_delete=models.CASCADE,
                             related_name="tahrir_sorovlari", verbose_name="Haftalik qatori")
    soradi = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                               related_name="moliya_tahrir_soragan", verbose_name="So'radi (buxgalter)")
    sabab = models.CharField("Sabab", max_length=300)
    status = models.CharField("Holat", max_length=12, choices=Status.choices, default=Status.SOROV)
    created_at = models.DateTimeField("So'ralgan vaqt", auto_now_add=True)
    javob_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="moliya_tahrir_javob", verbose_name="Javob berdi (admin)")
    javob_at = models.DateTimeField("Javob vaqti", null=True, blank=True)
    javob_izoh = models.CharField("Admin izohi", max_length=300, blank=True)
    bajarildi_at = models.DateTimeField("Tahrirlangan vaqt", null=True, blank=True)

    class Meta:
        verbose_name = "Moliya tahrir so'rovi"
        verbose_name_plural = "Moliya tahrir so'rovlari"
        ordering = ["-created_at"]

    @property
    def qator(self):
        return self.limit_item if self.limit_item_id else self.item

    @property
    def project(self):
        q = self.qator
        if q is None:
            return None
        return q.project if self.limit_item_id else q.request.project

    @property
    def ochiq(self):
        return self.status in (self.Status.SOROV, self.Status.RUXSAT)

    def __str__(self):
        q = self.qator
        return f"{q.name if q else '—'}: {self.get_status_display()}"


class Xabar(models.Model):
    """ADMIN -> xodim(lar)ga xabar. Xodim tizimga kirganda oynasi tepasida
    ko'rinadi, «O'qidim» bosguncha turadi. Telegram bog'langan bo'lsa botga ham ketadi."""

    class Muhimlik(models.TextChoices):
        ODDIY = "oddiy", "Oddiy"
        MUHIM = "muhim", "Muhim"
        SHOSHILINCH = "shoshilinch", "Shoshilinch"

    matn = models.TextField("Xabar matni", max_length=2000)
    muhimlik = models.CharField("Muhimlik", max_length=12, choices=Muhimlik.choices,
                                default=Muhimlik.ODDIY)
    yubordi = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="xabarlar_yuborgan", verbose_name="Yubordi (admin)",
    )
    # Bo'sh = HAMMAGA (barcha faol xodimlar)
    kimga = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name="xabarlar_olgan", verbose_name="Kimga",
    )
    hammaga = models.BooleanField("Hammaga", default=False)
    created_at = models.DateTimeField("Yuborilgan sana", auto_now_add=True)

    class Meta:
        verbose_name = "Xabar"
        verbose_name_plural = "Xabarlar"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%d.%m.%Y %H:%M} — {self.matn[:40]}"


class XabarOqildi(models.Model):
    """Xodim xabarni o'qigani (O'qidim bosgani)."""
    xabar = models.ForeignKey(Xabar, on_delete=models.CASCADE, related_name="oqilganlar")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="xabar_oqilgan")
    oqilgan_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Xabar o'qildi"
        verbose_name_plural = "Xabar o'qilganlar"
        constraints = [
            models.UniqueConstraint(fields=["xabar", "user"], name="uniq_xabar_user_oqildi"),
        ]


class LimitNavbat(models.Model):
    """Limitni mas'ullarga KETMA-KET (userdan userga) yuborish zanjiri.

    Birinchi mas'ulga xabar boradi; u «O'qidim» bosgach navbat avtomatik
    keyingisiga o'tadi — xuddi limit tasdiqlash zanjiri kabi."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name="limit_navbatlar")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   on_delete=models.SET_NULL, related_name="+")
    izoh = models.CharField("Izoh", max_length=300, blank=True)
    # [{"bolim": "...", "user_id": 5, "username": "nurbek"}, ...] — yuborish tartibida
    items = models.JSONField(default=list)
    idx = models.IntegerField("Joriy bosqich", default=0)
    xabar = models.ForeignKey(Xabar, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, default="active")   # active | done | bekor
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Limit navbati (ketma-ket)"
        verbose_name_plural = "Limit navbatlari"


class MaterialRequest(models.Model):
    """Prorab -> PTO material so'rovi.

    Prorab o'ziga biriktirilgan obyekt uchun kerakli materiallar ro'yxatini
    yuboradi. PTO tahrirlab QABUL yoki RAD qiladi. Qabul qilingani — ro'yxat
    bo'lib saqlanadi (PTO keyin haftalik so'rovga o'zi kiritadi)."""

    class Status(models.TextChoices):
        PENDING = "pending", "PTO ko'rib chiqishida"
        ACCEPTED = "accepted", "Qabul qilingan"
        REJECTED = "rejected", "Rad etilgan"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="material_requests", verbose_name="Obyekt",
    )
    # Obyekt bo'yicha tartib raqami (№1, №2, …) — yaratilganda beriladi
    raqam = models.PositiveIntegerField("Raqami", default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="material_requests_created", verbose_name="Prorab",
    )
    status = models.CharField(
        "Holati", max_length=16, choices=Status.choices, default=Status.PENDING,
    )
    note = models.CharField("Izoh", max_length=500, blank=True)
    created_at = models.DateTimeField("Yuborilgan sana", auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="material_requests_decided", verbose_name="PTO",
    )
    decided_at = models.DateTimeField("Qaror sanasi", null=True, blank=True)
    reject_note = models.CharField("Rad etish sababi", max_length=500, blank=True)

    class Meta:
        verbose_name = "Prorab so'rovi"
        verbose_name_plural = "Prorab so'rovlari"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.project.code} №{self.raqam} — {self.get_status_display()} ({self.created_at:%d.%m.%Y})"

    def save(self, *args, **kwargs):
        if not self.raqam and self.project_id:
            oxirgi = (MaterialRequest.objects.filter(project_id=self.project_id)
                      .order_by("-raqam").values_list("raqam", flat=True).first() or 0)
            self.raqam = oxirgi + 1
        super().save(*args, **kwargs)


class MaterialRequestItem(models.Model):
    """Prorab material so'rovi qatori."""

    class Kind(models.TextChoices):
        MATERIAL = "material", "Material"
        LABOR = "labor", "Ish haqi"
        MACHINERY = "machinery", "Mashina chasti"
        OTHER = "other", "Ko'zda tutilmagan xarajatlar"
        AVANS = "avans", "Avans"   # 2026-10-23: umumiy limit jamiga KIRMAYDI, haftalikda hisoblanadi

    request = models.ForeignKey(
        MaterialRequest, on_delete=models.CASCADE,
        related_name="items", verbose_name="So'rov",
    )
    kind = models.CharField("Turi", max_length=16, choices=Kind.choices, default=Kind.MATERIAL)
    name = models.CharField("Material nomi", max_length=255)
    unit = models.CharField("O'lchov birligi", max_length=32, blank=True)
    quantity = models.DecimalField("Miqdor", **QTY)
    unit_price = models.DecimalField("Narxi (1 birlik, ixtiyoriy)", default=0, **MONEY)
    note = models.CharField("Izoh", max_length=500, blank=True)
    created_at = models.DateTimeField("Kiritilgan sana", auto_now_add=True, null=True)

    class Meta:
        verbose_name = "So'rov qatori"
        verbose_name_plural = "So'rov qatorlari"

    def __str__(self):
        return f"{self.name} — {self.quantity}"

    @property
    def total(self):
        return ((self.quantity or Decimal("0")) * (self.unit_price or Decimal("0"))).quantize(Decimal("0.01"))


def obyekt_yigindilar(projects):
    """Bir nechta obyekt uchun BITTA yurishda: sarflangan (tur bo'yicha ham), berilgan (fakt),
    limitlar soni. N+1 o'rniga 3 ta guruhlangan so'rov. Natija: {project_id: {...}}.
    (2026-10-13 — dashboard/moliya tezligi)"""
    ids = [p.pk if hasattr(p, "pk") else int(p) for p in projects]
    nol = Decimal("0.00")

    def bosh():
        return {"sarf": nol, "kind": {"material": nol, "labor": nol, "machinery": nol, "other": nol},
                "ber": nol, "lim_soni": 0, "avans": nol}
    out = {i: bosh() for i in ids}
    if not ids:
        return out
    for r in (WeeklyRequestItem.objects
              .filter(request__project_id__in=ids, request__status=WeeklyRequest.Status.APPROVED)
              .values("request__project_id", "kind").annotate(s=Sum(_LINE_TOTAL))):
        d = out.setdefault(r["request__project_id"], bosh())
        v = r["s"] or nol
        if r["kind"] == "avans":
            d["avans"] += v           # limit sarfiga kirmaydi, alohida
            continue
        d["kind"][r["kind"]] = d["kind"].get(r["kind"], nol) + v
        d["sarf"] += v
    for r in (Moliya.objects.filter(item__request__project_id__in=ids, bekor=False,
                                    item__request__status=WeeklyRequest.Status.APPROVED)
              .values("item__request__project_id").annotate(s=Sum("summa"))):
        out.setdefault(r["item__request__project_id"], bosh())["ber"] += r["s"] or nol
    for r in (Moliya.objects.filter(limit_item__project_id__in=ids, bekor=False)
              .values("limit_item__project_id").annotate(s=Sum("summa"))):
        out.setdefault(r["limit_item__project_id"], bosh())["ber"] += r["s"] or nol
    for r in Limit.objects.filter(project_id__in=ids).values("project_id").annotate(n=Count("id")):
        out.setdefault(r["project_id"], bosh())["lim_soni"] = r["n"]
    return out
