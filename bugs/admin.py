from django.contrib import admin

from .models import BugNote, BugReport


class BugNoteInline(admin.TabularInline):
    model = BugNote
    extra = 0
    readonly_fields = ["created"]


@admin.register(BugReport)
class BugReportAdmin(admin.ModelAdmin):
    list_display = ["title", "club", "reported_by", "status", "priority", "created", "fixed_at"]
    list_filter = ["club", "status", "priority"]
    search_fields = ["title", "description", "reported_by__last_name", "reported_by__first_name"]
    raw_id_fields = ["reported_by"]
    inlines = [BugNoteInline]
