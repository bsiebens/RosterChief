from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from members.models import Member

from .forms import UserChangeForm, UserCreationForm
from .models import User


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
