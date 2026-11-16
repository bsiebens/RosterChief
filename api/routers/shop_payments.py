"""Shop payments: order payments (``/shop/orders/{id}/payments``, ``mark-paid``, ``payment-link``), provider settings
(``/shop/settings``) and the public provider webhook (``/public/shop/payments/webhook/{provider}``). Needs ``shop.manage_orders``
except the webhook, which is verified by the provider's own signature check."""

import uuid

from django.db import transaction
from django.http import HttpResponse
from django_tenants.utils import get_public_schema_name
from ninja import Router
from ninja.errors import HttpError

from api.auth import AUTHENTICATORS
from api.routers.shop_common import access, gate
from api.routers.shop_orders import get_order, order_detail, payment_out
from api.schemas_shop_orders import MarkPaidIn, NumberingPreviewIn, NumberingPreviewOut, OrderDetailOut, PaymentIn, PaymentLinkIn, PaymentOut, PaymentPatch, SettingsIn, SettingsOut, ShopStatusOut, WebhookOut
from formbuilder.services.management import Conflict
from members.services.errors import Invalid
from shop.models import Payment
from shop.services import defaults, numbering, payment_providers, payments
from shop.services import orders as order_svc
from shop.settings_models import ShopSettings

router = Router(auth=AUTHENTICATORS)
settings_router = Router(auth=AUTHENTICATORS)
public_router = Router()


def _gate(request):
    gate(request, access.require_manage_shop_orders)


def _payment(order, payment_id) -> Payment:
    p = order.payments.select_related("voucher", "recorded_by").filter(pk=payment_id).first()
    if p is None:
        raise HttpError(404, "Not found.")
    return p


@router.post("/{order_id}/payments", response={201: PaymentOut}, summary="Record a payment", operation_id="record_shop_payment")
def record(request, order_id: uuid.UUID, payload: PaymentIn):
    _gate(request)
    order = get_order(order_id)
    voucher = order_svc.find_voucher(payload.voucher_number) if payload.voucher_number else None
    p = payments.record_payment(order, amount=payload.amount, method=payload.method, reference=payload.reference, note=payload.note, voucher=voucher, paid_at=payload.paid_at, actor=request.user)
    return 201, payment_out(Payment.objects.select_related("voucher", "recorded_by").get(pk=p.pk))


@router.post("/{order_id}/mark-paid", response=OrderDetailOut, summary="Settle what is still due", operation_id="mark_shop_order_paid")
def mark_paid(request, order_id: uuid.UUID, payload: MarkPaidIn):
    _gate(request)
    order = get_order(order_id)
    if payments.mark_paid(order, method=payload.method, reference=payload.reference, actor=request.user) is None:
        raise Conflict("Nothing is due on this order.", code="nothing_due")
    return order_detail(order)


@router.patch("/{order_id}/payments/{payment_id}", response=PaymentOut, summary="Edit a payment", operation_id="update_shop_payment")
def update(request, order_id: uuid.UUID, payment_id: uuid.UUID, payload: PaymentPatch):
    _gate(request)
    p = _payment(get_order(order_id), payment_id)
    payments.update_payment(p, **payload.dict(exclude_unset=True))
    return payment_out(_payment(p.order, payment_id))


@router.delete("/{order_id}/payments/{payment_id}", response={204: None}, summary="Delete a payment", operation_id="delete_shop_payment")
def delete(request, order_id: uuid.UUID, payment_id: uuid.UUID):
    _gate(request)
    payments.delete_payment(_payment(get_order(order_id), payment_id))
    return 204, None


@router.post("/{order_id}/payments/{payment_id}/refund", response=PaymentOut, summary="Refund a payment", operation_id="refund_shop_payment")
def refund(request, order_id: uuid.UUID, payment_id: uuid.UUID):
    _gate(request)
    p = _payment(get_order(order_id), payment_id)
    payments.refund_payment(p, actor=request.user)
    return payment_out(_payment(p.order, payment_id))


@router.post("/{order_id}/payment-link", response={201: PaymentOut}, summary="Create an online payment link", operation_id="create_shop_payment_link")
def payment_link(request, order_id: uuid.UUID, payload: PaymentLinkIn):
    _gate(request)
    p = payments.create_payment_link(get_order(order_id), return_url=payload.return_url, amount=payload.amount, actor=request.user)
    return 201, payment_out(Payment.objects.select_related("voucher", "recorded_by").get(pk=p.pk))


def _settings_out(user) -> dict:
    s = ShopSettings.current()
    provider = payment_providers.get_provider(s.payment_provider)
    return {
        "shop_open": s.shop_open,
        "closed_message": s.closed_message,
        "pickup_instructions": s.pickup_instructions,
        "club_payment_instructions": defaults.payment_instructions(),
        "numbering": [numbering.status(kind) for kind in numbering.KINDS],
        "numbering_placeholders": numbering.PLACEHOLDERS,
        "can_edit_invoice_numbering": access.can_manage_shop(user),
        "payment_provider": s.payment_provider,
        "credentials_configured": s.credentials_configured,
        "credential_fields": list(provider.credential_fields) if provider else [],
        "invoice_due_days": s.invoice_due_days,
        "vat_mode": s.vat_mode,
        "can_edit_vat": access.can_manage_shop(user),
        "providers": [{"key": p.key, "label": p.label, "credential_fields": list(p.credential_fields), "supports_refunds": p.supports_refunds} for p in payment_providers.providers()],
    }


def _apply_numbering(settings, changes: dict, user) -> None:
    """Validate every change first (unknown type, not allowed, bad pattern, a next number that goes back), then save the patterns and
    move the counters, so a wrong entry changes nothing. Invoice numbering needs the whole-shop permission."""
    unknown = sorted(set(changes) - set(numbering.KINDS))
    if unknown:
        raise Invalid("Unknown document type.", {f"numbering.{unknown[0]}": ["Unknown document type."]})
    if changes.get("invoice") and (changes["invoice"].get("pattern") is not None or changes["invoice"].get("next") is not None):
        access.require_manage_shop(user)  # 403 before anything is saved
    for kind, change in changes.items():
        if change.get("pattern") is not None:
            numbering.check_pattern(change["pattern"].strip(), f"numbering.{kind}.pattern")
    with transaction.atomic():
        for kind, change in changes.items():
            if change.get("pattern") is not None and change["pattern"].strip() != numbering.pattern_for(kind):
                setattr(settings, numbering.KINDS[kind][0], change["pattern"].strip())
                settings.save(update_fields=[numbering.KINDS[kind][0], "modified"])
            if change.get("next") is not None:
                try:
                    numbering.set_next(kind, change["next"])
                except Invalid as error:
                    raise Invalid(str(error), {f"numbering.{kind}.next": error.errors.get("next", [str(error)])}) from error


@settings_router.post("/settings/numbering-preview", response=NumberingPreviewOut, summary="Preview a numbering pattern", operation_id="preview_shop_numbering")
def numbering_preview(request, payload: NumberingPreviewIn):
    """The number the next document of this type would get with ``payload.pattern`` (empty = the saved one), or why the pattern is not
    valid. Always 200 so the settings page can show the message while typing."""
    _gate(request)
    if payload.kind not in numbering.KINDS:
        raise Invalid("Unknown document type.", {"kind": ["Unknown document type."]})
    pattern = payload.pattern.strip() or numbering.pattern_for(payload.kind)
    try:
        numbering.check_pattern(pattern)
    except Invalid as error:
        return {"valid": False, "message": str(error), "number": "", "resets": "", "scope_label": ""}
    state = numbering.status(payload.kind, pattern)
    return {"valid": True, "message": "", "number": state["next_number"], "resets": state["resets"], "scope_label": state["scope_label"]}


@settings_router.get("/settings", response=SettingsOut, summary="Shop settings (secrets never returned)", operation_id="get_shop_settings")
def get_settings(request):
    _gate(request)
    return _settings_out(request.user)


@settings_router.put("/settings", response=SettingsOut, summary="Change shop settings", operation_id="update_shop_settings")
def put_settings(request, payload: SettingsIn):
    _gate(request)
    s = ShopSettings.current()
    data = payload.dict(exclude_unset=True)
    if "payment_provider" in data:
        key = data["payment_provider"] or ""
        if key and payment_providers.get_provider(key) is None:
            raise Invalid("Unknown provider.", {"payment_provider": ["Unknown provider."]})
        if key != s.payment_provider:
            s.set_credentials({})
        s.payment_provider = key
    if "credentials" in data:
        provider = payment_providers.get_provider(s.payment_provider)
        values = {k: v for k, v in (data["credentials"] or {}).items() if v}
        if values and (provider is None or set(values) - set(provider.credential_fields)):
            raise Invalid("Unknown credential field.", {"credentials": ["Unknown credential field for this provider."]})
        s.set_credentials({**s.credentials, **values} if values else {})
    if data.get("numbering"):
        _apply_numbering(s, data["numbering"], request.user)
    if data.get("pickup_instructions") is not None:
        s.pickup_instructions = data["pickup_instructions"].strip()
    if data.get("shop_open") is not None:
        s.shop_open = data["shop_open"]
    if data.get("closed_message") is not None:
        s.closed_message = data["closed_message"].strip()
    if "invoice_due_days" in data and data["invoice_due_days"] is not None:
        s.invoice_due_days = data["invoice_due_days"]
    if data.get("vat_mode") is not None and data["vat_mode"] != s.vat_mode:
        if data["vat_mode"] not in ShopSettings.VatMode.values:
            raise Invalid("Unknown VAT setting.", {"vat_mode": ["Choose off or included."]})
        access.require_manage_shop(request.user)  # VAT is on legal documents: only whoever manages the whole shop
        s.vat_mode = data["vat_mode"]
    s.save()
    return _settings_out(request.user)


@public_router.get("/status", auth=None, response=ShopStatusOut, summary="Whether the shop is open for members", operation_id="shop_public_status")
def public_status(request):
    """For the storefront: ``open`` and, while closed, the club's message. 404 when the club has no shop."""
    if request.tenant.schema_name == get_public_schema_name() or not request.features.has("shop"):
        raise HttpError(404, "Not found.")
    s = ShopSettings.current()
    return {"open": s.shop_open, "message": "" if s.shop_open else s.closed_message}


@public_router.post("/payments/webhook/{provider}", auth=None, response=WebhookOut, summary="Payment provider webhook", operation_id="shop_payment_webhook")
def webhook(request, provider: str):
    """Verified by the provider interface (400 on a bad signature); idempotent; confirms through the payments service."""
    if request.tenant.schema_name == get_public_schema_name() or not request.features.has("shop"):
        raise HttpError(404, "Not found.")
    impl = payment_providers.get_provider(provider)
    if impl is None:
        raise HttpError(404, "Not found.")
    try:
        event = impl.parse_webhook(request, ShopSettings.current().credentials)
    except payment_providers.InvalidWebhook as error:
        raise HttpError(400, str(error) or "Invalid webhook.") from error
    return {"result": payments.apply_provider_event(provider, event)}


__all__ = ["HttpResponse"]
