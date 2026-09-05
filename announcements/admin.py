from django.contrib import admin

from .models import Announcement, AnnouncementSeen


class AnnouncementSeenInline(admin.TabularInline):
    model = AnnouncementSeen
    extra = 0
    readonly_fields = ["user", "seen_at"]


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ["title", "club", "status", "scheduled_for", "sent_at", "created_by"]
    list_filter = ["status", "club"]
    search_fields = ["title", "message"]
    inlines = [AnnouncementSeenInline]
