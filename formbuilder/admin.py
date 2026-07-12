from django.contrib import admin

from .models import Answer, Field, Form, Submission


class FieldInline(admin.TabularInline):
    model = Field
    extra = 0
    fields = ["order", "key", "label", "field_type", "required", "is_active"]
    ordering = ["order"]


@admin.register(Form)
class FormAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "club", "is_active", "login_required", "opens_at", "closes_at"]
    list_filter = ["club", "is_active", "login_required"]
    search_fields = ["title", "slug"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [FieldInline]


@admin.register(Field)
class FieldAdmin(admin.ModelAdmin):
    list_display = ["label", "key", "form", "field_type", "order", "required", "is_active"]
    list_filter = ["field_type", "required", "is_active"]
    search_fields = ["label", "key", "form__title"]
    raw_id_fields = ["form"]


class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 0
    raw_id_fields = ["field"]


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ["form", "member", "submitted_at"]
    list_filter = ["form"]
    search_fields = ["form__title", "member__first_name", "member__last_name"]
    raw_id_fields = ["form", "member"]
    readonly_fields = ["submitted_at"]
    inlines = [AnswerInline]


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ["submission", "field", "value"]
    search_fields = ["field__label", "field__key"]
    raw_id_fields = ["submission", "field"]
