from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Club, ClubMembership, Season


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    ordering = ["name"]


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ["__str__", "club", "start_date", "end_date"]
    list_filter = ["club"]
    search_fields = ["club__name"]
    ordering = ["club", "-start_date"]


@admin.register(ClubMembership)
class ClubMembershipAdmin(admin.ModelAdmin):
    list_display = ["club__name", "member__last_name", "member__first_name", "season", "status", "fee_status", "license"]
    search_fields = ["club__name", "member__last_name", "member__first_name", "license"]
    list_filter = ["club", "season", "status", "fee_status"]
    raw_id_fields = ["member"]
    fieldsets = [
        [None, {"fields": ["club", "season", "member"]}],
        [_("Membership"), {"fields": ["license", "status", "fee_status"]}],
        [_("Dates"), {"fields": ["signed_up_at", "activated_at"]}],
    ]
