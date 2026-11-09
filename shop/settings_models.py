"""Per-club shop settings: open/closed, the online payment provider and its (encrypted) credentials, invoice terms.

Registered from ``ShopConfig.ready`` (kept out of ``models.py`` so the foundation models stay untouched)."""

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(f"shop-credentials:{settings.SECRET_KEY}".encode()).digest())
    return Fernet(key)


class ShopSettings(UUIDModel):
    """One row per club schema (``ShopSettings.current()``). Credentials are stored Fernet-encrypted (key derived from
    ``SECRET_KEY``) and are never returned by the API: only ``credentials_configured``."""

    payment_provider = models.CharField(_("payment provider"), max_length=50, blank=True)
    credentials_encrypted = models.TextField(_("credentials"), blank=True)
    invoice_due_days = models.PositiveSmallIntegerField(_("invoice due days"), default=14)
    shop_open = models.BooleanField(_("shop open"), default=True, help_text=_("Closed: members cannot place orders (staff can still create orders for them; payments on existing orders keep working)."))
    pickup_instructions = models.TextField(_("default pickup instructions"), blank=True, help_text=_("Used when an order is marked ready for pickup without instructions of its own."))
    order_number_pattern = models.CharField(_("order number pattern"), max_length=40, default="ORD-{YYYY}-{NNNNN}")
    invoice_number_pattern = models.CharField(_("invoice number pattern"), max_length=40, default="INV-{YYYY}-{NNNNN}")
    voucher_number_pattern = models.CharField(_("voucher number pattern"), max_length=40, default="VCH-{YYYY}-{NNNNN}")
    batch_number_pattern = models.CharField(_("production batch number pattern"), max_length=40, default="PB-{YYYY}-{NNNNN}", help_text=_("Placeholders {YYYY} {YY} {MM} and one sequence such as {NNNNN}; see shop.services.numbering."))
    closed_message = models.TextField(_("closed message"), blank=True, help_text=_("Shown to members while the shop is closed."))

    class Meta:
        verbose_name = _("shop settings")
        verbose_name_plural = _("shop settings")
        app_label = "shop"

    @classmethod
    def current(cls) -> ShopSettings:
        obj = cls.objects.order_by("created").first()
        return obj or cls.objects.create()

    @property
    def credentials_configured(self) -> bool:
        return bool(self.credentials_encrypted)

    @property
    def credentials(self) -> dict:
        if not self.credentials_encrypted:
            return {}
        try:
            return json.loads(_fernet().decrypt(self.credentials_encrypted.encode()).decode())
        except InvalidToken, ValueError:
            return {}

    def set_credentials(self, values: dict) -> None:
        self.credentials_encrypted = _fernet().encrypt(json.dumps(values).encode()).decode() if values else ""


class ShopNumberSequence(models.Model):
    """The gapless counter behind the automatic numbers of one document type (``kind``: order, invoice, voucher, batch), one row
    per scope (``"2026"``, ``"2026-11"`` or ``"all"``, see ``shop.services.numbering``). Independent of the pattern's text."""

    kind = models.CharField(_("kind"), max_length=20)
    scope = models.CharField(_("scope"), max_length=16)
    last_number = models.PositiveIntegerField(_("last number"), default=0)

    class Meta:
        verbose_name = _("number sequence")
        verbose_name_plural = _("number sequences")
        app_label = "shop"
        constraints = [models.UniqueConstraint(fields=["kind", "scope"], name="unique_shop_number_sequence")]

    def __str__(self) -> str:
        return f"{self.kind} {self.scope}: {self.last_number}"
