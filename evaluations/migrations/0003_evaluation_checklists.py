# Generated manually -- renames the club-wide singleton EvaluationSettings
# into a named, repeatable EvaluationChecklist, and gives PlayerEvaluation a
# denormalized FK to whichever checklist it was scored against (see both
# models' own docstrings for why).

import django.db.models.deletion
from django.db import migrations, models


def backfill_checklist_metadata(apps, schema_editor):
    """Every pre-existing row was the club's one and only rubric -- name it
    "Default" so it keeps working exactly as before until someone renames
    or adds to it."""
    EvaluationChecklist = apps.get_model("evaluations", "EvaluationChecklist")
    for checklist in EvaluationChecklist.objects.all():
        checklist.name = "Default"
        checklist.slug = "default"
        checklist.save(update_fields=["name", "slug"])


def backfill_playerevaluation_checklist(apps, schema_editor):
    """Every pre-existing PlayerEvaluation was scored against its club's one
    (now-renamed) checklist -- there was never more than one to choose
    between."""
    PlayerEvaluation = apps.get_model("evaluations", "PlayerEvaluation")
    EvaluationChecklist = apps.get_model("evaluations", "EvaluationChecklist")
    checklist_by_club = {checklist.club_id: checklist for checklist in EvaluationChecklist.objects.all()}
    for evaluation in PlayerEvaluation.objects.all():
        checklist = checklist_by_club.get(evaluation.club_id)
        if checklist is not None:
            evaluation.checklist = checklist
            evaluation.save(update_fields=["checklist"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("evaluations", "0002_create_evaluations_flag"),
    ]

    operations = [
        migrations.RenameModel(old_name="EvaluationSettings", new_name="EvaluationChecklist"),
        migrations.AlterModelOptions(
            name="evaluationchecklist",
            options={"ordering": ["order", "name"], "verbose_name": "evaluation checklist", "verbose_name_plural": "evaluation checklists"},
        ),
        migrations.RemoveConstraint(model_name="evaluationchecklist", name="unique_evaluation_settings_per_club"),
        migrations.AddField(model_name="evaluationchecklist", name="name", field=models.CharField(default="", max_length=255, verbose_name="name"), preserve_default=False),
        migrations.AddField(model_name="evaluationchecklist", name="slug", field=models.SlugField(blank=True, verbose_name="slug")),
        migrations.AddField(
            model_name="evaluationchecklist",
            name="description",
            field=models.TextField(blank=True, help_text="Optional notes for whoever picks a checklist -- e.g. which age group or squad it's meant for.", verbose_name="description"),
        ),
        migrations.AddField(
            model_name="evaluationchecklist",
            name="is_active",
            field=models.BooleanField(default=True, help_text="Whether this checklist can still be picked for a new evaluation. Existing evaluations against it are unaffected.", verbose_name="is active?"),
        ),
        migrations.AddField(model_name="evaluationchecklist", name="order", field=models.PositiveIntegerField(default=0, verbose_name="order")),
        migrations.AlterField(
            model_name="evaluationchecklist",
            name="form",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="evaluation_checklists_for", to="formbuilder.form", verbose_name="form"),
        ),
        migrations.RunPython(backfill_checklist_metadata, noop),
        migrations.AddConstraint(model_name="evaluationchecklist", constraint=models.UniqueConstraint(fields=("club", "slug"), name="unique_evaluation_checklist_slug_per_club")),
        migrations.AddField(
            model_name="playerevaluation",
            name="checklist",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="evaluations", to="evaluations.evaluationchecklist", verbose_name="checklist"),
        ),
        migrations.RunPython(backfill_playerevaluation_checklist, noop),
        migrations.AlterField(
            model_name="playerevaluation",
            name="checklist",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="evaluations", to="evaluations.evaluationchecklist", verbose_name="checklist"),
        ),
    ]
