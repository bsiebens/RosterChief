from django.contrib import admin

from .models import RegistrationBatch, RegistrationDetails


class RegistrationDetailsInline(admin.TabularInline):
    model = RegistrationDetails
    extra = 0
    fields = ["membership", "entry_kind", "requested_team", "requested_position", "product_variant", "price", "discount_amount"]
    raw_id_fields = ["membership", "requested_team", "requested_position", "product_variant"]


@admin.register(RegistrationBatch)
class RegistrationBatchAdmin(admin.ModelAdmin):
    list_display = ["contact_first_name", "contact_last_name", "contact_email", "club", "season", "subtotal", "discount_amount", "total", "created"]
    list_filter = ["club", "season"]
    search_fields = ["contact_first_name", "contact_last_name", "contact_email"]
    raw_id_fields = ["submitted_by_user"]
    inlines = [RegistrationDetailsInline]


@admin.register(RegistrationDetails)
class RegistrationDetailsAdmin(admin.ModelAdmin):
    list_display = ["membership", "entry_kind", "requested_team", "requested_position", "batch"]
    list_filter = ["entry_kind"]
    search_fields = ["membership__member__first_name", "membership__member__last_name"]
    raw_id_fields = ["membership", "batch", "requested_team", "requested_position", "product_variant", "resulting_team_membership", "resulting_staff_assignment"]
