from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .forms import UserChangeForm, UserCreationForm
from .models import Family, FamilyMembership, Member, User


class MemberInline(admin.StackedInline):
    """Edit the member profile attached to a login from the User page."""

    model = Member
    can_delete = False
    extra = 0
    max_num = 1
    verbose_name_plural = _("member profile")
    fields = ("first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    model = User
    inlines = [MemberInline]

    list_display = ("email", "full_name", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("email", "member__first_name", "member__last_name")
    ordering = ("email",)
    readonly_fields = ("last_login",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important dates"), {"fields": ("last_login",)}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)

    @admin.display(description=_("name"))
    def full_name(self, obj):
        return obj.get_full_name()


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
