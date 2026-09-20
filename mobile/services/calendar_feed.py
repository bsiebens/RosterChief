"""Builds the .ics feed mobile.views.CalendarFeedView serves -- see that
view's own docstring for the shape (one combined feed per account, every
managed person's events, regardless of RSVP status).
"""

import datetime

from django.utils import timezone
from django.utils.translation import gettext as _
from icalendar import Calendar
from icalendar import Event as ICalEvent

from events.models import Attendance

#: Non-game events don't always have an explicit `end` -- same assumed-duration
#: read-time fallback events.services.calendar._local_span()/events.models.
#: ASSUMED_EVENT_DURATION already use for display purposes.
_ASSUMED_DURATION = datetime.timedelta(hours=2)


def _utc(value):
    return timezone.localtime(value).astimezone(datetime.UTC)


def build_feed(club, people) -> bytes:
    """A combined VCALENDAR of every one of ``people``'s Attendance rows in
    ``club``, regardless of RSVP status -- this mirrors what the in-app
    Calendar screen shows, minus its 2-week window: a synced calendar app is
    exactly where someone wants the *whole* season visible, not a
    deliberately-short mobile agenda. Still-in-progress events are kept
    (filtered on effective end, not start) so one doesn't vanish from a
    subscriber's calendar partway through. A cancelled event stays in the
    feed with STATUS:CANCELLED rather than simply disappearing from it, so a
    calendar app that already synced it removes it properly on next refresh.
    """
    calendar = Calendar()
    calendar.add("prodid", "-//RosterChief//Member calendar feed//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", _("%(club)s — RosterChief") % {"club": club.name})

    now = timezone.now()
    show_member_name = len(people) > 1
    dtstamp = _utc(now)

    attendances = Attendance.objects.filter(member__in=people, event__club=club, event__start__gte=now - datetime.timedelta(days=1)).select_related("event", "event__location", "member").order_by("event__start")

    for attendance in attendances:
        event = attendance.event
        end = event.end or (event.start + _ASSUMED_DURATION)
        if end < now:
            continue

        # A synced calendar invite is exactly where a gathering time matters
        # most -- it's the one place someone actually checks "when do I need
        # to leave", and today it only ever showed up as a line of free text
        # in the description, which most calendar apps don't surface without
        # opening the event. Using it as DTSTART instead blocks the whole
        # gathering-to-end span off on the calendar itself; DTEND stays the
        # event's own end either way, so the invite's *length* only ever
        # grows to cover the extra lead time, never shrinks.
        start = event.gathering or event.start

        component = ICalEvent()
        component.add("uid", f"attendance-{attendance.pk}@rosterchief.app")
        component.add("dtstamp", dtstamp)
        component.add("dtstart", _utc(start))
        component.add("dtend", _utc(end))
        component.add("summary", _("%(title)s — %(name)s") % {"title": event.title, "name": attendance.member.first_name} if show_member_name else event.title)
        component.add("status", "CANCELLED" if event.cancelled else "CONFIRMED")

        if event.location:
            # Address only, deliberately without event.location.name -- a bare
            # street/zip/city string is what lets a calendar app (Apple Calendar,
            # Google Calendar, ...) actually geocode/map it, where a name-prefixed
            # string often doesn't resolve to anything.
            address = ", ".join(part for part in [event.location.address, f"{event.location.zip_code} {event.location.city}".strip()] if part)
            component.add("location", address)

        description_lines = [_("RSVP: %(status)s") % {"status": attendance.get_status_display()}]
        if event.gathering:
            # The invite's own DTSTART is the gathering time now -- what's
            # otherwise not obvious from the block itself is when the event
            # actually starts, not when to meet.
            description_lines.append(_("Starts: %(time)s") % {"time": timezone.localtime(event.start).strftime("%H:%M")})
        component.add("description", "\n".join(description_lines))

        calendar.add_component(component)

    return calendar.to_ical()
