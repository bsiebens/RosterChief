from django.contrib import admin

from .models import EvaluationSettings, PlayerEvaluation


@admin.register(EvaluationSettings)
class EvaluationSettingsAdmin(admin.ModelAdmin):
    list_display = ["club", "form"]
    search_fields = ["club__name"]
    raw_id_fields = ["form"]


@admin.register(PlayerEvaluation)
class PlayerEvaluationAdmin(admin.ModelAdmin):
    list_display = ["player", "club", "season", "created"]
    list_filter = ["club", "season"]
    search_fields = ["player__first_name", "player__last_name"]
    raw_id_fields = ["player", "submission"]
