from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Club, ClubMembership, ClubRole, FeePayment, Season, Sponsor


@admin.register(Club)
class ClubAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "sport_type"]
    list_filter = ["sport_type"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    ordering = ["name"]


@admin.register(Sponsor)
class SponsorAdmin(admin.ModelAdmin):
    list_display = ["name", "club", "start_date", "end_date"]
    list_filter = ["club"]
    search_fields = ["name"]


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ["__str__", "club", "start_date", "end_date"]
    list_filter = ["club"]
    search_fields = ["club__name"]
    ordering = ["club", "-start_date"]


class FeePaymentInline(admin.TabularInline):
    model = FeePayment
    extra = 0
    readonly_fields = ["recorded_by"]


@admin.register(ClubMembership)
class ClubMembershipAdmin(admin.ModelAdmin):
    list_display = ["club__name", "member__last_name", "member__first_name", "season", "status", "fee_status", "fee_amount", "amount_paid", "license"]
    search_fields = ["club__name", "member__last_name", "member__first_name", "license"]
    list_filter = ["club", "season", "status", "fee_status"]
    raw_id_fields = ["member"]
    # Money is settled by club.services.fees, which re-derives fee_status from the payments.
    readonly_fields = ["amount_paid", "fee_status"]
    fieldsets = [
        [None, {"fields": ["club", "season", "member"]}],
        [_("Membership"), {"fields": ["license", "status", "fee_status", "fee_amount", "amount_paid"]}],
        [_("Dates"), {"fields": ["signed_up_at", "activated_at"]}],
    ]
    inlines = [FeePaymentInline]


@admin.register(FeePayment)
class FeePaymentAdmin(admin.ModelAdmin):
    list_display = ["membership", "amount", "method", "paid_at", "recorded_by"]
    list_filter = ["method"]
    search_fields = ["membership__club__name", "membership__member__last_name", "reference"]


@admin.register(ClubRole)
class ClubRoleAdmin(admin.ModelAdmin):
    list_display = ["club__name", "member__last_name", "member__first_name", "role"]
    search_fields = ["club__name", "member__last_name", "member__first_name"]
    list_filter = ["club", "role"]
    raw_id_fields = ["member"]
