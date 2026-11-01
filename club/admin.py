from django.contrib import admin, messages
from django_tenants.admin import TenantAdminMixin

from .models import Club, Domain, User
from .services import create_account, send_password_setup_email


@admin.register(Club)
class ClubAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ["name", "schema_name", "owner"]


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """There is no public signup: admins create accounts, users then set a password via emailed reset link."""

    list_display = ["email", "first_name", "last_name", "is_active", "is_verified"]
    search_fields = ["email", "first_name", "last_name"]
    fields = ["email", "first_name", "last_name", "is_active"]
    actions = ["send_password_setup"]

    def save_model(self, request, obj, form, change):
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


admin.site.register(Domain)
