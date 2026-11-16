"""Telling the invited people about new, cancelled and rescheduled events (inbox + push, category "event").

The audience is ``attendance.effective_members(event)``; only members with a login hear anything, never the actor
themself, and nothing is sent for an event that has already started. One message per person per call: a new series
sends ONE summary ("New recurring event") instead of one per occurrence. The calendar API calls these after the
service succeeded, inside ``transaction.on_commit`` so a rolled-back write tells nobody."""

from __future__ import annotations

from django.utils import timezone

from events.models import Event, EventSeries
from events.services.attendance import current_club, effective_members

CATEGORY = "event"


def _href(event: Event) -> str:
    return f"/calendar/events/{event.pk}"


def describe_when_where(event: Event) -> str:
    """E.g. "Sat 14 Nov 2026, 10:00-12:00 at Sporthal (Gent)"."""
    start = timezone.localtime(event.start)
    text = f"{start:%a %d %b %Y}, {start:%H:%M}"
    if event.end is not None:
        end = timezone.localtime(event.end)
        text += f"-{end:%H:%M}" if end.date() == start.date() else f" - {end:%a %d %b %Y %H:%M}"
    if event.location_id is not None:
        location = event.location
        text += f" at {location.name}" + (f" ({location.city})" if location.city else "")
    return text


def _send(event: Event, *, title: str, body: str, actor=None, href: str | None = None) -> int:
    """Notify the event's audience with a login (minus the actor). Returns how many accounts were told."""
    club = current_club()
    if club is None:
        return 0
    from notifications import inbox

    from members.services import recipients

    actor_id = getattr(actor, "pk", None)
    told = 0
    seen = set()  # (account, child it is for): a parent is told once per child, an adult once
    for member in effective_members(event).select_related("user"):
        for user, child in recipients.recipients(member):
            key = (user.pk, child.pk if child is not None else None)
            if user.pk == actor_id or key in seen:
                continue
            seen.add(key)
            inbox.notify(user, club, title=recipients.on_behalf_title(title, child) if child is not None else title, body=body, kind=inbox.Kind.INFO, href=href or _href(event), push=True, category=CATEGORY)
            told += 1
    return told


def _upcoming(event: Event) -> bool:
    return event.start > timezone.now()


def notify_event_created(event: Event, *, actor=None) -> int:
    """A one-off event was created: "New event: <title>" with its date, time and location."""
    event = Event.objects.select_related("location").get(pk=event.pk)
    if event.cancelled or not _upcoming(event):
        return 0
    return _send(event, title=f"New event: {event.title}", body=describe_when_where(event), actor=actor)


def notify_series_created(series: EventSeries, *, actor=None) -> int:
    """A recurring series was created: ONE summary per person (its rule and the first upcoming occurrence)."""
    from events.services.recurrence import describe_recurrence

    first = series.occurrences.filter(start__gt=timezone.now(), cancelled=False).select_related("location").order_by("start").first()
    if first is None:
        return 0
    body = f"{describe_recurrence(series)}. First: {describe_when_where(first)}."
    return _send(first, title=f"New recurring event: {series.title}", body=body, actor=actor)


CHANGES = {
    "cancelled": ("Cancelled: {title}", "{when} is cancelled."),
    "uncancelled": ("Back on: {title}", "{when} is going ahead after all."),
    "rescheduled": ("Rescheduled: {title}", "Now {when}."),
}


def notify_event_changed(event: Event, what: str, *, actor=None, series_wide: bool = False) -> int:
    """``what`` is "cancelled", "uncancelled" or "rescheduled". With ``series_wide`` the text says the change applies to
    this and the later occurrences as well (still one message per person)."""
    if what not in CHANGES:
        raise ValueError(f"Unknown change: {what}")
    event = Event.objects.select_related("location").get(pk=event.pk)
    if not _upcoming(event):
        return 0
    title, body = CHANGES[what]
    body = body.format(when=describe_when_where(event))
    if series_wide:
        body += " This applies to the later occurrences of this series too."
    return _send(event, title=title.format(title=event.title), body=body, actor=actor)
