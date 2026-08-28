from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership, Season
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope
from shop.models import ProductVariant
from teams.models import Position, StaffAssignment, Team


class RegistrationBatch(ClubScopedModel):
    """One registration session -- a parent registering several kids at once,
    a single self-registration, or a re-registration for a new season.
    Purely for grouping + audit: which entries came in together (for the
    Product-level min_registrants discount, see registration.services.pricing)
    and who filled the form in. Deliberately carries no status of its own --
    each entry becomes a real Member + ClubMembership(status=PENDING)
    immediately (registration.services.submission.submit_registration), and
    review from there on is the *existing* Sign-up queue
    (club.services.onboarding), not a second gate this model would add."""

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="registration_batches", verbose_name=_("season"))
    submitted_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="registration_batches", verbose_name=_("submitted by"), blank=True, null=True)

    contact_first_name = models.CharField(_("contact first name"), max_length=150)
    contact_last_name = models.CharField(_("contact last name"), max_length=150)
    contact_email = models.EmailField(_("contact email"))
    contact_phone = models.CharField(_("contact phone"), max_length=50, blank=True)

    subtotal = models.DecimalField(_("subtotal"), max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(
        _("discount amount"), max_digits=10, decimal_places=2, default=0, help_text=_("The immediate (min-registrants) portion only -- an early-payment discount isn't confirmed until paid, see ClubMembership.early_payment_discount.")
    )
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("registration batch")
        verbose_name_plural = _("registration batches")
        ordering = ["-created"]

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("season",))

    def __str__(self):
        return f"{self.contact_first_name} {self.contact_last_name} ({self.season})"


class RegistrationDetails(UUIDModel):
    """What was actually requested when a ClubMembership came from this
    module -- a team/role preference and which price line was chosen, kept
    separate from ClubMembership itself so that model stays exactly what it
    already was (club/services/onboarding.py's own queue, unchanged) rather
    than growing registration-specific fields. Not itself club-scoped -- its
    club is reached through ``membership``, same reasoning as
    club.models.FeePayment.

    A plain FK, not OneToOne -- one membership (one person, one club, one
    season) can carry more than one of these: a kid playing on two teams, or
    playing *and* refereeing, is two separate requests against the same
    season-long ClubMembership, not two memberships (ClubMembership has
    ``unique_member_per_club_per_season`` -- see club.models). registration.
    services.submission.submit_registration always creates a new row here
    rather than updating an existing one, adding the new entry's price onto
    the membership's own fee_amount -- see that function's own docstring."""

    class EntryKind(models.TextChoices):
        PLAYER = "player", _("Player")
        VOLUNTEER = "volunteer", _("Volunteer")

    membership = models.ForeignKey(ClubMembership, on_delete=models.CASCADE, related_name="registration_details", verbose_name=_("membership"))
    batch = models.ForeignKey(RegistrationBatch, on_delete=models.CASCADE, related_name="entries", verbose_name=_("batch"))

    entry_kind = models.CharField(_("entry kind"), max_length=20, choices=EntryKind.choices, default=EntryKind.PLAYER)

    #: A request, not a placement -- see SignupPlaceInTeamView (players) and
    #: the Volunteer list's own placement action (volunteers), both of which
    #: still create the real TeamMembership/StaffAssignment by hand. Null also
    #: covers "volunteer, no team preference yet".
    requested_team = models.ForeignKey(Team, on_delete=models.SET_NULL, related_name="registration_requests", verbose_name=_("requested team"), blank=True, null=True)
    requested_position = models.ForeignKey(Position, on_delete=models.SET_NULL, related_name="registration_requests", verbose_name=_("requested position"), blank=True, null=True, limit_choices_to={"staff_position": True})

    product_variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, related_name="registration_requests", verbose_name=_("product variant"), blank=True, null=True)
    price = models.DecimalField(_("price"), max_digits=10, decimal_places=2, default=0, help_text=_("This entry's own share of the batch subtotal, before any discount."))
    discount_amount = models.DecimalField(_("discount amount"), max_digits=10, decimal_places=2, default=0, help_text=_("This entry's own share of the batch's immediate (min-registrants) discount."))

    resulting_staff_assignment = models.OneToOneField(StaffAssignment, on_delete=models.SET_NULL, related_name="registration_details", verbose_name=_("resulting staff assignment"), blank=True, null=True)

    class Meta:
        verbose_name = _("registration details")
        verbose_name_plural = _("registration details")
        ordering = ["batch", "created"]

    def clean(self):
        if self.entry_kind == self.EntryKind.PLAYER and self.requested_position_id:
            raise ValidationError({"requested_position": _("Only a volunteer entry can request a position.")})

    def __str__(self):
        return f"{self.membership} ({self.get_entry_kind_display()})"
