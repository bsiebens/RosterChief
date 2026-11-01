"""Admin site aware of the platform (public schema) versus a club (tenant schema).

* Platform models (Club, Sport, Domain, ...) are only offered on the base domain.
* While DEBUG is on, club admins (platform owners or holders of an admin-flagged permission) may use the
  admin on their club domain even though they are not ``is_staff`` there. In production the admin stays
  restricted to staff on the base domain.
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.db import connection
from django_tenants.utils import get_public_schema_name


def in_club() -> bool:
    return connection.schema_name != get_public_schema_name()


def is_club_admin_user(user) -> bool:
    from authorization.permissions import is_club_admin

    return bool(user.is_authenticated and user.is_active and is_club_admin(user))


def can_use_admin(user) -> bool:
    if not (user.is_authenticated and user.is_active):
        return False
    return bool(user.is_staff or (settings.DEBUG and in_club() and is_club_admin_user(user)))


class RosterAdminAuthenticationForm(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super(AdminAuthenticationForm, self).confirm_login_allowed(user)  # active check only
        if not can_use_admin(user):
            raise ValidationError(self.error_messages["invalid_login"], code="invalid_login", params={"username": self.username_field.verbose_name})


class RosterAdminSite(admin.AdminSite):
    login_form = RosterAdminAuthenticationForm

    def has_permission(self, request):
        return can_use_admin(request.user)


class PlatformAdminMixin:
    """Shared (public schema) models: only on the base domain."""

    def has_module_permission(self, request):
        return not in_club() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return not in_club() and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return not in_club() and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return not in_club() and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not in_club() and super().has_delete_permission(request, obj)
