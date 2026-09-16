from django.contrib import admin

from .models import ContactRequest


@admin.register(ContactRequest)
class ContactRequestAdmin(admin.ModelAdmin):
    list_display = ["name", "club", "role", "members", "plan_interest", "created"]
    list_filter = ["role", "plan_interest"]
    search_fields = ["name", "email", "club"]
