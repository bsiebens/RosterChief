"""The public marketing site (rosterchief.app, ``request.club is None`` -- see
club/views.py:root and marketing/views.py). Platform data, same posture as
billing.Plan: nothing here inherits ClubScopedModel, since a lead has not
chosen a club yet -- ``club`` below is just the free-text name they typed.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from rosterchief.base import UUIDModel


class ContactRequest(UUIDModel):
    """A "Book a demo" submission from the marketing homepage's contact form.

    Persisting this matters more than the notification email (see
    design_handoff_rosterchief_platform/WEBSITE.md, "The contact form must be
    built server-side") -- the mail can fail to send or get lost in a spam
    folder, this row can't.
    """

    class Role(models.TextChoices):
        BOARD_SECRETARY = "board_secretary", _("Board / secretary")
        TREASURER = "treasurer", _("Treasurer")
        COACH_MANAGER = "coach_manager", _("Coach / team manager")
        FEDERATION = "federation", _("Federation")
        OTHER = "other", _("Something else")

    name = models.CharField(_("name"), max_length=150)
    email = models.EmailField(_("email"))
    club = models.CharField(_("club"), max_length=150)
    members = models.PositiveIntegerField(_("members"), null=True, blank=True)
    role = models.CharField(_("role"), max_length=20, choices=Role.choices)
    message = models.TextField(_("message"), blank=True)

    # The raw ?plan= query string a pricing CTA was clicked with (club/large/website),
    # if any -- see WEBSITE.md item 6. Free text, not a choices field: it just names
    # which button sent the visitor here, for the notification email to surface.
    plan_interest = models.CharField(_("plan interest"), max_length=20, blank=True)

    ip = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    user_agent = models.CharField(_("user agent"), max_length=300, blank=True)

    class Meta:
        verbose_name = _("contact request")
        verbose_name_plural = _("contact requests")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.name} ({self.club})"
