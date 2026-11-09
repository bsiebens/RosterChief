"""Public registration (club schema, so no club foreign key anywhere). Ported from the old ``registration`` app.

Someone fills in the public join page; that creates a ``RegistrationBatch`` with one ``RegistrationDetails`` per person, and a pending
``ClubMembership`` for each, whose fee lives on the membership (``fee_amount``, ``early_payment_deadline`` / ``early_payment_discount``).
Until staff confirm the batch's invoice (``confirmed_at``) its memberships stay out of the Sign-ups queue.
"""

import secrets
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from members.models import ClubMembership, Member
from rosterchief.base import UUIDModel


def _token() -> str:
    return secrets.token_urlsafe(32)


class RegistrationSettings(UUIDModel):
    """The club's single registration settings row (``registration.services.settings.get_settings`` creates it)."""

    is_open = models.BooleanField(_("open for registrations"), default=False)
    intro_text = models.TextField(_("introduction"), blank=True, help_text=_("Shown at the top of the join page."))
    terms_text = models.TextField(_("terms"), blank=True, help_text=_("Shown above the submit button."))
    payment_instructions = models.TextField(_("payment instructions"), blank=True, help_text=_("Printed on the invoice and in its email."))
    invoice_due_days = models.PositiveSmallIntegerField(_("invoice due days"), default=30)

    class Meta:
        verbose_name = _("registration settings")
        verbose_name_plural = _("registration settings")

    def __str__(self):
        return "Registration settings"


class RegistrationBatch(UUIDModel):
    """One submission of the join page: a contact registering themselves and/or the people they are a parent of."""

    #: The possession of this token is the registrant's credential for the status page (no login).
    status_token = models.CharField(_("status token"), max_length=64, unique=True, editable=False, default=_token)
    contact = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="registration_batches", verbose_name=_("contact"))
    season = models.ForeignKey("events.Season", on_delete=models.PROTECT, related_name="registration_batches", verbose_name=_("season"))
    submitted_at = models.DateTimeField(_("submitted at"), auto_now_add=True)
    #: The billing review gate: a batch with no ``confirmed_at`` is invisible in the Sign-ups queue.
    confirmed_at = models.DateTimeField(_("confirmed at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    confirmation_sent_at = models.DateTimeField(_("confirmation sent at"), null=True, blank=True)
    invoice_number = models.CharField(_("invoice number"), max_length=50, blank=True, unique=True, null=True)
    invoice_due_date = models.DateField(_("invoice due date"), null=True, blank=True)
    invoice_sent_at = models.DateTimeField(_("invoice sent at"), null=True, blank=True)
    manual_discount_amount = models.DecimalField(_("manual discount"), max_digits=10, decimal_places=2, default=Decimal("0.00"))
    manual_discount_note = models.CharField(_("manual discount note"), max_length=255, blank=True)
    language = models.CharField(_("language"), max_length=5, blank=True, help_text=_("The registrant's language (from their browser), used for the emails."))

    class Meta:
        verbose_name = _("registration")
        verbose_name_plural = _("registrations")
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.contact or '?'} ({self.submitted_at:%Y-%m-%d})"

    @property
    def status(self) -> str:
        if self.cancelled_at:
            return "cancelled"
        return "confirmed" if self.confirmed_at else "awaiting"


class RegistrationDetails(UUIDModel):
    """One person in a batch: who, what they signed up for and what it costs (before the manual discount)."""

    class EntryKind(models.TextChoices):
        PLAYER = "player", _("player")
        VOLUNTEER = "volunteer", _("volunteer")

    batch = models.ForeignKey(RegistrationBatch, on_delete=models.CASCADE, related_name="details", verbose_name=_("registration"))
    membership = models.OneToOneField(ClubMembership, on_delete=models.CASCADE, related_name="registration_details", verbose_name=_("membership"))
    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="registration_details", verbose_name=_("member"))
    entry_kind = models.CharField(_("kind"), max_length=20, choices=EntryKind.choices, default=EntryKind.PLAYER)
    requested_team = models.ForeignKey("teams.Team", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("requested team"))
    requested_position = models.ForeignKey("teams.Position", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("requested position"))
    requested_jersey_number = models.PositiveSmallIntegerField(_("requested jersey number"), null=True, blank=True)
    product_variant = models.ForeignKey("shop.ProductVariant", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("product"))
    description = models.CharField(_("description"), max_length=255, blank=True, help_text=_("The product name when it was bought."))
    base_price = models.DecimalField(_("price"), max_digits=10, decimal_places=2, default=Decimal("0.00"))
    #: The registrant discount (tiers) already taken off ``base_price``; the line's price is ``base_price - discount_amount``.
    discount_amount = models.DecimalField(_("discount"), max_digits=10, decimal_places=2, default=Decimal("0.00"))
    #: Staff's override of the line price in the billing review (``None`` = the computed price).
    price_override = models.DecimalField(_("price override"), max_digits=10, decimal_places=2, null=True, blank=True)
    excluded = models.BooleanField(_("excluded"), default=False, help_text=_("Left out of the invoice in the billing review."))
    resulting_team_membership = models.ForeignKey("teams.TeamMembership", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name=_("resulting roster spot"))

    class Meta:
        verbose_name = _("registration entry")
        verbose_name_plural = _("registration entries")
        ordering = ["created"]

    def __str__(self):
        return f"{self.member} - {self.description}"

    @property
    def price(self) -> Decimal:
        """What this line costs before the manual discount: the override, else the computed price; nothing when excluded."""
        if self.excluded:
            return Decimal("0.00")
        if self.price_override is not None:
            return self.price_override
        return max(self.base_price - self.discount_amount, Decimal("0.00"))
