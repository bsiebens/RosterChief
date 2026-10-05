"""Model objects -> the JSON shapes in schemas.py. Data only (API.md §6): stored enum
values, ids and timestamps; no CSS classes, reversed web URLs or display grouping."""

import hashlib
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import get_language

from events.models import Attendance, Event
from formbuilder.models import FormSend
from news.models import News
from news.services import localized_news, render_body_excerpt

from .schemas import CURRENCY

#: Words in a news card's plain-text excerpt.
EXCERPT_WORDS = 30


def money(amount) -> dict:
    return {"amount": f"{Decimal(amount):.2f}", "currency": CURRENCY}


def team_ref(team) -> dict:
    return {"id": team.pk, "name": team.name, "short_name": team.short_name}


def season_out(season) -> dict:
    return {"id": season.pk, "start_date": season.start_date, "end_date": season.end_date}


def game_status(event: Event) -> str | None:
    """`cancelled`, `live`, `finished` or `scheduled` for a game; None for any other kind.

    There is no stored status, so it is derived: the live flag wins, then the poller having seen
    the game come off live, a final score on a game that has started, or its (assumed) end
    having passed."""
    if event.kind != Event.EventKind.GAME:
        return None
    if event.cancelled:
        return "cancelled"
    if event.is_live:
        return "live"

    now = timezone.now()
    has_score = event.score_for is not None and event.score_against is not None
    if event.live_score_polling_done_at or (has_score and event.start <= now) or (event.end is not None and event.end <= now):
        return "finished"

    return "scheduled"


def event_out(event: Event) -> dict:
    location = event.location
    return {
        "id": event.pk,
        "title": event.title,
        "kind": event.kind,
        "start": event.start,
        "end": event.end,
        "gathering": event.gathering,
        "deadline": event.deadline,
        "cancelled": event.cancelled,
        "location": {"name": location.name, "address": location.address, "city": location.city, "zip_code": location.zip_code, "is_home": location.is_home} if location else None,
        "opponent": event.opponent.name if event.opponent_id else None,
        "is_home_game": event.is_home_game,
        "teams": [team_ref(team) for team in event.teams.all()],
        "competition": event.competition,
        "score_for": event.score_for,
        "score_against": event.score_against,
        "is_live": event.is_live,
        "game_status": game_status(event),
    }


def attendance_out(attendance: Attendance) -> dict:
    return {"member_id": attendance.member_id, "status": attendance.status, "note": attendance.note}


def signup_out(signup) -> dict:
    return {"id": signup.pk, "member_id": signup.member_id, "status": signup.status}


def task_out(task) -> dict:
    return {
        "id": task.pk,
        "title": task.title,
        "description": task.description,
        "needed_quantity": task.needed_quantity,
        "claimed_by": [str(label) for label in task.claim_labels],
        "is_full": task.is_full,
        "claimed_by_me": task.claimed_by_me,
    }


def _absolute(request, url: str | None) -> str | None:
    return request.build_absolute_uri(url) if url else None


def news_summary(request, item: News) -> dict:
    title, body, _language = localized_news(item, get_language())
    photo = item.main_photo
    return {
        "id": item.pk,
        "slug": item.slug,
        "title": title,
        "author": {"first_name": item.created_by.first_name, "last_name": item.created_by.last_name} if item.created_by_id else None,
        "excerpt": render_body_excerpt(body, words=EXCERPT_WORDS),
        "published_at": item.published_at,
        "image_url": _absolute(request, photo.image.url) if photo else None,
        "teams": [team_ref(team) for team in item.teams.all()],
    }


def notification_source(source) -> dict | None:
    if isinstance(source, News):
        return {"type": "news", "id": source.pk, "slug": source.slug}
    if isinstance(source, Event):
        return {"type": "event", "id": source.pk, "slug": None}
    if isinstance(source, FormSend):
        return {"type": "form_send", "id": source.pk, "slug": None}
    return None


def notification_out(notification) -> dict:
    return {
        "id": notification.pk,
        "member_id": notification.member_id,
        "title": notification.title,
        "body": notification.body,
        "created": notification.created,
        "read_at": notification.read_at,
        "source": notification_source(notification.source),
    }


def member_photo_url(request, member) -> str | None:
    """The app API's own photo endpoint (token-authenticated), versioned like
    Member.photo_url so a replaced photo is never served from the app's cache."""
    if not member.photo:
        return None
    version = hashlib.sha256(member.photo.name.encode()).hexdigest()[:12]
    return request.build_absolute_uri(f"/api/app/v1/members/{member.pk}/photo?v={version}")
