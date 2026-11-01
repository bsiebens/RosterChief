from django.contrib import admin
from django_tenants.admin import TenantAdminMixin

from .models import Club, Domain


@admin.register(Club)
class ClubAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ["name"]


admin.site.register(Domain)
