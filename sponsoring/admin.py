from django.contrib import admin

from rosterchief.admin import ClubAdminMixin

from .models import Sponsor


@admin.register(Sponsor)
class SponsorAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]
    ordering = ["name"]
