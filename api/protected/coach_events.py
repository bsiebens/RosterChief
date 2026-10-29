"""Event and series editing for the coach endpoints (API.md §7.3).

The validation is the web coach screens' own: ``management.forms.EventForm`` /
``EventSeriesForm``, scoped the way ``mobile.coach_views.CoachCreateEventView`` scopes them
(audience locked to the path's team, no groups, no club-wide, four kinds). A JSON body is turned
into the form's POST data, so every rule and error message is the same; a failed form answers
``422`` with ``fields`` named after the JSON keys.
"""

import datetime

from django.db import transaction

from api.core.forms import form_error
from api.core.forms import post_data as core_post_data
from club.services.access import current_season, teams_managed_by
from events.models import EventSeries
from events.services import propagate_series
from events.services.coach import COACH_EVENT_KINDS
from management.forms import EventForm, EventSeriesForm
from management.recurrence_ui import parse_rrule
from members.models import Member
from teams.models import Team, TeamMembership
from teams.services import eligible_roster_members

#: Form field name -> the JSON key the app knows.
FIELD_ALIASES = {"location": "location_id", "opponent": "opponent_id", "invited_members": "invited_member_ids", "excluded_members": "excluded_member_ids", "__all__": "non_field_errors"}

#: What a series sets on its occurrences (events.services.recurrence.apply_template). Editing one of
#: these on an occurrence that still follows its series would be undone by the next series edit.
TEMPLATE_FIELDS = {"title", "kind", "location_id", "opponent_id", "end", "gathering", "deadline", "invited_member_ids", "excluded_member_ids"}
#: Per-event fields that never go through the series.
RESULT_FIELDS = {"score_for", "score_against", "is_live"}


def _member_pools(coach):
    """Who can be invited (eligible club members off the roster) and excluded (the roster)."""
    season = current_season(coach.club)
    roster_ids = list(TeamMembership.objects.filter(team=coach.team, season=season).values_list("member_id", flat=True)) if season else []
    excluded = Member.objects.filter(pk__in=roster_ids).order_by("last_name", "first_name")
    invited = eligible_roster_members(coach.club).exclude(pk__in=roster_ids).order_by("last_name", "first_name")
    return invited, excluded


def _scope_form(form, coach, *, team_pks):
    """The web coach form's scoping: the audience is the path's team, nothing wider."""
    form.fields["teams"].queryset = Team.objects.filter(pk__in=team_pks)
    form.fields["teams"].required = True
    del form.fields["groups"]
    if "club_wide" in form.fields:
        del form.fields["club_wide"]
    invited, excluded = _member_pools(coach)
    form.fields["invited_members"].queryset = invited
    form.fields["excluded_members"].queryset = excluded
    # An event the club made on the web may be a kind the coach screens don't offer: it can stay what it is.
    allowed = {*COACH_EVENT_KINDS, form.instance.kind} if form.instance.pk else set(COACH_EVENT_KINDS)
    form.fields["kind"].choices = [choice for choice in form.fields["kind"].choices if choice[0] in allowed]
    return form


# --- events ---------------------------------------------------------------------------------


def event_baseline(event) -> dict:
    """The POST data that would re-save ``event`` unchanged."""
    return {
        "kind": event.kind,
        "title": event.title,
        "start": event.start,
        "end": event.end,
        "gathering": event.gathering,
        "deadline": event.deadline,
        "location_id": event.location_id,
        "opponent_id": event.opponent_id,
        "competition": event.competition,
        "is_friendly": event.is_friendly,
        "external_game_id": event.external_game_id,
        "max_referees": event.max_referees,
        "max_officials": event.max_officials,
        "score_for": event.score_for,
        "score_against": event.score_against,
        "is_live": event.is_live,
        "invited_member_ids": list(event.invited_members.values_list("pk", flat=True)),
        "excluded_member_ids": list(event.excluded_members.values_list("pk", flat=True)),
    }


#: JSON key -> form field, for the keys that are not named the same.
SINGLE_CHOICES = {"location_id": "location", "opponent_id": "opponent"}
MULTIPLE_CHOICES = {"invited_member_ids": "invited_members", "excluded_member_ids": "excluded_members"}
CHECKBOXES = {"is_friendly", "is_live"}


def post_data(values: dict, team_pks) -> dict:
    """Event JSON values -> what the web event form would have POSTed (api.core.forms.post_data
    with the event form's field names)."""
    return core_post_data(values, multiple=MULTIPLE_CHOICES, single=SINGLE_CHOICES, checkboxes=CHECKBOXES, extra={"teams": [str(pk) for pk in team_pks]})


def build_event_form(coach, values: dict, *, instance, editing: bool, team_pks):
    data = post_data(values, team_pks)
    form = EventForm(data, club=coach.club, user=coach.user, editing=editing, instance=instance)
    return _scope_form(form, coach, team_pks=team_pks)


def save_event(form):
    if not form.is_valid():
        raise form_error(form, FIELD_ALIASES)
    return form.save()


def keep_other_teams(event, before_team_pks, managed_pks) -> None:
    """The web form locks ``teams`` to one team; a team the coach doesn't manage that was already on
    a multi-team event must survive an edit."""
    others = [pk for pk in before_team_pks if pk not in managed_pks]
    if others:
        event.teams.add(*others)


# --- series ---------------------------------------------------------------------------------


def series_ref(event) -> dict | None:
    if event.series_id is None:
        return None
    series = event.series
    parsed = parse_rrule(series.rrule) or {}
    return {"id": series.pk, "rrule": series.rrule, "frequency": parsed.get("frequency"), "interval": parsed.get("interval"), "weekdays": parsed.get("weekdays", []), "detached": event.detached}


def _minutes(value):
    return int(value.total_seconds() // 60) if value is not None else None


def series_out(series, *, created=0, updated=0) -> dict:
    parsed = parse_rrule(series.rrule) or {}
    return {
        "id": series.pk,
        "title": series.title,
        "kind": series.kind,
        "rrule": series.rrule,
        "frequency": parsed.get("frequency"),
        "interval": parsed.get("interval"),
        "weekdays": parsed.get("weekdays", []),
        "dtstart": series.dtstart,
        "until": series.until,
        "duration_minutes": _minutes(series.duration),
        "gathering_minutes_before": _minutes(series.gathering_offset),
        "deadline_minutes_before": _minutes(series.deadline_offset),
        "location_id": series.location_id,
        "opponent_id": series.opponent_id,
        "invited_member_ids": [member.pk for member in series.invited_members.all()],
        "excluded_member_ids": [member.pk for member in series.excluded_members.all()],
        "occurrences_created": created,
        "occurrences_updated": updated,
    }


def series_baseline(series) -> dict:
    parsed = parse_rrule(series.rrule) or {}
    return {
        "title": series.title,
        "kind": series.kind,
        "dtstart": series.dtstart,
        "until": series.until,
        "frequency": parsed.get("frequency"),
        "interval": parsed.get("interval"),
        "weekdays": parsed.get("weekdays", []),
        "duration_minutes": _minutes(series.duration),
        "gathering_minutes_before": _minutes(series.gathering_offset),
        "deadline_minutes_before": _minutes(series.deadline_offset),
        "location_id": series.location_id,
        "opponent_id": series.opponent_id,
        "invited_member_ids": list(series.invited_members.values_list("pk", flat=True)),
        "excluded_member_ids": list(series.excluded_members.values_list("pk", flat=True)),
    }


def build_series_form(coach, values: dict, *, instance, team_pks):
    data = post_data({key: value for key, value in values.items() if key not in ("duration_minutes", "weekdays")}, team_pks)
    data["weekdays"] = list(values.get("weekdays") or [])
    if values.get("duration_minutes") is not None:
        data["duration_hours"], data["duration_minutes"] = divmod(values["duration_minutes"], 60)
    form = EventSeriesForm(data, club=coach.club, user=coach.user, instance=instance)
    del form.fields["advanced_rrule"]
    return _scope_form(form, coach, team_pks=team_pks)


def save_series(form):
    if not form.is_valid():
        raise form_error(form, FIELD_ALIASES)
    return form.save()


@transaction.atomic
def split_series(event) -> EventSeries:
    """Cut ``event``'s series in two at ``event``: the old series stops just before it, a copy
    takes over from it, and this and every later occurrence move across."""
    old = event.series
    new = EventSeries.objects.get(pk=old.pk)
    new.pk = None
    new._state.adding = True
    new.dtstart = event.start
    new.generated_until = old.generated_until
    cut = event.start.isoformat()
    new.excluded_dates = [date for date in old.excluded_dates if date >= cut]
    old.excluded_dates = [date for date in old.excluded_dates if date < cut]
    old.until = event.start - datetime.timedelta(seconds=1)
    old.save(update_fields=["until", "excluded_dates"])
    new.save()
    for relation in ("teams", "groups", "invited_members", "excluded_members"):
        getattr(new, relation).set(getattr(old, relation).all())
    old.occurrences.filter(start__gte=event.start).update(series=new)
    return new


def apply_to_series(series, event, changed: set) -> int:
    """Copy the template fields in ``changed`` (JSON keys) from the just-saved ``event`` onto
    ``series`` and its upcoming, still-attached occurrences. Returns how many *other*
    occurrences changed."""
    if "title" in changed:
        series.title = event.title
    if "kind" in changed:
        series.kind = event.kind
    if "location_id" in changed:
        series.location_id = event.location_id
    if "opponent_id" in changed:
        series.opponent_id = event.opponent_id
    if "end" in changed:
        series.duration = event.end - event.start if event.end else None
    if "gathering" in changed:
        series.gathering_offset = event.start - event.gathering if event.gathering else None
    if "deadline" in changed:
        series.deadline_offset = event.start - event.deadline if event.deadline else None
    series.save()
    if "invited_member_ids" in changed:
        series.invited_members.set(event.invited_members.all())
    if "excluded_member_ids" in changed:
        series.excluded_members.set(event.excluded_members.all())
    return len([other for other in propagate_series(series) if other.pk != event.pk])


def managed_team_pks(coach) -> set:
    return set(teams_managed_by(coach.user, coach.club).values_list("pk", flat=True))
