"""
projects/models.py
"""
from decimal import Decimal
from django.db import models


class Project(models.Model):
    """Qurilish obyekti (loyiha)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        PAUSED = "paused", "To'xtatilgan"
        DONE = "done", "Yakunlangan"

    code = models.CharField("Kod", max_length=64, unique=True)
    name = models.CharField("Nomi", max_length=255)
    status = models.CharField(
        "Holati", max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    budget_total = models.DecimalField(
        "Umumiy limit", max_digits=18, decimal_places=2, default=0,
        help_text="0 = limit yo'q. Oshsa: RUXSAT KERAK.",
    )
    budget_weekly = models.DecimalField(
        "Haftalik limit", max_digits=18, decimal_places=2, default=0,
        help_text="0 = limit yo'q. Faqat ko'rsatkich.",
    )
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)

    class Meta:
        verbose_name = "Obyekt"
        verbose_name_plural = "Obyektlar"

    def __str__(self):
        return f"{self.code} — {self.name}"

    def sarflangan(self):
        from ombor.models import Issue
        total = Decimal("0.00")
        issues = Issue.objects.filter(
            work_section__project=self, is_posted=True
        ).prefetch_related("items")
        for issue in issues:
            total += issue.total
        return total

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
