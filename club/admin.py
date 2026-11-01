from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe
from django.db import connection
from django_tenants.admin import TenantAdminMixin

from authorization.models import Role
from authorization.services import ProtectedRoleError, roles_by_club, set_roles, validate_roles
from rosterchief.admin import PlatformAdminMixin, in_club, is_club_admin_user
from authorization.permissions import is_platform_owner

from .models import Club, Domain, Sport, User
from .services import create_account, provision_club, send_password_setup_email


@admin.register(Club)
class ClubAdmin(PlatformAdminMixin, TenantAdminMixin, admin.ModelAdmin):
    list_display = ["name", "slug", "sport"]
    list_filter = ["sport"]
    search_fields = ["name", "slug"]
    ordering = ["name"]

    @admin.display(description="Plan and features")
    def plan_and_features(self, obj):
        from features.admin import effective_features_html
        from features.models import Subscription

        sub = Subscription.objects.filter(club=obj, ends_at__isnull=True).select_related("plan").first()
        head = format_html_join("", "<div>Plan: <strong>{}</strong> ({})</div>", [(sub.plan, sub.status)] if sub else [("none", "-")])
        return mark_safe(f"{head}{effective_features_html(obj)}")

    def get_fields(self, request, obj=None):
        # Slug, schema and domain are generated on creation and fixed afterwards (the domain depends on the slug).
        return ["name", "sport", "owner"] if obj is None else ["name", "sport", "owner", "slug", "schema_name", "plan_and_features"]

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else ("slug", "schema_name", "plan_and_features")

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
        else:
            provision_club(obj)


@admin.register(Sport)
class SportAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["name", "slug"]
    ordering = ["name"]
    search_fields = ["name", "slug"]


class ClubRolesForm(forms.ModelForm):
    """On a club domain the only editable thing about a user is the roles they hold in that club."""

    roles = forms.ModelMultipleChoiceField(queryset=Role.objects.none(), required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = User
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"].queryset = Role.objects.select_related("group").order_by("group__name")
        if self.instance.pk:
            self.fields["roles"].initial = Role.objects.filter(group__in=self.instance.tenant_perms.groups.all())

    def clean_roles(self):
        roles = self.cleaned_data["roles"]
        try:
            validate_roles(self.instance, roles)
        except ProtectedRoleError as e:
            raise ValidationError(str(e)) from e
        return roles


def can_manage_roles(user) -> bool:
    return is_platform_owner(user) or user.has_perm("authorizations.manage_roles")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """There is no public signup: admins create accounts, users then set a password via emailed reset link."""

    list_display = ["email", "first_name", "last_name", "is_active", "is_verified"]
    search_fields = ["email", "first_name", "last_name"]
    actions = ["send_password_setup", "deactivate"]

    def get_fields(self, request, obj=None):
        if in_club():
            return ["email", "first_name", "last_name", "roles"]
        return ["email", "first_name", "last_name", "is_active"] + ([] if obj is None else ["club_roles"])

    def get_readonly_fields(self, request, obj=None):
        if in_club():
            return ("email", "first_name", "last_name")
        return () if obj is None else ("club_roles",)

    def get_form(self, request, obj=None, **kwargs):
        if in_club():
            kwargs["form"] = ClubRolesForm
        return super().get_form(request, obj, **kwargs)

    def get_list_display(self, request):
        return ["email", "first_name", "last_name", "roles_here"] if in_club() else super().get_list_display(request)

    @admin.display(description="Roles")
    def roles_here(self, obj):
        return ", ".join(sorted(obj.tenant_perms.groups.values_list("name", flat=True))) or "-"

    @admin.display(description="Clubs and roles")
    def club_roles(self, obj):
        rows = roles_by_club(obj, [connection.tenant]) if in_club() else roles_by_club(obj)
        if not rows:
            return "Not a member of any club."
        return format_html_join(
            "",
            "<div><strong>{}</strong> ({}): {}</div>",
            ((club.name, club.schema_name, ", ".join(roles) or mark_safe("<em>no roles</em>")) for club, roles in rows),
        )

    # On a club domain the user admin lists that club's users and lets club admins edit their roles.
    def has_module_permission(self, request):
        return is_club_admin_user(request.user) if in_club() else super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return is_club_admin_user(request.user) if in_club() else super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return False if in_club() else super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return can_manage_roles(request.user) if in_club() else super().has_change_permission(request, obj)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(tenants=connection.tenant) if in_club() else qs

    def get_actions(self, request):
        return {} if in_club() else super().get_actions(request)

    def has_delete_permission(self, request, obj=None):
        # Users are referenced from club schemas (e.g. Member.user), which a public-schema delete cannot see.
        # Deactivate instead of deleting.
        return False

    def save_model(self, request, obj, form, change):
        if in_club():
            set_roles(obj, form.cleaned_data["roles"])
            return
        if change:
            super().save_model(request, obj, form, change)
            return
        created = create_account(obj.email, first_name=obj.first_name, last_name=obj.last_name)
        obj.pk = created.pk
        obj._state.adding = False
        send_password_setup_email(request, created)
        messages.success(request, f"Account created. A password setup email was sent to {created.email}.")

    @admin.action(description="Send password setup / reset email")
    def send_password_setup(self, request, queryset):
        for user in queryset.filter(is_active=True):
            send_password_setup_email(request, user)
        self.message_user(request, "Password setup emails sent.")

    @admin.action(description="Deactivate selected users")
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} user(s) deactivated.")



@admin.register(Domain)
class DomainAdmin(PlatformAdminMixin, admin.ModelAdmin):
    list_display = ["domain", "tenant", "is_primary"]
