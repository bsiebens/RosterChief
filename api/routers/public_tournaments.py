"""``/public/tournaments``: a club's public tournament pages and the team registration, no login. Unknown club host, the platform host and
clubs without the ``tournaments`` feature answer 404. Listing shows published / in progress / completed tournaments; a tournament's own
pages work for every status (a draft's link works, it is just not listed). A visiting team registers with ``POST /{slug}/registrations``
(only while the tournament is draft or published, 422 ``closed`` when entries are closed or full) and keeps the returned ``status_token``:
``GET /{slug}/registrations/{token}`` shows the decision and amounts, ``PUT .../players`` changes the player list while pending, and the
live roster of an approved team until the tournament starts. Abuse protection as in ``public_registration``: submissions are counted per
client address and email (429 + ``Retry-After``), a filled ``website`` field is a honeypot (pretend success, save nothing)."""

import secrets
from decimal import Decimal
from io import BytesIO
from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from ninja import Field, File, Router, Schema, UploadedFile
from ninja.errors import HttpError

from api.routers import tournaments_common as c
from api.routers.public_registration import _address, _count, _too_many
from api.routers.tournaments_competition import stages_doc, standings_doc
from api.routers.tournaments_roster import check_birth_date, check_position, lunch_days_for
from api.routers.tournaments_timeline import chronological, event_out
from api.routers.web_push import _valid_keys
from api.schemas_tournaments import PlayerRowIn, PlayersIn, PublicRegistrationIn
from members.services.errors import Invalid
from news.services import render_body_html
from notifications import gateways
from notifications.models import PushSettings
from sponsoring.models import Sponsor
from tournaments.models import Match, ScheduleBlock, Tournament, TournamentFollower, TournamentMessage, TournamentRegistration, TournamentRegistrationPlayer, TournamentRosterEntry, TournamentRosterRole, TournamentTeam
from tournaments.services import notifications, team_access
from tournaments.services.messages import message_out, notify_organisers, thread_for_team, unread_for_team
from tournaments.sports import is_ice_hockey

router = Router(auth=None)

LISTED = (Tournament.Status.PUBLISHED, Tournament.Status.IN_PROGRESS, Tournament.Status.COMPLETED)
OPEN_FOR_REGISTRATION = (Tournament.Status.DRAFT, Tournament.Status.PUBLISHED)
WINDOW = 600
SUBMIT_LIMIT = 10
PER_EMAIL, EMAIL_WINDOW = 5, 3600
MAX_LOGO_BYTES = 2 * 1024 * 1024


def _tournament(request, slug) -> Tournament:
    c.public_gate(request)
    return c.get_or_404(Tournament.objects.select_related("season", "home_team"), slug=slug)


def _money(value):
    return None if value is None else f"{value:.2f}"


def _address_line(location) -> str:
    if location is None:
        return ""
    return ", ".join(part for part in (location.address, f"{location.zip_code} {location.city}".strip()) if part)


def maps_url(location) -> str | None:
    """A link that opens the venue in a maps view: its coordinates when the club has them, else a search for name and address."""
    if location is None:
        return None
    if location.latitude is not None and location.longitude is not None:
        query = f"{location.latitude},{location.longitude}"
    else:
        query = ", ".join(part for part in (location.name, location.address, f"{location.zip_code} {location.city}".strip()) if part)
    return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"


def item(t: Tournament) -> dict:
    location = t.location
    return {
        "slug": t.slug, "year": t.start_date.year, "path": t.public_key, "name": t.name, "start_date": t.start_date, "end_date": t.end_date, "days": c.days_out(t), "status": t.status, "location": location.name if location else None,
        "location_address": _address_line(location), "maps_url": maps_url(location),
        "latitude": location.latitude if location else None, "longitude": location.longitude if location else None,
        "accepts_registrations": t.accepts_registrations and t.status in OPEN_FOR_REGISTRATION,
    }  # fmt: skip


def _sponsors(t: Tournament) -> list[dict]:
    """The sponsors for the public page: the tournament's own, then the club's active ones when the tournament uses them (same name once)."""
    out = [{"name": s.name, "url": s.url, "logo_url": c.url(s.logo) if s.logo else None} for s in t.sponsors.all()]
    if t.use_club_sponsors:
        seen = {s["name"].lower() for s in out}
        out += [{"name": s.name, "url": s.url, "logo_url": c.url(s.logo) if s.logo else None} for s in Sponsor.active() if s.name.lower() not in seen]
    return out


def _block(b: ScheduleBlock, tournament: Tournament) -> dict:
    """A block for the public pages: a lunch shows its real sitting (not the padded window the scheduler keeps games out of)."""
    lunch = b.kind == ScheduleBlock.Kind.LUNCH
    return {
        "id": b.pk, "title": b.title, "kind": b.kind, "starts_at": b.lunch_start if lunch and b.lunch_start else b.starts_at, "ends_at": b.lunch_end if lunch and b.lunch_end else b.ends_at,
        "location": b.location or (tournament.lunch_location if lunch else "") or (b.field.name if b.field_id else ""),
        "team_ids": [x.pk for x in b.teams.all()],
    }  # fmt: skip


def _blocks(t: Tournament) -> list[dict]:
    return [_block(b, t) for b in t.schedule_blocks.select_related("field").prefetch_related("teams").order_by("starts_at")]


def _match_docs(qs) -> list[dict]:
    qs = list(qs)
    c.prime_matches(qs)
    return [c.match_out(m) for m in qs]


_RELATED = ("stage", "field", "home_team__team", "away_team__team", "home_source_match", "away_source_match", "home_source_group", "away_source_group")


def _live_matches(t) -> list[dict]:
    return _match_docs(Match.objects.filter(stage__tournament=t, status=Match.Status.IN_PROGRESS).select_related(*_RELATED).order_by("scheduled_start"))


def _recent_matches(t, limit=6) -> list[dict]:
    return _match_docs(Match.objects.filter(stage__tournament=t, status=Match.Status.COMPLETE, leg_of__isnull=True).select_related(*_RELATED).order_by("-scheduled_start")[:limit])


def _upcoming_matches(t, limit=6):
    qs = (
        Match.objects.filter(stage__tournament=t, status=Match.Status.SCHEDULED).exclude(scheduled_start__isnull=True)
        .select_related("stage", "field", "home_team__team", "away_team__team", "home_source_match", "away_source_match", "home_source_group", "away_source_group")
        .order_by("scheduled_start")[:limit]
    )  # fmt: skip
    qs = list(qs)
    c.prime_matches(qs)
    return [c.match_out(m) for m in qs]


@router.get("", summary="Published, running and finished tournaments of the club", operation_id="public_tournaments_list")
def list_tournaments(request):
    c.public_gate(request)
    rows = list(Tournament.objects.filter(status__in=LISTED))
    return {"upcoming": [item(t) for t in rows if t.status != Tournament.Status.COMPLETED], "past": [item(t) for t in rows if t.status == Tournament.Status.COMPLETED]}


@router.get("/{slug}", summary="A tournament: details, next matches and the current standings", operation_id="public_tournaments_detail")
def detail(request, slug: str):
    t = _tournament(request, slug)
    return {
        **item(t),
        "description_html": render_body_html(t.description) if t.description else "", "contact_email": t.contact_email, "earliest_birth_year": t.earliest_birth_year, "latest_birth_year": t.latest_birth_year,
        "entry_fee_amount": _money(t.entry_fee_amount), "deposit_amount": _money(t.deposit_amount), "lunch_mode": t.lunch_mode, "lunch_price_per_day": _money(t.lunch_price_per_day),
        "ice_hockey": is_ice_hockey(getattr(connection.tenant, "sport", None)), "dates": t.date_range(), "registration_open": t.registration_open, "stages": [c.stage_summary(s) for s in t.stages.order_by("sequence")],
        "upcoming_matches": _upcoming_matches(t), "live_matches": _live_matches(t), "recent_matches": _recent_matches(t), **standings_doc(t),
        "rules_html": render_body_html(t.rules_markdown) if t.rules_markdown else "", "rules_file_url": c.url(t.rules_file) if t.rules_file else None, "sponsors": _sponsors(t),
    }  # fmt: skip


@router.get("/{slug}/schedule", summary="Every match by stage and round", operation_id="public_tournaments_schedule")
def schedule(request, slug: str):
    t = _tournament(request, slug)
    return {"tournament": item(t), "stages": stages_doc(t), "blocks": _blocks(t)}


@router.get("/{slug}/standings", summary="Standings and brackets", operation_id="public_tournaments_standings")
def standings(request, slug: str):
    t = _tournament(request, slug)
    return {"tournament": item(t), **standings_doc(t)}


@router.get("/{slug}/teams", summary="The teams taking part", operation_id="public_tournaments_teams")
def teams(request, slug: str):
    t = _tournament(request, slug)
    rows = list(t.teams.filter(withdrawn_at__isnull=True).select_related("team").prefetch_related("dressing_room_assignments__dressing_room"))
    rows.sort(key=lambda team: team.display_name.lower())
    return {"tournament": item(t), "teams": [{**c.team_ref(team), "dressing_rooms": [a.dressing_room.name for a in team.dressing_room_assignments.all()]} for team in rows]}


@router.get("/{slug}/teams/{team_slug}", summary="One team: its matches, roster (no personal details) and dressing rooms", operation_id="public_tournaments_team")
def team(request, slug: str, team_slug: str):
    t = _tournament(request, slug)
    team = c.get_or_404(TournamentTeam.objects.select_related("team"), tournament=t, slug=team_slug)
    entries = team.roster_entries.select_related("member").order_by("role", "jersey_number")
    matches = (
        Match.objects.filter(Q(home_team=team) | Q(away_team=team))
        .select_related("stage", "group", "field", "home_team__team", "away_team__team", "home_source_match", "away_source_match", "home_source_group", "away_source_group")
        .order_by("stage__sequence", "round_number", "sequence")
    )  # fmt: skip
    matches = list(matches)
    c.prime_matches(matches)
    return {
        "tournament": item(t), "team": {**c.team_ref(team), "withdrawn": team.withdrawn_at is not None},
        "roster": [{"name": e.display_name, "role": e.role, "position_label": e.position_label, "jersey_number": e.jersey_number} for e in entries],
        "dressing_rooms": [{"name": a.dressing_room.name, "assigned_from": a.assigned_from, "assigned_until": a.assigned_until} for a in team.dressing_room_assignments.select_related("dressing_room")],
        "matches": [c.match_out(m) for m in matches],
        "blocks": [b for b in _blocks(t) if team.pk in b["team_ids"]],
    }  # fmt: skip


@router.get("/{slug}/matches/{match_id}", summary="One game with its timeline; a game in periods comes back as all its periods", operation_id="public_tournaments_match")
def game(request, slug: str, match_id: str):
    t = _tournament(request, slug)
    chosen = c.get_or_404(Match.objects.select_related("stage"), pk=match_id, stage__tournament=t)
    tie_id = chosen.leg_of_id or chosen.pk  # the second leg is the tie's own match, the first leg points at it
    legs = list(
        Match.objects.filter(Q(pk=tie_id) | Q(leg_of_id=tie_id)).select_related("stage", "group", "field", "home_team__team", "away_team__team", "home_source_match", "away_source_match", "home_source_group", "away_source_group").order_by("leg_of_id")
    )  # the first leg has leg_of set, so it sorts after NULL; reorder by the leg number below
    c.prime_matches(legs)
    docs = sorted((c.match_out(m) for m in legs), key=lambda d: d.get("leg") or 1)
    by_id = {m.pk: m for m in legs}
    return {
        "tournament": item(t), "ice_hockey": is_ice_hockey(getattr(connection.tenant, "sport", None)), "clock_direction": t.clock_direction,
        "periods": [{"match": d, "events": [event_out(e) for e in chronological(by_id[d["id"]])]} for d in docs],
    }  # fmt: skip


# --- push alerts -----------------------------------------------------------------------------------------------------------


class AlertKeys(Schema):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class AlertIn(Schema):
    endpoint: str = Field(min_length=1, max_length=500)
    keys: AlertKeys
    team_slugs: list[str] = Field(default_factory=list, max_length=64)
    prefs: dict[str, bool] = Field(default_factory=dict)
    lang: str = Field(default="en", max_length=5)


class AlertEndpoint(Schema):
    endpoint: str = Field(min_length=1, max_length=500)


def _alerts_key() -> str | None:
    return (gateways.webpush_public_key() or None) if PushSettings.get().webpush_enabled else None


def _follower_doc(f: TournamentFollower | None) -> dict:
    if f is None:
        return {"subscribed": False, "team_slugs": [], "prefs": dict.fromkeys(TournamentFollower.PREFS, True)}
    return {"subscribed": True, "team_slugs": [t.slug for t in f.teams.all()], "prefs": {k: f.wants(k) for k in TournamentFollower.PREFS}}


@router.get("/{slug}/alerts/config", summary="Whether push alerts are available, and the key a browser subscribes with", operation_id="public_tournaments_alerts_config")
def alerts_config(request, slug: str):
    _tournament(request, slug)
    return {"public_key": _alerts_key()}


@router.post("/{slug}/alerts/status", summary="What this browser follows", operation_id="public_tournaments_alerts_status")
def alerts_status(request, slug: str, payload: AlertEndpoint):
    t = _tournament(request, slug)
    return _follower_doc(t.followers.filter(endpoint=payload.endpoint).prefetch_related("teams").first())


@router.put("/{slug}/alerts", summary="Follow teams of a tournament with this browser (creates or replaces the subscription)", operation_id="public_tournaments_alerts_follow")
def alerts_follow(request, slug: str, payload: AlertIn):
    t = _tournament(request, slug)
    if not _alerts_key():
        raise HttpError(503, "Push alerts are unavailable.")
    if not gateways.is_allowed_push_endpoint(payload.endpoint) or not _valid_keys(payload.keys):
        raise HttpError(422, "Invalid push subscription.")
    teams = list(t.teams.filter(slug__in=payload.team_slugs, withdrawn_at__isnull=True))
    prefs = {k: bool(payload.prefs.get(k, True)) for k in TournamentFollower.PREFS}
    lang = payload.lang if payload.lang in ("en", "nl", "fr") else "en"
    follower, _created = TournamentFollower.objects.update_or_create(tournament=t, endpoint=payload.endpoint, defaults={"p256dh": payload.keys.p256dh, "auth": payload.keys.auth, "prefs": prefs, "lang": lang})
    follower.teams.set(teams)
    return _follower_doc(follower)


@router.post("/{slug}/alerts/unfollow", summary="Stop all alerts of this tournament for this browser", operation_id="public_tournaments_alerts_unfollow", response={204: None})
def alerts_unfollow(request, slug: str, payload: AlertEndpoint):
    t = _tournament(request, slug)
    t.followers.filter(endpoint=payload.endpoint).delete()
    return 204, None


# --- registration ----------------------------------------------------------------------------------------------------------


def _throttled(request, email=""):
    if not _count(f"tournament-registration:{request.tenant.schema_name}:{_address(request)}", SUBMIT_LIMIT, WINDOW):
        return _too_many(WINDOW)
    if email and not _count(f"tournament-registration:email:{request.tenant.schema_name}:{email.lower()}", PER_EMAIL, EMAIL_WINDOW):
        return _too_many(EMAIL_WINDOW)
    return None


def _people(t: Tournament, rows: list[PlayerRowIn]) -> list[dict]:
    """The submitted rows as model fields. A blank row is skipped; a person needs a first and last name; a staff row has no date of birth;
    lunch days follow the tournament's lunch mode; jersey numbers are unique within the team. An age outside the tournament's range is not an
    error (staff see it flagged)."""
    out, jerseys = [], set()
    for index, row in enumerate(rows):
        first, last = row.first_name.strip(), row.last_name.strip()
        if not first and not last:
            continue
        key = f"players.{index}"
        errors = {f"{key}.{name}": ["This field is required."] for name, value in (("first_name", first), ("last_name", last)) if not value}
        if errors:
            raise Invalid("Enter a first and last name for everyone.", errors)
        check_birth_date(row.date_of_birth, f"{key}.date_of_birth")
        if row.jersey_number is not None:
            if row.jersey_number in jerseys:
                raise Invalid("Jersey number used twice.", {f"{key}.jersey_number": ["This jersey number is already used by someone else in the team."]})
            jerseys.add(row.jersey_number)
        out.append(
            {
                "first_name": first, "last_name": last, "jersey_number": row.jersey_number, "role": row.role, "position_label": check_position(row.position_label, row.role, f"{key}.position_label"),
                "date_of_birth": None if row.role == TournamentRosterRole.STAFF else row.date_of_birth, "license_number": row.license_number.strip(),
                "dietary_restrictions": row.dietary_restrictions.strip(), "lunch_days": lunch_days_for(t, row.lunch_days),
            }
        )  # fmt: skip
    return out


def _closed(message="Registration is closed.", code="closed"):
    return Invalid(message, {"detail": [message]}, code=code)


@router.post("/{slug}/registrations", summary="A visiting team applies to enter", operation_id="public_tournaments_register", response={201: dict})
def register(request, slug: str, payload: PublicRegistrationIn):
    t = _tournament(request, slug)
    if t.status not in OPEN_FOR_REGISTRATION:
        raise HttpError(404, "Not found.")  # not accepting entries: as if the page did not exist
    if payload.website:
        return 201, {"status_token": secrets.token_urlsafe(32)}  # honeypot: pretend success, save nothing
    blocked = _throttled(request, payload.foreign_contact_email)
    if blocked is not None:
        return blocked
    try:
        validate_email(payload.foreign_contact_email.strip())
    except ValidationError:
        raise Invalid("Enter a valid email address.", {"foreign_contact_email": ["Enter a valid email address."]}) from None
    if not t.accepts_registrations:
        raise _closed()
    players = _people(t, payload.players)
    with transaction.atomic():
        registration = TournamentRegistration.objects.create(
            tournament=t, foreign_name=payload.foreign_name.strip(), foreign_contact_name=payload.foreign_contact_name.strip(), foreign_contact_email=payload.foreign_contact_email.strip(),
            foreign_contact_phone=payload.foreign_contact_phone, foreign_primary_color=payload.foreign_primary_color, foreign_secondary_color=payload.foreign_secondary_color,
        )  # fmt: skip
        TournamentRegistrationPlayer.objects.bulk_create([TournamentRegistrationPlayer(registration=registration, **p) for p in players])
        password = team_access.reset_password(registration)
    notifications.send_registration_confirmation_email(registration, request=request, password=password)
    # the person at this browser just filled the form: they go straight in, the emailed password is for later and for colleagues
    return 201, {"status_token": registration.status_token, "access": team_access.make_access(registration), "status": registration.status, "age_flagged_count": sum(1 for p in players if t.birth_year_out_of_range(p["date_of_birth"]))}


def _registration(request, slug, token, *, check=True) -> TournamentRegistration:
    """The application behind a private link. With a password set, the link alone is not enough: the caller has to send the access token
    that ``POST .../access`` hands out for the right password (401 ``password_required`` otherwise)."""
    t = _tournament(request, slug)
    registration = c.get_or_404(TournamentRegistration.objects.select_related("tournament", "tournament_team"), tournament=t, status_token=token)
    if check and not team_access.access_ok(registration, request.headers.get("X-Team-Access")):
        raise HttpError(401, "password_required")
    return registration


def _roster_editable(r: TournamentRegistration) -> bool:
    team = getattr(r, "tournament_team", None)
    return r.status == TournamentRegistration.Status.APPROVED and r.is_foreign and team is not None and team.withdrawn_at is None and timezone.localdate() < r.tournament.start_date


def _roster_closed(r: TournamentRegistration) -> bool:
    team = getattr(r, "tournament_team", None)
    return r.status == TournamentRegistration.Status.APPROVED and r.is_foreign and team is not None and team.withdrawn_at is None and timezone.localdate() >= r.tournament.start_date


def _lunch_totals(t: Tournament, rows) -> tuple[Decimal, Decimal]:
    """(mandatory lunch total, total lunch cost) for the people in ``rows`` at the tournament's price per day."""
    if t.lunch_mode == Tournament.LunchMode.NONE:
        return Decimal("0"), Decimal("0")
    price = t.lunch_price_per_day or Decimal("0")
    if t.lunch_mode == Tournament.LunchMode.MANDATORY:
        total = price * len(t.date_range()) * len(rows)
        return total, total
    return Decimal("0"), price * sum(len(r.lunch_days or []) for r in rows)


def _amount_owed(t: Tournament, lunch_total: Decimal, team: TournamentTeam) -> Decimal:
    owed = t.entry_fee_amount or Decimal("0")
    if t.lunch_billed_with_fee:
        owed += lunch_total
    if team.payment_status == TournamentTeam.PaymentStatus.FULLY_PAID:
        paid = owed
    elif team.payment_status == TournamentTeam.PaymentStatus.DEPOSIT_RECEIVED:
        paid = t.deposit_amount or Decimal("0")
    else:
        paid = Decimal("0")
    return max(owed - paid, Decimal("0"))


def _registration_doc(r: TournamentRegistration) -> dict:
    t = r.tournament
    team = getattr(r, "tournament_team", None)
    approved = r.status == TournamentRegistration.Status.APPROVED and team is not None
    rows = list(team.roster_entries.all()) if approved else list(r.players.all())
    mandatory_total, lunch_total = _lunch_totals(t, rows)
    return {
        "tournament": {
            **item(t), "contact_email": t.contact_email, "lunch_mode": t.lunch_mode, "lunch_price_per_day": _money(t.lunch_price_per_day), "dates": t.date_range(), "ice_hockey": is_ice_hockey(getattr(connection.tenant, "sport", None)),
            "earliest_birth_year": t.earliest_birth_year, "latest_birth_year": t.latest_birth_year,
        },
        "registration": {
            "status": r.status, "name": r.foreign_name, "contact_name": r.foreign_contact_name, "contact_email": r.foreign_contact_email, "contact_phone": r.foreign_contact_phone,
            "primary_color": r.foreign_primary_color, "secondary_color": r.foreign_secondary_color, "logo_url": c.url(r.foreign_logo), "rejection_reason": r.rejection_reason, "submitted_at": r.submitted_at,
        },
        "team": {"slug": team.slug, "withdrawn_at": team.withdrawn_at, "withdrawal_reason": team.withdrawal_reason, "payment_status": team.payment_status} if approved else None,
        "players": [c.person_out(p, t) for p in rows],
        # editable: pending applications always; an approved foreign team's live roster until the tournament starts
        "players_editable": r.status == TournamentRegistration.Status.PENDING or _roster_editable(r), "roster_closed": _roster_closed(r),
        "lunch_is_mandatory": t.lunch_mode == Tournament.LunchMode.MANDATORY, "mandatory_total": _money(mandatory_total), "lunch_total": _money(lunch_total),
        "amount_owed": _money(_amount_owed(t, lunch_total, team)) if approved else None,
        "unread_messages": unread_for_team(r),
    }  # fmt: skip


class AccessIn(Schema):
    password: str = Field(min_length=1, max_length=64)


@router.get("/{slug}/registrations/{token}/lock", summary="Whether this private link needs a password", operation_id="public_tournaments_registration_lock")
def registration_lock(request, slug: str, token: str):
    return {"locked": team_access.is_locked(_registration(request, slug, token, check=False))}


@router.post("/{slug}/registrations/{token}/access", summary="Trade the emailed password for an access token", operation_id="public_tournaments_registration_access")
def registration_access(request, slug: str, token: str, payload: AccessIn):
    r = _registration(request, slug, token, check=False)
    blocked = _throttled(request, token)
    if blocked is not None:
        return blocked
    if not team_access.password_ok(r, payload.password):
        raise Invalid("That password is not right.", {"password": ["That password is not right."]}, code="bad_password")
    return {"access": team_access.make_access(r)}


@router.get("/{slug}/registrations/{token}", summary="The status of an application, by its private token", operation_id="public_tournaments_registration_status")
def registration_status(request, slug: str, token: str):
    return _registration_doc(_registration(request, slug, token))


class MessageIn(Schema):
    body: str = Field(min_length=1, max_length=4000)


@router.get("/{slug}/registrations/{token}/messages", summary="The conversation between this team and the organisers (marks it read)", operation_id="public_tournaments_team_messages")
def team_messages(request, slug: str, token: str):
    r = _registration(request, slug, token)
    rows = thread_for_team(r)
    r.messages_seen_at = timezone.now()
    r.save(update_fields=["messages_seen_at"])
    return {"messages": [message_out(m) for m in rows]}


@router.post("/{slug}/registrations/{token}/messages", summary="Write to the organisers", operation_id="public_tournaments_team_message_send", response={201: dict})
def team_message_send(request, slug: str, token: str, payload: MessageIn):
    r = _registration(request, slug, token)
    blocked = _throttled(request)
    if blocked is not None:
        return blocked
    body = payload.body.strip()
    if not body:
        raise HttpError(422, "Write a message first.")
    message = TournamentMessage.objects.create(tournament=r.tournament, registration=r, sender=TournamentMessage.Sender.TEAM, author=r.foreign_contact_name, body=body)
    transaction.on_commit(lambda: notify_organisers(message))
    return 201, message_out(message)


@router.put("/{slug}/registrations/{token}/players", summary="Replace the player list (pending application, or the live roster until the tournament starts)", operation_id="public_tournaments_players")
def replace_players(request, slug: str, token: str, payload: PlayersIn):
    r = _registration(request, slug, token)
    blocked = _throttled(request)
    if blocked is not None:
        return blocked
    t = r.tournament
    players = _people(t, payload.players)
    with transaction.atomic():
        if r.status == TournamentRegistration.Status.PENDING:
            r.players.all().delete()
            TournamentRegistrationPlayer.objects.bulk_create([TournamentRegistrationPlayer(registration=r, **p) for p in players])
        elif _roster_editable(r):
            r.tournament_team.roster_entries.all().delete()
            entries = [{"foreign_first_name": p.pop("first_name"), "foreign_last_name": p.pop("last_name"), **p} for p in players]
            TournamentRosterEntry.objects.bulk_create([TournamentRosterEntry(tournament_team=r.tournament_team, **e) for e in entries])
        else:
            raise _closed("This roster can no longer be changed.", "locked")
    return _registration_doc(_registration(request, slug, token))


@router.post("/{slug}/registrations/{token}/logo", summary="Upload the team's logo", operation_id="public_tournaments_logo")
def upload_logo(request, slug: str, token: str, logo: File[UploadedFile]):
    r = _registration(request, slug, token)
    blocked = _throttled(request)
    if blocked is not None:
        return blocked
    if r.status == TournamentRegistration.Status.REJECTED or not r.is_foreign:
        raise _closed("The logo can no longer be changed.", "locked")
    if logo.size > MAX_LOGO_BYTES:
        raise Invalid("The logo is too large.", {"logo": ["The logo may be at most 2 MB."]})
    try:
        from PIL import Image

        Image.open(BytesIO(logo.read())).verify()
        logo.seek(0)
    except Exception:
        raise Invalid("Not an image.", {"logo": ["Upload a PNG, JPEG, GIF or WebP image."]}) from None
    r.foreign_logo.save(logo.name, logo, save=True)
    team = getattr(r, "tournament_team", None)
    if team is not None:
        team.foreign_logo = r.foreign_logo.name
        team.save(update_fields=["foreign_logo"])
    return _registration_doc(_registration(request, slug, token))
