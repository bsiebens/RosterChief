from django.contrib import admin

from .models import EvaluationChecklist, PlayerEvaluation


@admin.register(EvaluationChecklist)
class EvaluationChecklistAdmin(admin.ModelAdmin):
    list_display = ["name", "club", "is_active", "form"]
    list_filter = ["club", "is_active"]
    search_fields = ["name", "club__name"]
    prepopulated_fields = {"slug": ["name"]}
    raw_id_fields = ["form"]


@admin.register(PlayerEvaluation)
class PlayerEvaluationAdmin(admin.ModelAdmin):
    list_display = ["player", "checklist", "club", "season", "created"]
    list_filter = ["club", "season", "checklist"]
    search_fields = ["player__first_name", "player__last_name"]
    raw_id_fields = ["player", "checklist", "submission"]
