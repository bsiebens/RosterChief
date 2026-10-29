"""What the assignment panels of a game show: who is assigned, who could be, who signed up.

One definition for the /manage event page (management.views.EventDetailView) and the management
API (api.panel.routers.event_officials), so the two cannot drift. Referees and officials are
deliberately separate tables (see events.services.officials), so each has its own function; they
share ``signup_groups`` and ``_annotate_conflicts``.
"""

from events.models import OfficialSignup, RefereeSignup
from events.services.officials import eligible_officials, needs_official_management
from events.services.referees import conflicting_events_for_members, eligible_referees, needs_referee_management


def signup_groups(signups, model):
    """Splits a game's not-yet-confirmed referee/official sign-ups for the assignment panel:
    ``interested`` (signed up, waiting for staff to confirm), ``waiting`` (invited, no
    response yet) and ``declined``. Confirmed ones are the EventReferee/EventOfficial rows
    the panel lists on its own."""
    groups = {"interested": [], "waiting": [], "declined": []}
    for signup in signups:
        key = {model.Status.ACCEPTED: "interested", model.Status.INVITED: "waiting", model.Status.DECLINED: "declined"}.get(signup.status)
        if key:
            groups[key].append(signup)
    return groups


def _annotate_conflicts(candidates, event) -> list:
    """Flags each candidate with ``has_conflict`` / ``conflict_titles`` (informational, never a block)."""
    conflicts_by_member = conflicting_events_for_members(candidates, event)
    for candidate in candidates:
        conflicts = conflicts_by_member[candidate.pk]
        candidate.has_conflict = bool(conflicts)
        candidate.conflict_titles = ", ".join(conflict.title for conflict in conflicts)
    return candidates


def referee_panel(event, *, can_manage: bool) -> dict:
    """``needed`` (a game the club arranges referees for), the ``assigned`` rows, ``full``, and, for
    someone who can manage referees, the eligible ``candidates`` (while there is room) and the
    not-yet-confirmed ``signups`` (``signup_groups``)."""
    panel = {"needed": needs_referee_management(event), "assigned": [], "full": False, "candidates": [], "signups": {}}
    if not panel["needed"]:
        return panel
    panel["assigned"] = list(event.referees.select_related("member", "assigned_by").order_by("member__last_name", "member__first_name"))
    panel["full"] = len(panel["assigned"]) >= event.max_referees
    if can_manage and not panel["full"]:
        panel["candidates"] = _annotate_conflicts(list(eligible_referees(event, include_expired=True)), event)
    if can_manage:
        panel["signups"] = signup_groups(event.referee_signups.exclude(status=RefereeSignup.Status.CONFIRMED).select_related("member"), RefereeSignup)
    return panel


def official_panel(event, *, can_manage: bool) -> dict:
    """The officials counterpart of ``referee_panel`` (the caller checks the "officials" feature)."""
    panel = {"needed": needs_official_management(event), "assigned": [], "full": False, "candidates": [], "signups": {}}
    if not panel["needed"]:
        return panel
    panel["assigned"] = list(event.officials.select_related("member", "assigned_by").order_by("member__last_name", "member__first_name"))
    panel["full"] = len(panel["assigned"]) >= event.max_officials
    if can_manage and not panel["full"]:
        panel["candidates"] = _annotate_conflicts(list(eligible_officials(event)), event)
    if can_manage:
        panel["signups"] = signup_groups(event.official_signups.exclude(status=OfficialSignup.Status.CONFIRMED).select_related("member"), OfficialSignup)
    return panel
