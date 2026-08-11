"""
projects/models.py
"""
from decimal import Decimal
from django.conf import settings
from django.db import models, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum

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
            d[r["kind"]] = r["s"] or Decimal("0.00")
        return d

    def sarflangan(self):
        """Tasdiqlangan haftalik so'rovlar yig'indisi (bitta so'rov)."""
        agg = WeeklyRequestItem.objects.filter(
            request__project=self,
            request__status=WeeklyRequest.Status.APPROVED,
        ).aggregate(s=Sum(_LINE_TOTAL))
        return agg["s"] or Decimal("0.00")

    def qolgan(self):
        return self.budget_total - self.sarflangan()

    def limit_holati(self):
        spent = self.sarflangan()
        if self.budget_total <= 0:
            return "limitsiz"
        if spent > self.budget_total:
            return "RUXSAT KERAK"
        if spent / self.budget_total >= Decimal("0.9"):
            return "limitga yaqin"
        return "normal"


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
        SNAB = "snab", "Snabjeniye ko'rigida"   # PTO yuborgach snabjeniye narx/tarkibni ko'radi
        PTO2 = "pto2", "PTO xulosasida"          # snabjeniyedan qaytgan — PTO yakuniy xulosa beradi
        DIR = "dir", "Direktor tasdig'ida"
        ADM = "adm", "Admin tasdig'ida"
        PENDING = "pending", "Kutilmoqda"   # eski yozuvlar uchun (migratsiyada 'dir'ga o'tadi)
        APPROVED = "approved", "Tasdiqlangan"
        REJECTED = "rejected", "Rad etilgan"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="limit_requests", verbose_name="Obyekt",
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
    director_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="limit_requests_dir", verbose_name="Direktor tasdiqladi",
    )
    director_at = models.DateTimeField("Direktor tasdig'i sanasi", null=True, blank=True)
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

    def approve(self, admin_user):
        from django.utils import timezone
        p = self.project
        proposed = list(self.proposed_items.all())
        # Limitni qo'llash + statusni yozish BITTA tranzaksiyada — yarim holat qolmasin
        with transaction.atomic():
            if proposed:
                # «Limit ichi» tarkibi bo'yicha so'rov — taklif etilgan tarkibni qo'llash
                # (o'chirib-qayta yaratmaymiz — o'zgarmagan qatorlarning sanasi saqlansin)
                sync_limit_items(p, [
                    {"kind": it.kind, "name": it.name, "unit": it.unit,
                     "quantity": it.quantity, "unit_price": it.unit_price, "note": it.note,
                     "bolim": it.bolim, "masul": it.masul}
                    for it in proposed
                ])
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
        DRAFT = "draft", "Qoralama"
        DIR = "dir", "Direktor tasdig'ida"
        SUBMITTED = "submitted", "Admin tasdig'ida"   # direktordan o'tgan
        APPROVED = "approved", "Tasdiqlangan"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="weekly_requests", verbose_name="Obyekt",
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
    director_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="weekly_director", verbose_name="Direktor tasdiqladi",
    )
    director_at = models.DateTimeField("Direktor tasdig'i sanasi", null=True, blank=True)
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
    created_at = models.DateTimeField("Kiritilgan sana", auto_now_add=True, null=True)

    class Meta:
        verbose_name = "So'rov qatori"
        verbose_name_plural = "So'rov qatorlari"

    def __str__(self):
        return f"{self.name} × {self.quantity}"

    @property
    def total(self):
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))


class LimitItem(models.Model):
    """Umumiy limit ichi — kategoriya bo'yicha tarkib (material/ish haqi/mashina)."""

    class Kind(models.TextChoices):
        MATERIAL = "material", "Material"
        LABOR = "labor", "Ish haqi"
        MACHINERY = "machinery", "Mashina chasti"
        OTHER = "other", "Ko'zda tutilmagan xarajatlar"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE,
        related_name="limit_items", verbose_name="Obyekt",
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
    responsible = models.CharField("Маъсул шахс", max_length=255, blank=True)
    note = models.CharField("Иш прагнози / izoh", max_length=500, blank=True)
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


def sync_limit_items(project, items):
    """Limit tarkibini yangilaydi — o'zgarmagan qatorlarning SANASI saqlanib qoladi.

    Avval har saqlashda hamma qator o'chirilib qayta yaratilardi; u holda
    «qo'shilgan sana» har safar yangilanib, tarix yo'qolardi.
    Bir xil nomli bir necha qator BO'LISHI MUMKIN (masalan «Гранит» ikki xil
    narxda) — tartib bo'yicha juftlanadi, ortiqcha takror yo'qolmaydi.
    `items` — [{kind, name, unit, quantity, unit_price, note}, ...]
    """
    from collections import defaultdict, deque

    mavjud = defaultdict(deque)
    for li in project.limit_items.all().order_by("id"):
        mavjud[(li.kind, (li.name or "").strip().lower())].append(li)

    for it in items:
        kalit = (it["kind"], (it["name"] or "").strip().lower())
        navbat = mavjud.get(kalit)
        if not navbat:
            LimitItem.objects.create(project=project, **it)
            continue
        li = navbat.popleft()
        ozgardi = (
            li.unit != it.get("unit", "")
            or li.quantity != it["quantity"]
            or li.unit_price != it["unit_price"]
            or li.note != it.get("note", "")
            or li.bolim != it.get("bolim", "")
            or li.masul != it.get("masul", "")
        )
        if ozgardi:
            li.unit = it.get("unit", "")
            li.quantity = it["quantity"]
            li.unit_price = it["unit_price"]
            li.note = it.get("note", "")
            li.bolim = it.get("bolim", "")
            li.masul = it.get("masul", "")
            li.save()          # auto_now -> updated_at yangilanadi
    # ro'yxatda qolmaganlarni o'chiramiz
    for navbat in mavjud.values():
        for li in navbat:
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

    # Rol -> Django guruh nomi (is_pto/is_director/is_prorab guruh bo'yicha ishlaydi)
    ROLE_GROUP = {"director": "Direktor", "pto": "PTO", "prorab": "Prorab", "snab": "Snabjeniye"}

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
        return f"{self.project.code} — {self.get_status_display()} ({self.created_at:%d.%m.%Y})"


class MaterialRequestItem(models.Model):
    """Prorab material so'rovi qatori."""

    class Kind(models.TextChoices):
        MATERIAL = "material", "Material"
        LABOR = "labor", "Ish haqi"
        MACHINERY = "machinery", "Mashina chasti"
        OTHER = "other", "Ko'zda tutilmagan xarajatlar"

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
