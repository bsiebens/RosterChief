# Generated manually -- data migration, no schema change.

from django.db import migrations
from django.utils.translation import gettext as _


def backfill_game_titles(apps, schema_editor):
    """Rewrites every Game's title from "us vs opponent" to "home vs away".

    Mirrors the ordering management.forms.EventForm.save() now applies going
    forward: a location on record and NOT the club's home ground flips the
    order to "opponent vs us"; no location at all (can't tell home from away)
    keeps the historical "us vs opponent" ordering untouched. Titles are
    always auto-generated for a Game with teams and an opponent (see that
    same save() method's own docstring -- never staff-typed), so it's safe to
    recompute every one of them unconditionally rather than trying to guess
    which ones to leave alone.
    """
    Event = apps.get_model("events", "Event")

    games = Event.objects.filter(kind="game", opponent__isnull=False).select_related("location", "opponent").prefetch_related("teams")
    for event in games.iterator(chunk_size=200):
        teams = list(event.teams.all())
        if not teams:
            continue

        us = ", ".join(team.short_name for team in teams)
        them = event.opponent.name
        is_away = event.location_id is not None and not event.location.is_home
        home_side, away_side = (them, us) if is_away else (us, them)

        title = f"{home_side} {_('vs')} {away_side}"
        if event.title != title:
            event.title = title
            event.save(update_fields=["title"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0032_alter_event_max_officials_alter_event_max_referees"),
    ]

    operations = [
        migrations.RunPython(backfill_game_titles, noop),
    ]
