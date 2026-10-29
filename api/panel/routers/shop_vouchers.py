"""Management shop vouchers (``/manage/shop/vouchers``): the JSON counterpart of the
``shop/vouchers/*`` screens (management/views.py Voucher*View).

Who: the web's ``ShopManagerRequiredMixin`` -- a club admin or a shop manager (``can_manage_shop``) -- and
only while the club has the ``shop`` feature (404 ``feature_disabled`` otherwise, whatever the role).
Validation is ``VoucherForm`` (the number is generated, never sent); consumptions are recorded and removed
through shop.services.vouchers, which keep ``consumed_amount`` in step. There is no voucher delete: void one
with ``is_active: false``.
"""

import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.pagination import paginate
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import shop_vouchers as s
from api.protected.services import club_features
from club.services.access import can_manage_shop
from management.forms import VoucherConsumptionForm, VoucherForm
from shop.models import Voucher, VoucherConsumption
from shop.services.payments import PaymentError
from shop.services.vouchers import delete_manual_consumption, record_manual_consumption, voucher_history

router = Router(tags=["panel: shop vouchers"])

PREFIX = "/manage/shop/vouchers"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
FORM_KEYS = {"single": {"issued_to_id": "issued_to"}}
ALIASES = {"issued_to": "issued_to_id"}


def _club(request):
    club = panel_club(request)
    require(request, club, can_manage_shop)
    if not club_features(club)["shop"]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    return club


def _voucher(club, voucher_id) -> Voucher:
    return get_object_or_404(Voucher.objects.select_related("issued_to"), pk=voucher_id, club=club)


def _user(user) -> dict | None:
    return {"id": user.pk, "name": user.get_full_name() or str(user)} if user else None


def _out(voucher: Voucher) -> dict:
    return {
        "id": voucher.pk,
        "number": voucher.number,
        "issued_to": {"id": voucher.issued_to_id, "name": str(voucher.issued_to)} if voucher.issued_to_id else None,
        "amount": voucher.amount,
        "consumed_amount": voucher.consumed_amount,
        "available_amount": voucher.available_amount,
        "expiry_date": voucher.expiry_date,
        "is_active": voucher.is_active,
        "is_expired": voucher.is_expired,
        "is_usable": voucher.is_usable,
        "notes": voucher.notes,
        "created": voucher.created,
    }


def _history_row(row: dict) -> dict:
    manual = row["kind"] == "manual"
    return {
        "kind": row["kind"],
        "amount": row["amount"],
        "when": row["when"],
        "detail": row["detail"] if manual else str(row["detail"]),
        "consumption_id": row["consumption"].pk if manual else None,
        "order_id": None if manual else row["detail"].pk,
        "recorded_by": _user(row["recorded_by"]),
    }


def _consumption_out(consumption: VoucherConsumption) -> dict:
    return {"id": consumption.pk, "amount": consumption.amount, "note": consumption.note, "recorded_at": consumption.recorded_at, "recorded_by": _user(consumption.recorded_by)}


def _save(club, instance: Voucher, values: dict) -> Voucher:
    consumed = instance.consumed_amount if instance.pk else 0
    if values["amount"] < consumed:
        raise ApiError(422, "validation_error", fields={"amount": [str(_("Cannot be less than what is already consumed."))]})
    form = VoucherForm(post_data(values, checkboxes=("is_active",), **FORM_KEYS), club=club, instance=instance)
    # Before is_valid(): Voucher.clean() checks `issued_to` against club_id (as the web create view does).
    form.instance.club = club
    if not form.is_valid():
        raise form_error(form, ALIASES)
    form.save()
    return _voucher(club, form.instance.pk)


@router.get(PREFIX, response={200: s.VoucherListOut, **ERRORS}, summary="Vouchers")
def list_vouchers(
    request,
    q: Query[str | None] = None,
    is_active: bool | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Newest first. `q` matches the voucher number or the name of who it was issued to. Club admins and shop managers."""
    club = _club(request)
    vouchers = Voucher.objects.filter(club=club).select_related("issued_to")
    if q and q.strip():
        term = q.strip()
        vouchers = vouchers.filter(Q(number__icontains=term) | Q(issued_to__first_name__icontains=term) | Q(issued_to__last_name__icontains=term))
    if is_active is not None:
        vouchers = vouchers.filter(is_active=is_active)
    page = paginate(vouchers, sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [_out(v) for v in page.items], "next_cursor": page.next_cursor}


@router.post(PREFIX, response={201: s.VoucherOut, **ERRORS, **INVALID}, summary="Create a voucher")
def create_voucher(request, payload: s.VoucherIn):
    """The number is generated. 422 on `issued_to_id` for someone outside the club. Honours `Idempotency-Key`."""
    club = _club(request)
    return idempotent(request, lambda: (201, _out(_save(club, Voucher(club=club), payload.dict()))))


@router.get(PREFIX + "/{uuid:voucher_id}", response={200: s.VoucherDetailOut, **ERRORS}, summary="One voucher with its history")
def get_voucher(request, voucher_id: uuid.UUID):
    """Includes `history`: manual consumptions and order payments together, newest first."""
    voucher = _voucher(_club(request), voucher_id)
    return {**_out(voucher), "history": [_history_row(row) for row in voucher_history(voucher)]}


@router.patch(PREFIX + "/{uuid:voucher_id}", response={200: s.VoucherOut, **ERRORS, **INVALID}, summary="Edit a voucher")
def update_voucher(request, voucher_id: uuid.UUID, payload: s.VoucherPatch):
    """Send only the fields to change (`issued_to_id: null` clears it). `amount` may not drop below what is consumed."""
    club = _club(request)
    voucher = _voucher(club, voucher_id)
    values = {"issued_to_id": voucher.issued_to_id, "amount": voucher.amount, "expiry_date": voucher.expiry_date, "is_active": voucher.is_active, "notes": voucher.notes}
    changes = payload.dict(exclude_unset=True)
    for name in ("amount", "expiry_date", "is_active", "notes"):
        if name in changes and changes[name] is None:
            raise ApiError(422, "validation_error", fields={name: [str(_("This field cannot be null."))]})
    values.update(changes)
    return _out(_save(club, voucher, values))


@router.post(PREFIX + "/{uuid:voucher_id}/consumptions", response={201: s.ConsumptionCreatedOut, **ERRORS, **INVALID}, summary="Record a consumption")
def create_consumption(request, voucher_id: uuid.UUID, payload: s.ConsumptionIn):
    """Draws the voucher down by hand (cash, outside the shop). 422 on `amount` above the available balance.
    Honours `Idempotency-Key`."""
    club = _club(request)
    form = VoucherConsumptionForm(payload.dict())
    if not form.is_valid():
        raise form_error(form)

    def work():
        voucher = _voucher(club, voucher_id)
        try:
            consumption = record_manual_consumption(voucher, amount=form.cleaned_data["amount"], note=form.cleaned_data["note"], recorded_by=request.user)
        except PaymentError as error:
            raise ApiError(422, "validation_error", fields={"amount": [str(error)]}) from None
        voucher.refresh_from_db()
        return 201, {"consumption": _consumption_out(consumption), "voucher": _out(_voucher(club, voucher_id))}

    return idempotent(request, work)


@router.delete(PREFIX + "/{uuid:voucher_id}/consumptions/{uuid:consumption_id}", response={204: None, **ERRORS}, summary="Remove a manual consumption")
def delete_consumption(request, voucher_id: uuid.UUID, consumption_id: uuid.UUID):
    """Gives the amount back to the voucher."""
    club = _club(request)
    consumption = get_object_or_404(VoucherConsumption.objects.select_related("voucher"), pk=consumption_id, voucher__pk=voucher_id, voucher__club=club)
    delete_manual_consumption(consumption)
    return 204, None
