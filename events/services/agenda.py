"""A family's agenda: every upcoming thing the managed people are invited to or signed up
for, merged into one chronological list. Shared by the member app's Calendar screen
(mobile.views.CalendarView) and the app API's ``GET /calendar``.

Each row is a dict with ``type`` and ``event`` plus, per type:

* ``rsvp``: ``member`` and ``attendance`` -- an invitation (an Attendance row);
* ``referee`` / ``official``: ``member`` and ``signup`` -- a referee/official invite
  (INVITED) or commitment (ACCEPTED); declined ones are left out, and so is any game that
  has already finished -- looking back you want the games you or your family *played*;
* ``blocked``: ``member`` and ``requirements`` -- an event the person would be invited to
  but for open onboarding requirements, shown so it doesn't just silently vanish.
"""

from django.db.models import Q
from django.utils import timezone

from events.models import Attendance, Event, OfficialSignup, RefereeSignup
from events.services.attendance import blocked_upcoming_events_for_members

#: ``kind`` filter values, mapped to the Event.EventKind they select. Referee/official
#: rows are games, so the training filter leaves them out.
KIND_FILTERS = {"game": Event.EventKind.GAME, "training": Event.EventKind.TRAINING}


def member_agenda(club, people, *, kind: str | None = None, officials_enabled: bool = False, start=None, end=None) -> list[dict]:
    """Rows for ``people`` from ``start`` (default: now) up to ``end`` (default: no end),
    soonest first. ``kind`` is a KIND_FILTERS key or None for everything."""
    people = list(people)
    if not people:
        return []
    start = start or timezone.now()
    kind_value = KIND_FILTERS.get(kind)

    def in_window(queryset):
        queryset = queryset.filter(event__club=club, event__cancelled=False, event__start__gte=start)
        return queryset.filter(event__start__lt=end) if end is not None else queryset

    now = timezone.now()

    def not_finished(queryset):
        # Refereeing a game is only of interest until it is over. A game with no end is judged
        # by its start (games get an assumed end on save, so that is the rare case).
        return queryset.filter(Q(event__end__gte=now) | Q(event__end__isnull=True, event__start__gte=now))

    related = ("event", "event__location", "event__opponent", "member")
    rows = []

    attendances = in_window(Attendance.objects.filter(member__in=people)).select_related(*related).prefetch_related("event__teams")
    if kind_value is not None:
        attendances = attendances.filter(event__kind=kind_value)
    rows += [{"type": "rsvp", "event": attendance.event, "member": attendance.member, "attendance": attendance} for attendance in attendances]

    if kind != "training":
        referee_signups = in_window(not_finished(RefereeSignup.objects.filter(member__in=people, status__in=[RefereeSignup.Status.INVITED, RefereeSignup.Status.ACCEPTED])))
        rows += [{"type": "referee", "event": signup.event, "member": signup.member, "signup": signup} for signup in referee_signups.select_related(*related).prefetch_related("event__teams")]
        if officials_enabled:
            official_signups = in_window(not_finished(OfficialSignup.objects.filter(member__in=people, status__in=[OfficialSignup.Status.INVITED, OfficialSignup.Status.ACCEPTED])))
            rows += [{"type": "official", "event": signup.event, "member": signup.member, "signup": signup} for signup in official_signups.select_related(*related).prefetch_related("event__teams")]

    blocked_by_person = blocked_upcoming_events_for_members(people, club)
    for person in people:
        for event, requirements in blocked_by_person[person.pk]:
            if kind_value is not None and event.kind != kind_value:
                continue
            if event.start < start or (end is not None and event.start >= end):
                continue
            rows.append({"type": "blocked", "event": event, "member": person, "requirements": requirements})

    rows.sort(key=lambda row: row["event"].start)
    return rows
