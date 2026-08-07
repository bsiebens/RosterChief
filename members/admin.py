from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Family, FamilyMembership, Member


# Register your models here.
class MemberFamilyInline(admin.TabularInline):
    """Family memberships shown on the Member page."""

    model = FamilyMembership
    extra = 1
    autocomplete_fields = ("family",)


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "contact_email", "phone_display", "emergency_phone_display", "user")
    list_select_related = ("user",)
    search_fields = ("first_name", "last_name", "email")
    autocomplete_fields = ("user",)
    inlines = [MemberFamilyInline]
    fields = ("user", "first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone")

    @admin.display(description=_("email"), ordering="email")
    def contact_email(self, obj):
        return obj.contact_email

    @admin.display(description=_("phone"), ordering="phone")
    def phone_display(self, obj):
        return obj.phone.as_international if obj.phone else ""

    @admin.display(description=_("emergency phone"), ordering="emergency_phone")
    def emergency_phone_display(self, obj):
        return obj.emergency_phone.as_international if obj.emergency_phone else ""


class FamilyMemberInline(admin.TabularInline):
    """Members shown on the Family page."""

    model = FamilyMembership
    extra = 1
    autocomplete_fields = ("member",)


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = ("__str__", "member_count")
    search_fields = ("name", "memberships__member__first_name", "memberships__member__last_name")
    inlines = [FamilyMemberInline]

    @admin.display(description=_("members"))
    def member_count(self, obj):
        return obj.memberships.count()


@admin.register(FamilyMembership)
class FamilyMembershipAdmin(admin.ModelAdmin):
    list_display = ("family", "member", "role")
    list_filter = ("role",)
    autocomplete_fields = ("family", "member")
    search_fields = ("family__name", "member__first_name", "member__last_name")
