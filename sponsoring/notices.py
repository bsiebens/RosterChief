"""Notices around a sponsor being paused for non-payment: the heads-up, the suspension and the reactivation. Each goes to the sponsor's contact by
email and to the club's sponsor managers (inbox plus push). Never fatal: a failed mail or push must not undo the state change."""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.utils.translation import gettext as _

from rosterchief.mail import send_message

log = logging.getLogger(__name__)


def notify_managers(sponsor, title: str, body: str) -> int:
    """Everyone who may manage sponsors in this club (sponsor managers and club owners): inbox entry and push linking to the billing page."""
    from tenant_users.permissions.models import UserTenantPermissions

    from events.services.attendance import current_club
    from members.services import access
    from notifications import inbox

    club, told = current_club(), 0
    for profile in UserTenantPermissions.objects.select_related("profile"):
        user = profile.profile
        try:
            if user.is_active and access.can_manage_sponsors(user):
                inbox.notify(user, club, title=title, body=body, kind=inbox.Kind.INFO, href=f"/finance/sponsors/{sponsor.pk}", push=True, category="general")
                told += 1
        except Exception:
            log.exception("sponsoring.notify_failed")
    return told


def _mail(agreement, subject: str, lines: list[str]) -> bool:
    message = EmailMultiAlternatives(subject, "\n\n".join(lines).strip() + "\n", settings.DEFAULT_FROM_EMAIL, [agreement.contact_email])
    try:
        return send_message(message, fail_silently=False)
    except Exception:
        log.warning("sponsoring.mail_failed", exc_info=True)
        return False


def _facts(installment, due_date) -> dict:
    return {"amount": f"€{installment.amount:.2f}", "date": due_date.strftime("%d/%m/%Y")}


def send_suspended(agreement, installment, due_date) -> None:
    sponsor = agreement.sponsor
    d = _facts(installment, due_date)
    _mail(agreement, _("Your sponsorship of the club is paused"), [
        _("Hi %(name)s,") % {"name": agreement.contact_name},
        _("The sponsorship of %(sponsor)s is paused because an installment of %(amount)s due on %(date)s is unpaid. Pay it to be shown again.") % {"sponsor": sponsor.name, **d},
    ])  # fmt: skip
    notify_managers(sponsor, _("Sponsor paused: %(sponsor)s") % {"sponsor": sponsor.name}, _("Installment of %(amount)s due on %(date)s is unpaid; the sponsor is no longer shown.") % d)
    agreement.suspension_notified_at = timezone.now()
    agreement.save(update_fields=["suspension_notified_at", "modified"])


def send_reactivated(agreement) -> None:
    sponsor = agreement.sponsor
    _mail(agreement, _("Your sponsorship of the club is active again"), [
        _("Hi %(name)s,") % {"name": agreement.contact_name},
        _("The sponsorship of %(sponsor)s is active again and you are shown once more. Thank you!") % {"sponsor": sponsor.name},
    ])  # fmt: skip
    notify_managers(sponsor, _("Sponsor active again: %(sponsor)s") % {"sponsor": sponsor.name}, _("The sponsor is shown again."))
    agreement.reactivation_notified_at = timezone.now()
    agreement.save(update_fields=["reactivation_notified_at", "modified"])


def send_heads_up(agreement, installment, due_date, days_left: int) -> None:
    sponsor = agreement.sponsor
    d = {**_facts(installment, due_date), "days": max(days_left, 0)}
    _mail(agreement, _("Your sponsorship will be paused soon"), [
        _("Hi %(name)s,") % {"name": agreement.contact_name},
        _("The installment of %(amount)s due on %(date)s of the sponsorship of %(sponsor)s is still unpaid. Pay it within %(days)s days to stay visible.") % {"sponsor": sponsor.name, **d},
    ])  # fmt: skip
    notify_managers(sponsor, _("Sponsor will be paused: %(sponsor)s") % {"sponsor": sponsor.name}, _("Installment of %(amount)s due on %(date)s is unpaid; it is paused in %(days)s days.") % d)
    installment.warned_at = timezone.now()
    installment.save(update_fields=["warned_at", "modified"])
