"""Management shop discount codes (``/manage/shop/discounts``): the JSON counterpart of the
``shop/discounts/*`` screens (management/views.py Discount*View).

Who: the web's ``ShopManagerRequiredMixin`` -- a club admin or a shop manager (``can_manage_shop``) -- and
only while the club has the ``shop`` feature (404 ``feature_disabled`` otherwise, whatever the role).
Validation is ``DiscountForm``. There is no delete (orders keep a PROTECT reference): a code is switched
off with ``toggle-active``. The form leaves ``club`` out, so the per-club unique code is checked here (422 on ``code``).
Reads: shop.services.discounts.
"""

import uuid

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from ninja import Router

from api.core.errors import ApiError
from api.core.forms import form_error, post_data
from api.core.idempotency import idempotent
from api.core.pagination import paginate_keyset
from api.core.schemas import ErrorOut
from api.panel.guards import panel_club, require
from api.panel.schemas import shop_discounts as s
from api.protected.services import club_features
from club.services.access import can_manage_shop
from management.forms import DiscountForm
from shop.models import Discount
from shop.services.discounts import code_taken, discounts_with_use_count

router = Router(tags=["panel: shop discounts"])

PREFIX = "/manage/shop/discounts"
ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
INVALID = {422: ErrorOut}
FIELDS = ("name", "description", "code", "discount_type", "discount_amount", "is_active", "valid_from", "valid_until", "max_uses", "once_per_member")


def _club(request):
    club = panel_club(request)
    require(request, club, can_manage_shop)
    if not club_features(club)["shop"]:
        raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))
    return club


def _discount(club, discount_id) -> Discount:
    return get_object_or_404(discounts_with_use_count(club), pk=discount_id)


def _out(discount: Discount) -> dict:
    return {**{name: getattr(discount, name) for name in ("id", "slug", *FIELDS)}, "use_count": getattr(discount, "use_count", 0)}


def _save(club, instance: Discount, values: dict) -> Discount:
    if code_taken(club, values["code"], exclude_pk=instance.pk):
        raise ApiError(422, "validation_error", fields={"code": [str(_("A discount with this code already exists."))]})
    form = DiscountForm(post_data(values, checkboxes=("is_active", "once_per_member")), instance=instance)
    if not form.is_valid():
        raise form_error(form)
    instance.club = club
    form.save()
    return _discount(club, instance.pk)


@router.get(PREFIX, response={200: s.DiscountListOut, **ERRORS}, summary="Discount codes")
def list_discounts(request, cursor: str | None = None, limit: int | None = None):
    """By name, each with how often it was used. Club admins and shop managers."""
    club = _club(request)
    page = paginate_keyset(discounts_with_use_count(club), fields=["name", "pk"], cursor=cursor, limit=limit)
    return {"items": [_out(d) for d in page.items], "next_cursor": page.next_cursor}


@router.post(PREFIX, response={201: s.DiscountOut, **ERRORS, **INVALID}, summary="Create a discount code")
def create_discount(request, payload: s.DiscountIn):
    """422 on `code` when the club already has it (any case). Honours `Idempotency-Key`."""
    club = _club(request)
    return idempotent(request, lambda: (201, _out(_save(club, Discount(club=club), payload.dict()))))


@router.get(PREFIX + "/{uuid:discount_id}", response={200: s.DiscountOut, **ERRORS}, summary="One discount code")
def get_discount(request, discount_id: uuid.UUID):
    return _out(_discount(_club(request), discount_id))


@router.patch(PREFIX + "/{uuid:discount_id}", response={200: s.DiscountOut, **ERRORS, **INVALID}, summary="Edit a discount code")
def update_discount(request, discount_id: uuid.UUID, payload: s.DiscountPatch):
    """Send only the fields to change (`valid_from`, `valid_until`, `max_uses` accept null to clear them)."""
    club = _club(request)
    discount = _discount(club, discount_id)
    values = {name: getattr(discount, name) for name in FIELDS}
    changes = payload.dict(exclude_unset=True)
    for name in ("name", "code", "discount_type", "discount_amount", "is_active", "once_per_member", "description"):
        if name in changes and changes[name] is None:
            raise ApiError(422, "validation_error", fields={name: [str(_("This field cannot be null."))]})
    values.update(changes)
    return _out(_save(club, discount, values))


@router.post(PREFIX + "/{uuid:discount_id}/toggle-active", response={200: s.DiscountOut, **ERRORS}, summary="Switch a discount code on or off")
def toggle_discount(request, discount_id: uuid.UUID):
    """Flips `is_active` and returns the discount. Honours `Idempotency-Key` (a replay does not flip it back)."""
    club = _club(request)

    def work():
        discount = _discount(club, discount_id)
        discount.is_active = not discount.is_active
        discount.save(update_fields=["is_active"])
        return 200, _out(discount)

    return idempotent(request, work)
