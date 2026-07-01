from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .forms import UserChangeForm, UserCreationForm
from .models import Family, Member, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    model = User

    list_display = ("email", "is_staff", "is_superuser", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("email",)
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "usable_password", "password1", "password2"),
        }),
    )


class MemberInline(admin.TabularInline):
    model = Member
    extra = 0
    fields = ("first_name", "last_name", "date_of_birth", "is_guardian", "license_number")
    show_change_link = True


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "date_of_birth", "is_guardian", "license_number", "family", "user")
    list_filter = ("is_guardian", "family")
    search_fields = ("first_name", "last_name", "email", "license_number")
    autocomplete_fields = ("user", "family")


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = ("name", "address")
    search_fields = ("name",)
    inlines = (MemberInline,)
