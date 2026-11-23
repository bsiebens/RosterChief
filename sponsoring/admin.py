from django.contrib import admin

from rosterchief.admin import ClubAdminMixin

from .models import Sponsor, SponsorAgreement, SponsorInstallment


@admin.register(Sponsor)
class SponsorAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]
    ordering = ["name"]


@admin.register(SponsorAgreement)
class SponsorAgreementAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["sponsor", "contact_name", "contact_email", "lead_days", "grace_days", "keep_active"]
    search_fields = ["sponsor__name", "contact_name", "contact_email"]


@admin.register(SponsorInstallment)
class SponsorInstallmentAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["agreement", "due_date", "amount", "order"]
    ordering = ["due_date"]
