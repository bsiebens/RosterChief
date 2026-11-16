"""The personal calendar link: one secret iCalendar feed per account (``CalendarFeed``), served without login at
``GET /api/v1/public/calendar/<token>.ics``. It holds the events of the signed-in person and of the children they manage (the same people
the member app shows). The token is the opt-in: switching the link off removes it, replacing it makes the old link stop working at once.
Every change is audited."""

import datetime
import secrets

from django.db import transaction
from django.utils import timezone

from events.models import ASSUMED_EVENT_DURATION, Attendance, CalendarFeed
from members.models import Member
from members.services import audit

BACK = datetime.timedelta(days=30)
AHEAD = datetime.timedelta(days=365)
ALARM = datetime.timedelta(minutes=60)
PRODID = "-//RosterChief//Calendar feed//EN"


def get(user) -> CalendarFeed | None:
    return CalendarFeed.objects.filter(user=user).first()


@transaction.atomic
def enable(actor, member: Member, *, rotate: bool = False) -> CalendarFeed:
    """The account's feed: created when missing, its token replaced with ``rotate``."""
    feed = get(actor)
    if feed is not None and not rotate:
        return feed
    token = secrets.token_urlsafe(24)
    if feed is None:
        feed = CalendarFeed.objects.create(user=actor, token=token)
        audit.record(actor, "calendar_feed.enabled", member=member, target=feed)
    else:
        feed.token = token
        feed.save(update_fields=["token", "modified"])
        audit.record(actor, "calendar_feed.rotated", member=member, target=feed)
    return feed


@transaction.atomic
def disable(actor, member: Member) -> None:
    feed = get(actor)
    if feed is not None:
        label = str(feed)
        feed.delete()
        audit.record(actor, "calendar_feed.disabled", member=member, target_label=label)


def feed_for_token(token: str) -> CalendarFeed | None:
    return CalendarFeed.objects.select_related("user").filter(token=token).first() if token else None


# -- the iCalendar document --


def _escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines are at most 75 octets; longer ones continue on the next line after a space (never inside a UTF-8 character)."""
    data = line.encode()
    if len(data) <= 75:
        return line
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode())
        if size + width > (75 if not parts else 74):
            parts.append(current)
            current, size = "", 0
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def _utc(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")


def _location(location) -> str:
    if location is None:
        return ""
    place = ", ".join(part for part in (location.address, " ".join(part for part in (location.zip_code, location.city) if part)) if part)
    return f"{location.name}, {place}" if place else location.name


def _description(row, *, host: str) -> str:
    event = row.event
    lines = []
    if event.gathering:
        lines.append(f"Gathering: {timezone.localtime(event.gathering):%H:%M}")
    lines.append(f"Answer: {row.get_status_display()}")
    if event.opponent_id:
        lines.append(f"Opponent: {event.opponent.name}")
    if event.cancelled:
        lines.append("This event is cancelled.")
    notes = (getattr(event, "notes", "") or "").strip()
    if notes:
        lines += ["", notes]
    return "\n".join(lines)


def _vevent(row, viewer: Member, host: str) -> list[str]:
    event = row.event
    end = event.end if event.end and event.end > event.start else event.start + ASSUMED_EVENT_DURATION
    title = event.title if row.member_id == viewer.pk else f"{event.title} ({row.member.first_name})"
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event.pk}-{row.member_id}@{host}",
        f"DTSTAMP:{_utc(event.modified)}",
        f"LAST-MODIFIED:{_utc(event.modified)}",
        f"DTSTART:{_utc(event.start)}",
        f"DTEND:{_utc(end)}",
        f"SUMMARY:{_escape(title)}",
    ]
    if place := _location(event.location):
        lines.append(f"LOCATION:{_escape(place)}")
    lines.append(f"DESCRIPTION:{_escape(_description(row, host=host))}")
    lines.append(f"STATUS:{'CANCELLED' if event.cancelled else 'CONFIRMED'}")
    if not event.cancelled:
        before = ALARM + (event.start - event.gathering if event.gathering and event.gathering < event.start else datetime.timedelta())
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(event.title)}", f"TRIGGER:-PT{int(before.total_seconds() // 60)}M", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def build_ics(viewer: Member, people: dict, *, club_name: str, host: str, now: datetime.datetime | None = None) -> str:
    """The calendar of ``viewer`` and the managed children in ``people`` (``{member id: Member}``, viewer included)."""
    now = now or timezone.now()
    rows = (
        Attendance.objects.filter(member_id__in=people, event__start__range=(now - BACK, now + AHEAD))
        .select_related("event__location", "event__opponent", "member")
        .order_by("event__start", "member_id", "id")
    )
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_escape(club_name)}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for row in rows:
        lines += _vevent(row, viewer, host)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
