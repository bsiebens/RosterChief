import datetime
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField
from django_extensions.db.fields import AutoSlugField
from django_tenants.models import DomainMixin
from tenant_users.tenants.models import TenantBase, UserProfile

from rosterchief.base import UUIDModel


def club_logo_path(instance: Club, filename: str) -> str:
    return f"clubs/{instance.slug}/{filename}"


def club_event_background_path(instance: Club, filename: str) -> str:
    return f"clubs/{instance.slug}/event-background/{filename}"


class User(UserProfile):
    """Platform user (public schema). Email is the username."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=75)
    last_name = models.CharField(max_length=75)

    def __str__(self) -> str:
        return self.email

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["last_name", "first_name"]


class Club(TenantBase):
    """A club is a tenant. TenantBase provides owner, slug, created and modified."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = AutoSlugField(_("slug"), unique=True, populate_from=["name"])

    sport = models.ForeignKey("club.Sport", null=True, on_delete=models.PROTECT, related_name="clubs")

    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True, db_index=True, help_text=_("Set while the club is archived: its subdomain stops resolving but all data is kept."))

    legal_name = models.CharField(_("legal name"), max_length=255, blank=True, help_text=_("Full registered name (e.g. including a legal form like VZW/ASBL), used on official documents. Falls back to club name if blank."))
    contact_email = models.EmailField(_("contact email"), blank=True, help_text=_("The club's public address, shown to people the club writes to or asks to get in touch. Falls back to nothing being shown at all, so it's worth setting."))
    website = models.URLField(_("website"), blank=True, help_text=_("The club's own site, if it has one -- shown alongside its RosterChief pages, not used for anything else yet."))

    legal_address = models.CharField(
        _("legal address"),
        max_length=255,
        blank=True,
        help_text=_("Street address for official documents (invoices, the referee payment form). Falls back to the home location's address when left blank -- set this when the club's registered address isn't where it actually plays."),
    )
    legal_zip_code = models.CharField(_("legal zip code"), max_length=255, blank=True)
    legal_city = models.CharField(_("legal city"), max_length=255, blank=True)
    legal_country = CountryField(_("legal country"), blank=True)
    vat_id = models.CharField(_("VAT ID"), max_length=32, blank=True, help_text=_("VAT/tax registration number, if the club has one. Shown on invoices sent to or by the club when set."))

    payment_instructions = models.TextField(
        _("payment instructions"),
        blank=True,
        help_text=_("How to pay a fee (e.g. bank account/IBAN, or a preferred method) -- shown on the mobile Payments & dues page and the registration status page, and included on registration invoices. Falls back to nothing shown when left blank."),
    )

    logo = models.ImageField(_("logo"), upload_to=club_logo_path, blank=True, help_text=_("Shown on the club's own pages. Without one, the club's initials are used."))
    event_background = models.ImageField(
        _("event background"),
        upload_to=club_event_background_path,
        blank=True,
        help_text=_("A generic background photo for event screens in the mobile app, shown in black & white under a dark gradient. Without one, a plain dark background is used."),
    )
    primary_color = models.CharField(
        _("primary colour"), max_length=7, blank=True, validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Enter a colour as a hex value, e.g. #1e40af."))], help_text=_("Hex colour for buttons and links on the club's pages, e.g. #1e40af.")
    )
    secondary_color = models.CharField(
        _("secondary colour"),
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Enter a colour as a hex value, e.g. #be185d."))],
        help_text=_("Hex colour for highlights on the club's pages, e.g. avatar initials. Defaults to the theme's secondary colour."),
    )
    primary_text_color = models.CharField(
        _("text colour on primary"),
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Enter a colour as a hex value, e.g. #ffffff."))],
        help_text=_("Text colour on top of the primary colour, e.g. #ffffff. Leave blank to pick black or white automatically."),
    )
    secondary_text_color = models.CharField(
        _("text colour on secondary"),
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", _("Enter a colour as a hex value, e.g. #ffffff."))],
        help_text=_("Text colour on top of the secondary colour, e.g. #ffffff. Leave blank to pick black or white automatically."),
    )

    shop_enabled = models.BooleanField(_("shop enabled"), default=False, help_text=_("Shop status that controls when orders are accepted and the shop can be browsed by members. When closed items can be managed."))
    season_start = models.DateField(_("season start"), default=datetime.date(2000, 8, 1), help_text=_("Which day of the year a season begins — only the month and day are used, the year is ignored."))
    season_duration_months = models.PositiveSmallIntegerField(_("season duration (months)"), default=12, validators=[MinValueValidator(1), MaxValueValidator(24)], help_text=_("How many months a season lasts, counted from its start date."))

    auto_create_schema = True

    def __str__(self) -> str:
        return self.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    class Meta:
        verbose_name = _("club")
        verbose_name_plural = _("clubs")
        ordering = ["name"]

    def clean(self):
        errors = {}
        if self.primary_color.lower() == "#ffffff":
            errors["primary_color"] = _("White can't be used as the primary colour yet -- it breaks the contrast of several buttons and badges. Pick a different shade for now.")
        if self.secondary_color.lower() == "#ffffff":
            errors["secondary_color"] = _("White can't be used as the secondary colour yet -- it breaks the contrast of several buttons and badges. Pick a different shade for now.")
        if errors:
            raise ValidationError(errors)

    @property
    def primary_content_color(self) -> str:
        """Text colour to sit *on* ``primary_color``: the club's own
        ``primary_text_color`` when set, else the automatic pick (``_content_color_for``)."""
        if self.primary_color and self.primary_text_color:
            return self.primary_text_color
        return self._content_color_for(self.primary_color)

    @property
    def secondary_content_color(self) -> str:
        """Text colour to sit *on* ``secondary_color``: the club's own
        ``secondary_text_color`` when set, else the automatic pick (``_content_color_for``)."""
        if self.secondary_color and self.secondary_text_color:
            return self.secondary_text_color
        return self._content_color_for(self.secondary_color)

    @staticmethod
    def _content_color_for(hex_color: str) -> str:
        """Black or white, whichever reads on ``hex_color``.

        A club picking a pale yellow would otherwise get white-on-yellow buttons.
        Relative luminance per WCAG, with its 0.179 threshold for black vs white.
        """
        if not hex_color:
            return ""

        def channel(value: int) -> float:
            fraction = value / 255
            return fraction / 12.92 if fraction <= 0.04045 else ((fraction + 0.055) / 1.055) ** 2.4

        red, green, blue = (channel(int(hex_color[index : index + 2], 16)) for index in (1, 3, 5))
        luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue

        return "#000000" if luminance > 0.179 else "#ffffff"


class Domain(DomainMixin):
    pass


# Some other general models that will be used later on for different parts of the platform
class Sport(UUIDModel):
    """A club can be marked as belonging to a given sport. This would enable further things like specific competitions etc."""

    name = models.CharField(_("name"), max_length=250)
    slug = AutoSlugField(_("slug"), unique=True, populate_from=["name"])

    def __str__(self) -> str:
        return self.name

    class Meta:
        verbose_name = _("sport")
        verbose_name_plural = _("sports")
        ordering = ["name"]


class RefereeType(UUIDModel):
    """A type of referee or official for one sport (shared reference data, ordered for display)."""

    REFEREE, OFFICIAL = "referee", "official"
    KIND_CHOICES = [(REFEREE, _("Referee")), (OFFICIAL, _("Official"))]

    sport = models.ForeignKey(Sport, on_delete=models.CASCADE, related_name="referee_types")
    kind = models.CharField(_("kind"), max_length=10, choices=KIND_CHOICES, default=REFEREE)
    name = models.CharField(_("name"), max_length=100)
    ordering = models.PositiveSmallIntegerField(_("ordering"), default=0, help_text=_("Lower comes first."))

    class Meta:
        verbose_name = _("referee type")
        verbose_name_plural = _("referee types")
        ordering = ["sport", "kind", "ordering", "name"]
        constraints = [models.UniqueConstraint(fields=["sport", "kind", "name"], name="unique_referee_type_name_per_sport_kind")]

    def __str__(self) -> str:
        return self.name


class Competition(UUIDModel):
    """A competition clubs can import fixtures from. Which clubs may use it is decided by ``feature`` (a catalog feature)."""

    name = models.CharField(_("name"), max_length=250)
    module = models.CharField(_("module"), max_length=250, help_text=_("Dotted path of the module with the class that fetches fixtures."))
    sport = models.ForeignKey(Sport, null=True, blank=True, on_delete=models.PROTECT, related_name="competitions")
    feature = models.ForeignKey("features.Feature", null=True, blank=True, on_delete=models.PROTECT, related_name="competitions", help_text=_("Clubs need this feature to use the competition."))

    class Meta:
        verbose_name = _("competition")
        verbose_name_plural = _("competitions")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
