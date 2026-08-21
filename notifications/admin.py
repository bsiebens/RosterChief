from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "member", "club", "sent_at", "content_type"]
    list_filter = ["club", "sent_at"]
    search_fields = ["title", "member__last_name", "member__first_name"]
    raw_id_fields = ["member"]
    readonly_fields = ["sent_at", "sent_to_emails"]
