from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from members.models import (
    AuditEntry,
    ClubMembership,
    DuesInvoice,
    Family,
    FamilyMembership,
    FeePayment,
    Group,
    GroupMembership,
    ImportJob,
    Member,
    MemberRequirementStatus,
    OnboardingRequirement,
    RequirementEvent,
)
from rosterchief.admin import ClubAdminMixin


class FamilyMembershipByMemberInline(admin.TabularInline):
    model = FamilyMembership
    fk_name = "member"
    extra = 0
    autocomplete_fields = ["family"]


class FamilyMembershipByFamilyInline(admin.TabularInline):
    model = FamilyMembership
    fk_name = "family"
    extra = 0
    autocomplete_fields = ["member"]


class ClubMembershipInline(admin.TabularInline):
    model = ClubMembership
    extra = 0
    fields = ["season", "status", "fee_status", "fee_amount", "amount_paid"]
    readonly_fields = ["amount_paid"]
    show_change_link = True


class GroupMembershipByMemberInline(admin.TabularInline):
    model = GroupMembership
    fk_name = "member"
    extra = 0
    autocomplete_fields = ["group"]


class GroupMembershipByGroupInline(admin.TabularInline):
    model = GroupMembership
    fk_name = "group"
    extra = 0
    autocomplete_fields = ["member"]


class FeePaymentInline(admin.TabularInline):
    model = FeePayment
    extra = 0
    fields = ["amount", "method", "reference", "paid_at", "recorded_by"]
    raw_id_fields = ["recorded_by"]


@admin.register(Member)
class MemberAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["last_name", "first_name", "email", "phone", "date_of_birth", "display_roles", "license_number"]
    list_filter = ["language", "photo_public_consent"]
    search_fields = ["first_name", "last_name", "email", "license_number"]
    ordering = ["last_name", "first_name"]
    raw_id_fields = ["user"]
    inlines = [FamilyMembershipByMemberInline, ClubMembershipInline, GroupMembershipByMemberInline]

    @admin.display(description=_("roles"))
    def display_roles(self, obj):
        return ", ".join(sorted(str(r.label) if hasattr(r, "label") else str(r) for r in obj.roles)) or "-"


@admin.register(Family)
class FamilyAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "member_count"]
    search_fields = ["name", "memberships__member__first_name", "memberships__member__last_name"]
    ordering = ["name"]
    inlines = [FamilyMembershipByFamilyInline]

    @admin.display(description=_("members"))
    def member_count(self, obj):
        return obj.memberships.count()


@admin.register(FamilyMembership)
class FamilyMembershipAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["family", "member", "role"]
    list_filter = ["role"]
    list_select_related = ["family", "member"]
    search_fields = ["family__name", "member__first_name", "member__last_name"]
    autocomplete_fields = ["family", "member"]


@admin.register(Group)
class GroupAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]
    ordering = ["name"]
    inlines = [GroupMembershipByGroupInline]


@admin.register(GroupMembership)
class GroupMembershipAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["group", "member"]
    list_filter = ["group"]
    list_select_related = ["group", "member"]
    search_fields = ["group__name", "member__first_name", "member__last_name"]
    autocomplete_fields = ["group", "member"]


@admin.register(ClubMembership)
class ClubMembershipAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["member", "season", "status", "fee_status", "fee_amount", "amount_paid", "signed_up_at", "activated_at"]
    list_filter = ["season", "status", "fee_status"]
    list_select_related = ["member", "season"]
    search_fields = ["member__first_name", "member__last_name", "member__email"]
    autocomplete_fields = ["member"]
    readonly_fields = ["amount_paid"]
    date_hierarchy = "signed_up_at"
    inlines = [FeePaymentInline]


@admin.register(FeePayment)
class FeePaymentAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["membership", "amount", "method", "reference", "paid_at", "recorded_by"]
    list_filter = ["method"]
    list_select_related = ["membership__member", "membership__season", "recorded_by"]
    search_fields = ["reference", "membership__member__first_name", "membership__member__last_name"]
    raw_id_fields = ["membership", "recorded_by"]
    date_hierarchy = "paid_at"


@admin.register(DuesInvoice)
class DuesInvoiceAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["number", "membership", "amount", "due_date", "sent_at", "sent_to_email", "reminder_count"]
    list_filter = ["sent_to_guardian"]
    list_select_related = ["membership__member", "membership__season"]
    search_fields = ["number", "sent_to_email", "membership__member__first_name", "membership__member__last_name"]
    raw_id_fields = ["membership"]
    date_hierarchy = "due_date"


@admin.register(OnboardingRequirement)
class OnboardingRequirementAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["name", "requires_document", "blocked_event_kinds", "is_active"]
    list_filter = ["is_active", "requires_document"]
    search_fields = ["name", "description"]
    ordering = ["name"]


@admin.register(MemberRequirementStatus)
class MemberRequirementStatusAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["membership", "requirement", "is_complete", "is_bypassed", "completed_at", "completed_by"]
    list_filter = ["requirement", "is_complete", "is_bypassed"]
    list_select_related = ["membership__member", "membership__season", "requirement", "completed_by"]
    search_fields = ["membership__member__first_name", "membership__member__last_name", "requirement__name"]
    raw_id_fields = ["membership", "completed_by"]


@admin.register(RequirementEvent)
class RequirementEventAdmin(ClubAdminMixin, admin.ModelAdmin):
    list_display = ["created", "status", "kind", "actor_name"]
    list_filter = ["kind"]
    list_select_related = ["status__membership__member", "status__requirement"]
    raw_id_fields = ["status", "actor"]


@admin.register(AuditEntry)
class AuditEntryAdmin(ClubAdminMixin, admin.ModelAdmin):
    """Append-only history: view only."""

    list_display = ["created", "action", "target_type", "target_label", "member", "actor"]
    list_filter = ["action", "target_type"]
    list_select_related = ["member", "actor"]
    search_fields = ["action", "target_label", "target_id", "member__first_name", "member__last_name"]
    date_hierarchy = "created"
    ordering = ["-created"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportJob)
class ImportJobAdmin(ClubAdminMixin, admin.ModelAdmin):
    """Background member imports (the rows and the per-row result are kept for the run, then dropped)."""

    list_display = ["created", "status", "total", "processed", "created_by", "finished_at"]
    list_filter = ["status"]
    list_select_related = ["created_by"]
    ordering = ["-created"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]
