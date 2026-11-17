from django.contrib import admin

from rosterchief.admin import PlatformAdminMixin

from .models import BillingSettings, ClubBilling, ClubCreditEntry, CreditNote, CreditNoteSequence, Due, DuePayment, Invoice, InvoiceSequence, PlanCycle, PlanPrice


class ReadOnlyMixin:
    """Financial records: shown, never added, changed or deleted in the admin."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyInline(ReadOnlyMixin):
    extra = 0
    can_delete = False
    show_change_link = True


@admin.register(BillingSettings)
class BillingSettingsAdmin(PlatformAdminMixin, admin.ModelAdmin):
    """Singleton: one row, created on first use (``BillingSettings.current()``)."""

    list_display = ["legal_name", "vat_id", "vat_rate", "contact_email"]

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not BillingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlanCycle)
class PlanCycleAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["plan", "label", "duration_months", "renewal_lead_days", "grace_days", "position", "is_active"]
    list_filter = ["plan", "is_active"]
    raw_id_fields = ["plan"]
    list_select_related = ["plan"]
    ordering = ["plan__position", "plan__name", "position"]


@admin.register(PlanPrice)
class PlanPriceAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["plan", "cycle", "active_from", "amount"]
    list_filter = ["plan"]
    raw_id_fields = ["plan", "cycle"]
    list_select_related = ["plan"]
    date_hierarchy = "active_from"


@admin.register(ClubBilling)
class ClubBillingAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["club", "auto_renew", "auto_archive", "trial_ends_at", "downgraded_at"]
    list_filter = ["auto_renew", "auto_archive"]
    search_fields = ["club__name", "club__slug"]
    autocomplete_fields = ["club"]
    list_select_related = ["club"]


class DuePaymentInline(ReadOnlyInline, admin.TabularInline):
    model = DuePayment
    fields = ["paid_at", "amount", "method", "reference", "recorded_by", "note"]
    readonly_fields = fields
    ordering = ["paid_at"]


class DueInvoiceInline(ReadOnlyInline, admin.TabularInline):
    model = Invoice
    fields = ["number", "issued_at", "sent_at", "voided_at", "replaced_by"]
    readonly_fields = fields


@admin.register(Due)
class DueAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["club", "plan", "period_start", "period_end", "amount", "vat_amount", "amount_paid", "status", "is_trial"]
    list_filter = ["status", "is_trial", "plan"]
    search_fields = ["club__name", "club__slug"]
    list_select_related = ["club", "plan"]
    date_hierarchy = "period_start"
    inlines = [DuePaymentInline, DueInvoiceInline]


@admin.register(DuePayment)
class DuePaymentAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["paid_at", "due", "amount", "method", "reference", "recorded_by"]
    list_filter = ["method"]
    search_fields = ["reference", "due__club__name"]
    list_select_related = ["due", "due__club", "recorded_by"]
    date_hierarchy = "paid_at"


@admin.register(Invoice)
class InvoiceAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["number", "due", "issued_at", "sent_at", "sent_method", "voided_at"]
    list_filter = ["sent_method"]
    search_fields = ["number", "due__club__name"]
    list_select_related = ["due", "due__club"]
    date_hierarchy = "issued_at"


@admin.register(CreditNote)
class CreditNoteAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["number", "club", "invoice", "issued_at", "kind", "net", "vat", "total"]
    list_filter = ["kind"]
    search_fields = ["number", "club__name", "invoice__number", "reason"]
    list_select_related = ["club", "invoice"]
    date_hierarchy = "issued_at"


@admin.register(ClubCreditEntry)
class ClubCreditEntryAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["created", "club", "kind", "amount", "credit_note", "due", "note"]
    list_filter = ["kind"]
    search_fields = ["club__name", "note"]
    list_select_related = ["club", "credit_note", "due"]
    date_hierarchy = "created"


@admin.register(InvoiceSequence)
class InvoiceSequenceAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["scope", "last_number"]
    ordering = ["scope"]


@admin.register(CreditNoteSequence)
class CreditNoteSequenceAdmin(PlatformAdminMixin, ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["scope", "last_number"]
    ordering = ["scope"]
