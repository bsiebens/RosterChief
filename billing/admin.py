from django.contrib import admin

from .models import Due, DuePayment, Subscription, Tier, TierPrice


class TierPriceInline(admin.TabularInline):
    model = TierPrice
    extra = 0


@admin.register(Tier)
class TierAdmin(admin.ModelAdmin):
    list_display = ["name", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [TierPriceInline]


@admin.register(TierPrice)
class TierPriceAdmin(admin.ModelAdmin):
    list_display = ["tier", "amount", "active_from"]
    list_filter = ["tier"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["club", "tier", "auto_archive"]
    list_filter = ["tier", "auto_archive"]
    search_fields = ["club__name"]


class DuePaymentInline(admin.TabularInline):
    model = DuePayment
    extra = 0
    readonly_fields = ["recorded_by"]


@admin.register(Due)
class DueAdmin(admin.ModelAdmin):
    list_display = ["club", "tier", "period_start", "period_end", "amount", "amount_paid", "status"]
    list_filter = ["status", "tier"]
    search_fields = ["club__name"]
    # Money is settled by the billing service, which re-derives these from the payments.
    readonly_fields = ["amount_paid", "status", "paid_at"]
    inlines = [DuePaymentInline]


@admin.register(DuePayment)
class DuePaymentAdmin(admin.ModelAdmin):
    list_display = ["due", "amount", "method", "paid_at", "recorded_by"]
    list_filter = ["method"]
    search_fields = ["due__club__name", "reference"]
