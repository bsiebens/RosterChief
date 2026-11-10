"""``/public/registration``: the public join page, no login. Unknown club host / the platform host answer 404. Abuse protection (the repo's
only throttle pattern, see ``public_forms``): every POST is counted per client address with the Django cache, submissions also per email address
(over the limit: 429 + ``Retry-After``, ``code: rate_limited``), and a hidden ``website`` field is a honeypot (filled: pretend success, save nothing).
Refusals are 422 ``{detail, code, errors}`` (``code``: ``closed``, ``invalid``, ``already_resolved``, ``not_required``, ``cancelled`` ...)."""

import datetime
import secrets
import uuid

from django.core.cache import cache
from django.http import HttpResponse
from django_tenants.utils import get_public_schema_name
from ninja import File, Form, Router, UploadedFile
from ninja.errors import HttpError

from api.schemas_public_registration import AnswerIn, NumbersOut, PreviewIn, PreviewOut, PublicRegistrationOut, RegistrationIn, StatusOut, SubmittedOut
from members.services.errors import Invalid
from registration.services import config, holds, pricing, status, submission
from teams.models import Position, Team
from teams.services import numbers as team_numbers

router = Router()

WINDOW = 600
LIMITS = {"preview": 60, "submit": 10, "status": 120, "upload": 20, "answer": 60, "numbers": 240}
PER_EMAIL = 5
EMAIL_WINDOW = 3600


def _club_only(request):
    if request.tenant.schema_name == get_public_schema_name():
        raise HttpError(404, "Not found.")


def _address(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[-1].strip() or request.META.get("REMOTE_ADDR", "") or "unknown"


def _count(key: str, limit: int, window: int) -> bool:
    """Count one hit; True while within the limit."""
    if cache.add(key, 1, window):
        return True
    try:
        return cache.incr(key) <= limit
    except ValueError:
        cache.add(key, 1, window)
        return True


def _throttled(request, kind: str, extra: str = ""):
    key = f"registration:{kind}:{request.tenant.schema_name}:{_address(request)}"
    if not _count(key, LIMITS[kind], WINDOW):
        return _too_many(WINDOW)
    if extra and kind == "submit" and not _count(f"registration:email:{request.tenant.schema_name}:{extra.lower()}", PER_EMAIL, EMAIL_WINDOW):
        return _too_many(EMAIL_WINDOW)
    return None


def _too_many(wait: int):
    response = HttpResponse(b'{"detail": "Too many attempts. Try again later.", "code": "rate_limited"}', status=429, content_type="application/json")
    response["Retry-After"] = str(wait)
    return response


def _language(request, given: str = "") -> str:
    if given in submission.LANGS:
        return given
    for part in request.META.get("HTTP_ACCEPT_LANGUAGE", "").split(","):
        code = part.split(";")[0].strip()[:2].lower()
        if code in submission.LANGS:
            return code
    return ""


def _brand(club) -> dict:
    return {
        "name": club.name, "logo_url": club.logo.url if club.logo else None, "primary_color": club.primary_color or None, "secondary_color": club.secondary_color or None,
        "primary_content_color": club.primary_content_color or None, "secondary_content_color": club.secondary_content_color or None,
    }  # fmt: skip


def _entries(payload_entries) -> list[dict]:
    return [e.dict() for e in payload_entries]


@router.get("", auth=None, response=PublicRegistrationOut, summary="What the join page shows", operation_id="public_registration")
def get_page(request):
    """Club branding, whether registration is open, the texts, the products people can register for (with variants, prices, early-bird and
    registrant discounts) and the teams / positions they may ask for."""
    _club_only(request)
    settings = config.get_settings()
    products = []
    for p in pricing.offerable_products():
        early = {"discount_type": p.early_bird_discount_type, "discount_amount": p.early_bird_discount_amount, "deadline": p.early_bird_deadline} if p.early_bird_discount_type and p.early_bird_deadline else None
        products.append(
            {
                "id": p.pk, "name": p.name, "description": p.description, "season_id": p.season_id, "season_name": p.season.name, "early_bird": early, "entry_kind": pricing.entry_kind(p),
                "variants": [
                    {"id": o.variant.pk if o.variant else p.pk, "option_id": o.key, "name": o.label, "price": o.effective_price, "team_id": o.team.pk if o.team else None, "team_name": o.team.name if o.team else None}
                    for o in pricing.options_of(p)
                ],
                "registrant_discount_scope": p.registrant_discount_scope,
                "registrant_tiers": [{"min_registrants": t.min_registrants, "discount_type": t.discount_type, "discount_amount": t.discount_amount} for t in p.registrant_tiers.all()],
            }
        )  # fmt: skip
    return {
        "club": _brand(request.tenant), "is_open": settings.is_open and bool(products), "intro_text": settings.intro_text, "terms_text": settings.terms_text, "products": products,
        "teams": [{"id": t.pk, "name": t.name} for t in Team.objects.order_by("name")], "positions": [{"id": p.pk, "name": p.name} for p in Position.objects.filter(staff_position=False).order_by("ordering", "name")],
        "max_entries": submission.MAX_ENTRIES,
    }  # fmt: skip


@router.get("/numbers", auth=None, response=NumbersOut, summary="Jersey numbers a new player could wear on a team", operation_id="public_registration_numbers")
def get_numbers(request, team_id: uuid.UUID, date_of_birth: datetime.date | None = None, exclude: str = "", variant_id: uuid.UUID | None = None, option_id: str = ""):
    """For the registration's season (the season of ``option_id`` / ``variant_id``, else the only offerable season). ``exclude`` is a comma separated list of numbers
    already picked by other people in the same form on this team. Free numbers follow the team's pool: its range, its reservations and the age gap
    rule for ``date_of_birth`` (unknown: a held number is never free). A team without a pool answers ``pool: null, numbers: null`` and the numbers its
    roster holds in ``taken``. 404 for an unknown team, 422 ``no_season`` when no season can be told."""
    _club_only(request)
    if (limited := _throttled(request, "numbers")) is not None:
        return limited
    team = Team.objects.select_related("pool").filter(pk=team_id).first()
    if team is None:
        raise HttpError(404, "Not found.")
    key = option_id or (str(variant_id) if variant_id is not None else "")
    if key:
        found = pricing.options_by_key([key])
        season = found[key].product.season if key in found else None
        if key in found and found[key].team is not None:
            team = Team.objects.select_related("pool").get(pk=found[key].team.pk)  # a registration team is fixed: its numbers, whatever team was asked for
    else:
        seasons = {p.season_id: p.season for p in pricing.offerable_products()}
        season = next(iter(seasons.values())) if len(seasons) == 1 else None
    if season is None:
        raise Invalid("Choose what to register for first.", {"variant_id": ["Choose what to register for first."]}, code="no_season")
    picked = [int(n) for n in exclude.split(",")[:60] if n.strip().isdigit()]
    return team_numbers.available_numbers(team, season, date_of_birth, taken_in_batch=picked, batch=holds.holders(season, team))  # numbers held by registrations waiting for review count too


@router.post("/preview", auth=None, response=PreviewOut, summary="The price of a registration, nothing is saved", operation_id="public_registration_preview")
def preview(request, payload: PreviewIn):
    _club_only(request)
    if (limited := _throttled(request, "preview")) is not None:
        return limited
    options = pricing.options_by_key([e.option_id or (str(e.variant_id) if e.variant_id else "") for e in payload.entries])
    chosen = []
    for i, e in enumerate(payload.entries):
        key = e.option_id or (str(e.variant_id) if e.variant_id else "")
        if key not in options:
            raise Invalid("Choose what to register for.", {f"entries.{i}.variant_id": ["Choose what to register for."]})
        if e.kind in ("player", "volunteer") and not pricing.fits_kind(options[key].product, e.kind):
            raise Invalid("Choose what to register for.", {f"entries.{i}.variant_id": [pricing.kind_error(e.kind)]})
        chosen.append(options[key])
    priced = pricing.price_entries(chosen)
    early = priced.early_bird_total_discount
    return {
        "season": {"id": priced.season.pk, "name": priced.season.name},
        "lines": [
            {
                "index": i, "description": ln.option.full_name, "base_price": ln.base, "discount": ln.discount, "price": ln.own_price, "payable": ln.price, "is_credit": ln.is_credit,
                "credit_share": ln.credit_share, "order_discount_share": ln.order_discount, "early_bird_discount": ln.early_bird_discount, "early_bird_deadline": ln.early_bird_deadline,
            }
            for i, ln in enumerate(priced.lines)
        ],
        "order_discounts": [{"description": row["product"].name, "amount": row["amount"], "people": row["people"], "percent": row["percent"]} for row in priced.order_discounts], "order_discount_total": priced.order_discount_total,
        "total": priced.total, "credit_total": priced.credit_total, "credit_applied": priced.credit_applied, "total_if_early": priced.total - early, "early_bird_discount": early,
    }  # fmt: skip


@router.post("", auth=None, response={201: SubmittedOut}, summary="Register", operation_id="public_registration_submit")
def submit(request, payload: RegistrationIn):
    """Creates the registration (pending memberships, family, no login) and emails a confirmation with the status link. 422 with errors keyed
    ``contact.<field>`` / ``entries.<n>.<field>``; 422 ``closed`` when registration is closed; 429 when throttled. A filled ``website`` honeypot gets a
    fake 201 and nothing is saved."""
    _club_only(request)
    if (limited := _throttled(request, "submit", payload.contact.email.strip())) is not None:
        return limited
    if payload.website.strip():
        return 201, {"token": secrets.token_urlsafe(32)}
    batch = submission.submit_registration(request.tenant, contact=payload.contact.dict(), entries=_entries(payload.entries), language=_language(request, payload.language), features=getattr(request, "features", None))
    return 201, {"token": batch.status_token}


def _batch(request, token: str):
    batch = status.get_batch(token)
    if batch is None:
        raise HttpError(404, "Not found.")
    return batch


@router.get("/status/{token}", auth=None, response=StatusOut, summary="The registrant's status page", operation_id="public_registration_status")
def get_status(request, token: str):
    """Possession of the token is the credential. Money only once the invoice is confirmed; ``checklist[].can_upload`` says where a document is wanted."""
    _club_only(request)
    if (limited := _throttled(request, "status")) is not None:
        return limited
    batch = _batch(request, token)
    return {**status.status_data(batch), "club": _brand(request.tenant)}


@router.post("/status/{token}/documents/{requirement_id}", auth=None, response={201: StatusOut}, summary="Upload a document for a checklist item", operation_id="public_registration_upload")
def upload(request, token: str, requirement_id: str, document: File[UploadedFile], member_id: Form[str] = ""):
    """Multipart: ``document`` (a file) and ``member_id`` (who it is for; optional when the registration has one person). 422 codes: ``already_resolved``,
    ``not_required``, ``unknown_person``, ``unknown_requirement``, ``cancelled`` or ``invalid`` (file type / size)."""
    import uuid

    _club_only(request)
    if (limited := _throttled(request, "upload")) is not None:
        return limited
    batch = _batch(request, token)
    try:
        req_id = uuid.UUID(requirement_id)
        person = uuid.UUID(member_id) if member_id else None
    except ValueError:
        raise HttpError(404, "Not found.") from None
    status.upload_document(batch, req_id, person, document)
    return 201, {**status.status_data(batch), "club": _brand(request.tenant)}


@router.post("/status/{token}/answers/{requirement_id}", auth=None, response={201: StatusOut}, summary="Answer a question on the checklist", operation_id="public_registration_answer")
def answer(request, token: str, requirement_id: str, payload: AnswerIn):
    """JSON ``{answer, member_id}``: ``member_id`` is optional when the registration has one person. The registrant may answer an open question and
    change their own answer later; an answer staff entered, or an item staff marked not needed, is closed (``already_resolved``). 422 codes:
    ``already_resolved``, ``not_a_question``, ``unknown_person``, ``unknown_requirement``, ``cancelled``; an invalid answer is keyed ``answer``."""
    import uuid

    _club_only(request)
    if (limited := _throttled(request, "answer")) is not None:
        return limited
    batch = _batch(request, token)
    try:
        req_id = uuid.UUID(requirement_id)
        person = uuid.UUID(payload.member_id) if payload.member_id else None
    except ValueError:
        raise HttpError(404, "Not found.") from None
    status.answer_question(batch, req_id, person, payload.answer)
    return 201, {**status.status_data(batch), "club": _brand(request.tenant)}
