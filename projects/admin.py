from django.contrib import admin

from .models import Project, WorkSection


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "status"]
    search_fields = ["code", "name"]


@admin.register(WorkSection)
class WorkSectionAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "parent"]
    list_filter = ["project"]
    search_fields = ["name", "code"]