import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.models import ClubMembership, Season
from rosterchief.base import ClubScopedModel, UUIDModel, validate_club_scope
from shop.models import ProductVariant
from teams.models import Position, StaffAssignment, Team, TeamMembership


def _generate_status_token() -> str:
    return secrets.token_urlsafe(32)


class RegistrationBatch(ClubScopedModel):
    """One registration session -- a parent registering several kids at once,
    a single self-registration, or a re-registration for a new season.
    Purely for grouping + audit: which entries came in together (for the
    Product-level min_registrants discount, see registration.services.pricing)
    and who filled the form in. Deliberately carries no *onboarding* status of
    its own -- each entry becomes a real Member + ClubMembership(status=PENDING)
    immediately (registration.services.submission.submit_registration), but
    review from there on is gated behind this batch's own *billing* status
    (invoice_sent_at and friends, below): a fresh registration is invisible
    to the Sign-up queue (club.services.onboarding, management.views.
    SignupDashboardView) until staff has reviewed and confirmed its invoice
    on the Registrations screen -- see registration.services.invoicing's own
    module docstring for why. Billing review always comes first; once
    confirmed, onboarding review (documents, team placement, approval) and
    payment can each proceed independently of the other from there.

    ``status_token`` is the unguessable key behind the public status page
    (registration.views.RegistrationStatusView) -- same secrets.token_urlsafe(32)
    "possession of the link is the credential" idea as mobile.models.
    CalendarFeedToken, since whoever submitted this may have no account at
    all. The confirmation email (registration.services.notifications.
    send_registration_confirmation_email) is what hands that link out -- it
    carries no money/invoice content itself, on purpose, since none of that
    exists yet at submission time."""

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="registration_batches", verbose_name=_("season"))
    submitted_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name="registration_batches", verbose_name=_("submitted by"), blank=True, null=True)
    status_token = models.CharField(_("status token"), max_length=64, unique=True, editable=False, default=_generate_status_token)

    contact_first_name = models.CharField(_("contact first name"), max_length=150)
    contact_last_name = models.CharField(_("contact last name"), max_length=150)
    contact_email = models.EmailField(_("contact email"))
    contact_phone = models.CharField(_("contact phone"), max_length=50, blank=True)

    subtotal = models.DecimalField(_("subtotal"), max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(
        _("discount amount"), max_digits=10, decimal_places=2, default=0, help_text=_("The immediate (min-registrants) portion only -- an early-payment discount isn't confirmed until paid, see ClubMembership.early_payment_discount.")
    )
    total = models.DecimalField(_("total"), max_digits=10, decimal_places=2, default=0)

    #: Presence of invoice_sent_at is the confirmation flag read everywhere --
    #: the family-facing status page/mobile app/invoice PDF all stay hidden
    #: until it's set, and it's what "registrations awaiting confirmation"
    #: (registration.services.invoicing.registrations_awaiting_confirmation)
    #: filters on. Set only by registration.services.invoicing.
    #: confirm_and_send_invoice, never by submit_registration.
    invoice_number = models.CharField(_("invoice number"), max_length=255, blank=True)
    invoice_sent_at = models.DateTimeField(_("invoice sent at"), null=True, blank=True)
    invoice_due_date = models.DateField(_("invoice due date"), null=True, blank=True)

    manual_discount_amount = models.DecimalField(_("manual discount amount"), max_digits=10, decimal_places=2, default=0, help_text=_("An extra discount staff can apply when confirming the invoice, on top of any multi-registrant discount."))
    manual_discount_note = models.CharField(_("manual discount note"), max_length=255, blank=True, help_text=_("Shown as the discount's own label on the invoice, e.g. “Loyalty discount”. Falls back to a generic label when left blank."))

    invoice_last_reminder_sent_at = models.DateTimeField(_("invoice last reminder sent at"), null=True, blank=True)
    invoice_reminder_count = models.PositiveIntegerField(_("invoice reminders sent"), default=0)

    class Meta:
        verbose_name = _("registration batch")
        verbose_name_plural = _("registration batches")
        ordering = ["-created"]
        constraints = [
            UniqueConstraint(fields=["club", "invoice_number"], name="unique_registration_invoice_number_per_club", condition=~models.Q(invoice_number="")),
        ]

    def clean(self):
        validate_club_scope(self, self.club_id, same_club_fields=("season",))

    def __str__(self):
        return f"{self.contact_first_name} {self.contact_last_name} ({self.season})"

    def generate_invoice_number(self) -> str:
        """Next per-club registration invoice number for the current year:
        ``REG-<year>-<seq>``. Same numbering shape as club.models.DuesInvoice's
        own generate_number, duplicated rather than shared across apps -- see
        that method's own docstring on why. Allocated explicitly by
        registration.services.invoicing.confirm_and_send_invoice, NOT on
        every save() the way DuesInvoice's own is: a batch is saved many
        times before it's ever confirmed (subtotal/discount_amount/total at
        submission), and none of those saves should mint a number."""
        prefix = f"REG-{timezone.now().year}-"
        sequences = [int(suffix) for existing in RegistrationBatch.objects.filter(club=self.club, invoice_number__startswith=prefix).values_list("invoice_number", flat=True) if (suffix := existing.removeprefix(prefix)).isdigit()]
        return f"{prefix}{max(sequences, default=0) + 1:05d}"


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
    #: A request, not a placement -- same as requested_team/requested_position
    #: above. Only meaningful for a player entry with a requested_team whose
    #: pool is set; see teams.services.numbers for the availability rules and
    #: SignupPlaceInTeamView/the team roster forms for where this becomes a
    #: real TeamMembership.jersey_number.
    requested_jersey_number = models.PositiveSmallIntegerField(_("requested jersey number"), blank=True, null=True)

    product_variant = models.ForeignKey(ProductVariant, on_delete=models.SET_NULL, related_name="registration_requests", verbose_name=_("product variant"), blank=True, null=True)
    #: Staff-editable up until the batch's own invoice is confirmed/sent
    #: (registration.services.invoicing.active_batch_entries), via the
    #: Registrations review screen -- see RegistrationBatch's own docstring
    #: on the billing review step. Unedited, this is simply what
    #: registration.services.submission.submit_registration priced it at.
    price = models.DecimalField(_("price"), max_digits=10, decimal_places=2, default=0, help_text=_("This entry's own share of the batch subtotal, before any discount."))
    discount_amount = models.DecimalField(_("discount amount"), max_digits=10, decimal_places=2, default=0, help_text=_("This entry's own share of the batch's immediate (min-registrants) discount."))
    #: Staff can drop one person's charge from the invoice without deleting
    #: this row -- the roster request/onboarding data it carries
    #: (requested_team, jersey number, ...) has to survive regardless of the
    #: billing decision.
    excluded_from_invoice = models.BooleanField(_("excluded from invoice"), default=False)

    #: What requested_team/requested_jersey_number actually became once staff
    #: confirmed them on the Registrations review screen (management.views.
    #: RegistrationInvoiceReviewView) -- players' own counterpart to
    #: resulting_staff_assignment below. club.services.cancellation.
    #: cancel_membership deletes this specific TeamMembership (only this
    #: one, not every placement the member happens to have that season) when
    #: this membership is later cancelled.
    resulting_team_membership = models.OneToOneField(TeamMembership, on_delete=models.SET_NULL, related_name="registration_details", verbose_name=_("resulting team membership"), blank=True, null=True)
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
