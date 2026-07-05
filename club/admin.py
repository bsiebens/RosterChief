from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Club, ClubMembership


# Register your models here.
@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]
    ordering = ["name"]


@admin.register(ClubMembership)
class ClubMembershipAdmin(admin.ModelAdmin):
    list_display = ["club__name", "member__last_name", "member__first_name", "license"]
    search_fields = ["club__name", "member__last_name", "member__first_name"]
    list_filter = ["club"]
    raw_id_fields = ["member"]
    fieldsets = [
        [None, {"fields": ["club", "member"]}],
        [_("Member informaton"), {"fields": ["license"]}],
    ]
