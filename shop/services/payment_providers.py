"""Provider-agnostic online payments.

A provider turns an order amount into a payment link and later tells us (webhook) whether it was paid. No concrete
provider ships yet; ``manual_test`` is a trivial built-in for tests and development. To plug a real one in:

1. subclass ``PaymentProvider`` (``key``, ``label``, ``credential_fields``, ``supports_refunds``),
2. implement ``create_payment_link``, ``parse_webhook`` (verify the signature against ``credentials``, raise
   ``InvalidWebhook`` otherwise) and, if ``supports_refunds``, ``refund``,
3. call ``register(MyProvider())`` at import time (e.g. from an app's ``ready()``),
4. admins pick it under ``PUT /shop/settings`` (key + credentials); the webhook URL to give the provider is
   ``/api/v1/public/shop/payments/webhook/<key>``.
"""

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal


class ProviderError(Exception):
    """The provider could not do what was asked (user-facing message)."""


class InvalidWebhook(Exception):
    """Signature or payload of a webhook did not verify."""


@dataclass
class PaymentLink:
    url: str
    external_id: str
    data: dict = field(default_factory=dict)


@dataclass
class WebhookEvent:
    external_id: str
    status: str  # "paid" | "failed" | "refunded"
    event_id: str = ""
    amount: Decimal | None = None
    data: dict = field(default_factory=dict)


class PaymentProvider:
    key = ""
    label = ""
    #: names of the credential fields an admin must fill in (all are write-only in the API)
    credential_fields: tuple = ()
    supports_refunds = False

    def create_payment_link(self, order, amount: Decimal, return_url: str, credentials: dict) -> PaymentLink:
        raise NotImplementedError

    def parse_webhook(self, request, credentials: dict) -> WebhookEvent:
        """Verify and parse ``request`` (a Django HttpRequest). Raise ``InvalidWebhook`` when it does not verify."""
        raise NotImplementedError

    def refund(self, payment, credentials: dict) -> None:
        raise ProviderError("This provider does not support refunds.")


class ManualTestProvider(PaymentProvider):
    """Dev/test provider. Webhook body is JSON ``{"external_id", "status", "event_id"?}`` signed with HMAC-SHA256 of the raw
    body using the ``webhook_secret`` credential, hex in header ``X-Signature``."""

    key = "manual_test"
    label = "Manual test provider"
    credential_fields = ("webhook_secret",)
    supports_refunds = True

    def create_payment_link(self, order, amount, return_url, credentials):
        external_id = f"mt_{uuid.uuid4().hex[:16]}"
        return PaymentLink(url=f"https://pay.example.test/{external_id}?return={return_url}", external_id=external_id)

    @staticmethod
    def sign(body: bytes, secret: str) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def parse_webhook(self, request, credentials):
        secret = credentials.get("webhook_secret", "")
        given = request.headers.get("X-Signature", "")
        if not secret or not hmac.compare_digest(self.sign(request.body, secret), given):
            raise InvalidWebhook("Bad signature.")
        try:
            payload = json.loads(request.body)
            status = payload["status"]
            external_id = str(payload["external_id"])
        except (ValueError, KeyError, TypeError) as error:
            raise InvalidWebhook("Bad payload.") from error
        if status not in ("paid", "failed", "refunded"):
            raise InvalidWebhook("Unknown status.")
        return WebhookEvent(external_id=external_id, status=status, event_id=str(payload.get("event_id", "")))

    def refund(self, payment, credentials):
        return None


_REGISTRY: dict[str, PaymentProvider] = {}


def register(provider: PaymentProvider) -> PaymentProvider:
    _REGISTRY[provider.key] = provider
    return provider


def get_provider(key: str) -> PaymentProvider | None:
    return _REGISTRY.get(key)


def providers() -> list[PaymentProvider]:
    return sorted(_REGISTRY.values(), key=lambda p: p.key)


register(ManualTestProvider())
