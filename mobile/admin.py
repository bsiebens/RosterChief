from django.contrib import admin

from .models import CalendarFeedToken, PushSubscription


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["member", "club", "user_agent", "created"]
    list_filter = ["club"]
    search_fields = ["member__first_name", "member__last_name", "endpoint"]
    autocomplete_fields = ["member"]


@admin.register(CalendarFeedToken)
class CalendarFeedTokenAdmin(admin.ModelAdmin):
    # Never the token itself -- it's a bearer credential, not something to
    # surface in a list view even to platform staff.
    list_display = ["user", "created", "modified"]
    search_fields = ["user__email"]
