from django.contrib import admin

from .models import Due, DuePayment, Plan, PlanPrice, Subscription


class PlanPriceInline(admin.TabularInline):
    model = PlanPrice
    extra = 0


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "duration_months", "renewal_lead_days", "grace_days", "is_trial", "is_active"]
    list_filter = ["is_active", "is_trial"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [PlanPriceInline]


@admin.register(PlanPrice)
class PlanPriceAdmin(admin.ModelAdmin):
    list_display = ["plan", "amount", "active_from"]
    list_filter = ["plan"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["club", "plan", "auto_renew", "auto_archive"]
    list_filter = ["plan", "auto_renew", "auto_archive"]
    search_fields = ["club__name"]


class DuePaymentInline(admin.TabularInline):
    model = DuePayment
    extra = 0
    readonly_fields = ["recorded_by"]


@admin.register(Due)
class DueAdmin(admin.ModelAdmin):
    list_display = ["club", "plan", "period_start", "period_end", "grace_until", "amount", "amount_paid", "status"]
    list_filter = ["status", "plan"]
    search_fields = ["club__name"]
    # Money is settled by the billing service, which re-derives these from the payments.
    # period_end/grace_until are snapshots taken when the period opened -- editing a plan
    # afterwards must not move them, and neither should a hand edit here.
    readonly_fields = ["amount_paid", "status", "paid_at", "period_end", "grace_until"]
    inlines = [DuePaymentInline]


@admin.register(DuePayment)
class DuePaymentAdmin(admin.ModelAdmin):
    list_display = ["due", "amount", "method", "paid_at", "recorded_by"]
    list_filter = ["method"]
    search_fields = ["due__club__name", "reference"]
