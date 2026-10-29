"""Re-registration (API.md §7.2), club host: register the account's people -- or someone
new to the family -- for a season. Two steps like the website: `quote` prices it without
saving anything, then `POST /registration` submits it for the club to review. JSON
entries run through the website's own RegistrationEntryFormSet, pricing and
submit_registration (mobile.services.reregistration), so the rules can't differ.
"""

from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.idempotency import idempotent
from api.core.scope import club_scope
from mobile.services.reregistration import PREFIX, entry_formset, member_current_numbers, price_formset, submit_reregistration
from registration.forms import MAX_REGISTRATION_ENTRIES
from registration.services import PricingError, RegistrationError, available_registration_products, resolve_chosen_season, team_number_pools
from registration.services.notifications import send_registration_confirmation_email
from teams.models import Team

from ..presenters import money
from ..schemas import ErrorOut, QuoteOut, RegistrationIn, RegistrationOptionsOut, RegistrationOut

router = Router(tags=["registration"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
#: Form field -> the API's name for it, for 422 `fields` keys (`entries.0.variant_id`).
FIELD_NAMES = {"existing_member": "member_id", "product_variant": "variant_id", "requested_team": "team_id", "requested_jersey_number": "jersey_number"}


def _season_out(season) -> dict:
    return {"id": season.pk, "start_date": season.start_date, "end_date": season.end_date, "name": season.name}


@router.get("/registration/options", response={200: RegistrationOptionsOut, **ERRORS}, summary="What can be registered for")
def options(request, season_id: str | None = None):
    """Seasons open for registration and, once one is chosen (automatic when there's only
    one), its products, teams and free jersey numbers."""
    scope = club_scope(request)
    season, seasons = resolve_chosen_season(scope.club, season_id)
    data = {"seasons": [_season_out(item) for item in seasons], "season": None, "products": [], "teams": [], "current_numbers": [], "max_entries": MAX_REGISTRATION_ENTRIES}
    if season is None:
        return data

    products = []
    for product in available_registration_products(scope.club, season=season).select_related("category"):
        variants = [{"id": variant.pk, "name": variant.name, "price": money(variant.effective_price)} for variant in product.variants.all() if variant.is_active]
        if variants:
            products.append({"id": product.pk, "name": product.name, "registration_kind": product.category.registration_kind if product.category_id else "", "variants": variants})
    pools = team_number_pools(scope.club, season)
    teams = [{"id": team.pk, "name": team.name, "short_name": team.short_name, "jersey_numbers": pools.get(str(team.pk))} for team in Team.objects.filter(club=scope.club).order_by("name")]
    current = [{"member_id": member_id, "team_id": team_id, "number": number} for member_id, numbers in member_current_numbers(scope.club, scope.people, season).items() for team_id, number in numbers.items()]
    return data | {"season": _season_out(season), "products": products, "teams": teams, "current_numbers": current}


def _formset(scope, payload: RegistrationIn):
    """Validate ``payload`` exactly as the website's form would, or raise 422."""
    season, _seasons = resolve_chosen_season(scope.club, str(payload.season_id))
    if season is None or season.pk != payload.season_id:
        raise ApiError(422, "validation_error", fields={"season_id": [_("Registration isn't open for this season.")]})
    if len(payload.entries) > MAX_REGISTRATION_ENTRIES:
        raise ApiError(422, "validation_error", fields={"entries": [_("At most %(count)d registrations at a time.") % {"count": MAX_REGISTRATION_ENTRIES}]})

    # The web form treats a row without a person as blank and skips it; in the API every
    # entry is deliberate, so a nameless new person is an error on that entry.
    unnamed = {f"entries.{index}.last_name": [_("Give a member_id, or the new person's name.")] for index, entry in enumerate(payload.entries) if not entry.member_id and not (entry.first_name.strip() or entry.last_name.strip())}
    if unnamed:
        raise ApiError(422, "validation_error", fields=unnamed)

    data = {f"{PREFIX}-TOTAL_FORMS": str(len(payload.entries)), f"{PREFIX}-INITIAL_FORMS": "0", f"{PREFIX}-MIN_NUM_FORMS": "0", f"{PREFIX}-MAX_NUM_FORMS": str(MAX_REGISTRATION_ENTRIES)}
    for index, entry in enumerate(payload.entries):
        key = f"{PREFIX}-{index}-"
        if entry.member_id:
            scope.person(entry.member_id)  # 404 for anyone outside the family
            data[key + "existing_member"] = str(entry.member_id)
            data[key + "is_contact"] = "on"  # "Include this person", as the web form posts it
        else:
            data[key + "first_name"] = entry.first_name
            data[key + "last_name"] = entry.last_name
            data[key + "date_of_birth"] = entry.date_of_birth.isoformat() if entry.date_of_birth else ""
        data[key + "entry_kind"] = entry.entry_kind
        data[key + "product_variant"] = str(entry.variant_id)
        data[key + "requested_team"] = str(entry.team_id) if entry.team_id else ""
        data[key + "requested_jersey_number"] = str(entry.jersey_number) if entry.jersey_number is not None else ""

    formset = entry_formset(scope.club, scope.people, season, data=data)
    if not formset.is_valid():
        fields = {}
        for index, errors in enumerate(formset.errors):
            for name, messages in errors.items():
                label = "entries" if name == "__all__" else f"entries.{index}.{FIELD_NAMES.get(name, name)}"
                fields.setdefault(label, []).extend(str(message) for message in messages)
        if formset.non_form_errors():
            fields.setdefault("entries", []).extend(str(message) for message in formset.non_form_errors())
        raise ApiError(422, "validation_error", fields=fields)
    try:
        entries, priced = price_formset(formset)
    except PricingError as error:
        raise ApiError(422, "validation_error", fields={"entries": [str(error)]}) from error
    return entries, priced


@router.post("/registration/quote", response={200: QuoteOut, **ERRORS, 422: ErrorOut}, summary="Price a registration")
def quote(request, payload: RegistrationIn):
    """Validates and prices the entries without saving anything. `422` names the entry
    and field, e.g. `entries.1.variant_id`."""
    scope = club_scope(request)
    entries, priced = _formset(scope, payload)
    lines = []
    for entry, row in zip(entries, priced, strict=True):
        variant = row["variant"]
        lines.append(
            {
                "member_id": entry.existing_member.pk if entry.existing_member else None,
                "first_name": entry.first_name,
                "last_name": entry.last_name,
                "entry_kind": entry.entry_kind,
                "product_name": variant.product.name if variant else "",
                "variant_name": variant.name if variant else "",
                "team_id": entry.requested_team.pk if entry.requested_team else None,
                "jersey_number": entry.requested_jersey_number,
                "price": money(row["price"]),
                "multi_registrant_discount": money(row["min_registrants_discount"]),
                "early_payment_deadline": row["deadline"],
                "early_payment_discount": money(row["deadline_discount"]),
            }
        )
    total = sum((row["price"] - row["min_registrants_discount"] for row in priced), Decimal("0"))
    early_total = sum((row["price"] - row["min_registrants_discount"] - row["deadline_discount"] for row in priced), Decimal("0"))
    return {"lines": lines, "total": money(total), "early_payment_total": money(early_total)}


@router.post("/registration", response={201: RegistrationOut, **ERRORS, 409: ErrorOut, 422: ErrorOut}, summary="Submit a registration")
def register(request, payload: RegistrationIn):
    """The same entries as the quote. The club reviews it, then sends the invoice; the
    account gets a confirmation email now. Send an `Idempotency-Key`: a double tap
    must not register twice."""
    scope = club_scope(request)
    entries, _priced = _formset(scope, payload)

    def work():
        try:
            batch = submit_reregistration(scope.club, request.user, scope.me, entries)
        except RegistrationError as error:
            raise ApiError(409, "registration_failed", str(error)) from error
        send_registration_confirmation_email(batch, request=request)
        return 201, {"registration_id": batch.pk, "total": money(batch.total), "status": "received"}

    return idempotent(request, work)
