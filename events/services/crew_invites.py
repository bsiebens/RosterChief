"""Keeping referee/official invites in step with who is eligible *now*.

The app (the calendar API, the member app's Home) shows a person the games that need a
referee/official through their RefereeSignup/OfficialSignup rows. Those rows are created by
events.services.referees/officials.sync_*_invites, which only run when a game is saved or its
teams change (events/signals.py) -- so anything that changes *eligibility* later never reached
the games already on the calendar: a referee/official profile added or edited, a level's
teams changed, a team switched to club-managed, the officials feature switched on. The
management app, which works out "missing officials" live, saw those games; the app did not.

``resync_upcoming_invites`` closes that gap: for every upcoming game it creates the sign-up
rows that are missing (never touching existing ones, so nobody who answered is re-invited).
It runs when eligibility changes (events/signals.py) and hourly as a safety net
(``sync_crew_invites``). Many new invites for one person arrive as one notification, not one
per game.
"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from events.models import Event
from events.services.officials import sync_official_invites
from events.services.referees import sync_referee_invites
from notifications.services import notify_members


def _notify_new_invites(club, new_invites, *, single, plural, title):
    """One notification per member: the game itself when it is the only one, otherwise a
    count that opens the calendar."""
    for member, events in new_invites.items():
        if len(events) == 1:
            notify_members([member], club=club, title=title, body=single % {"event": events[0].title}, source=events[0])
        else:
            notify_members([member], club=club, title=title, body=ngettext(*plural, len(events)) % {"count": len(events)}, target_type="calendar")


def resync_upcoming_invites(club=None, *, now=None) -> dict:
    """Create the missing referee/official invites for every upcoming, not cancelled game
    (of ``club``, or every club). Returns ``{"referee": n, "official": n}``: how many new
    invites were created."""
    now = now or timezone.now()
    events = Event.objects.filter(start__gt=now, cancelled=False).select_related("club", "location").prefetch_related("teams")
    if club is not None:
        events = events.filter(club=club)

    new_referees: dict = {}  # club -> {member: [events]}
    new_officials: dict = {}
    for event in events:
        if not event.is_home_fixture:
            continue
        for member in sync_referee_invites(event, notify=False):
            new_referees.setdefault(event.club, {}).setdefault(member, []).append(event)
        for member in sync_official_invites(event, notify=False):
            new_officials.setdefault(event.club, {}).setdefault(member, []).append(event)

    referee_texts = {
        "title": _("Referee needed"),
        "single": _("%(event)s needs a referee -- can you take it?"),
        "plural": ("%(count)d game needs a referee -- can you take it?", "%(count)d games need a referee -- can you take one?"),
    }
    official_texts = {
        "title": _("Official needed"),
        "single": _("%(event)s needs a match official -- can you take it?"),
        "plural": ("%(count)d game needs a match official -- can you take it?", "%(count)d games need a match official -- can you take one?"),
    }
    for event_club, invites in new_referees.items():
        _notify_new_invites(event_club, invites, **referee_texts)
    for event_club, invites in new_officials.items():
        _notify_new_invites(event_club, invites, **official_texts)

    return {"referee": sum(len(events) for invites in new_referees.values() for events in invites.values()), "official": sum(len(events) for invites in new_officials.values() for events in invites.values())}
