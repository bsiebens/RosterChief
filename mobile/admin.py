from django.contrib import admin

from .models import PushSubscription


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["member", "club", "user_agent", "created"]
    list_filter = ["club"]
    search_fields = ["member__first_name", "member__last_name", "endpoint"]
    autocomplete_fields = ["member"]
