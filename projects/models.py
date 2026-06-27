from django.db import models


class Project(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Faol"
        PAUSED = "paused", "To'xtatilgan"
        DONE = "done", "Yakunlangan"

    code = models.CharField("Kod", max_length=64, unique=True)
    name = models.CharField("Nomi", max_length=255)
    status = models.CharField(
        "Holati", max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField("Yaratilgan", auto_now_add=True)

    class Meta:
        verbose_name = "Obyekt"
        verbose_name_plural = "Obyektlar"

    def __str__(self):
        return f"{self.code} — {self.name}"


class WorkSection(models.Model):
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