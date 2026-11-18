"""Heads-up for the people who handle payments, ``LEAD_DAYS`` days before money falls due (inbox + push, category "admin").

``send_due`` is the daily sweep for the current club schema. For every confirmed, not cancelled registration it looks at the due dates still to
come: each instalment of a plan, or the invoice due date when there is no plan. When one of them is exactly 10 or 5 days away and part of it is
still open, the people who manage payments (``can_manage_finance``, the Owner role included) get one message per lead time that says how many
registrations that is, what is still open, and to check that everything received so far has been registered correctly, so nobody who paid is
chased or blocked from events by mistake. Nothing goes out when nothing is open, and nobody gets the same lead time twice on one day."""

import datetime
import logging
from decimal import Decimal

from django.utils import timezone

from members.models import Member
from registration.models import RegistrationBatch
from registration.services import installments
from registration.services import payments as pay

logger = logging.getLogger(__name__)

LEAD_DAYS = (10, 5)
DEFAULT_LANGUAGE = "nl"
ZERO = Decimal("0.00")
HREF = "/finance?payment=open"

TEXTS = {
    "nl": {
        "title": "Betalingen vervallen over {days} dagen",
        "body": "{count} inschrijving(en) hebben een betaling die vervalt op {date}; nog {amount} openstaand. Controleer of alle ontvangen betalingen correct zijn geregistreerd.",
    },
    "en": {
        "title": "Payments fall due in {days} days",
        "body": "{count} registration(s) have a payment due on {date}, {amount} still open. Check that everything received so far has been registered correctly.",
    },
    "fr": {
        "title": "Paiements dus dans {days} jours",
        "body": "{count} inscription(s) ont un paiement dû le {date}, {amount} encore ouvert. Vérifiez que tout ce qui a été reçu est correctement enregistré.",
    },
}


def _lang(code: str | None) -> str:
    code = (code or "").split("-")[0].lower()
    return code if code in TEXTS else DEFAULT_LANGUAGE


def finance_managers():
    """The active users of this club who may handle registration payments."""
    from tenant_users.permissions.models import UserTenantPermissions

    from members.services.access import can_manage_finance

    return [perms.profile for perms in UserTenantPermissions.objects.select_related("profile").filter(profile__is_active=True) if can_manage_finance(perms.profile)]


def upcoming(today: datetime.date) -> dict[int, dict]:
    """Per lead time: how many registrations have a payment falling due exactly then that is not (fully) paid, the amount open and the due date."""
    found: dict[int, dict] = {}
    batches = (
        RegistrationBatch.objects.filter(confirmed_at__isnull=False, cancelled_at__isnull=True, invoice_due_date__isnull=False)
        .select_related("season")
        .prefetch_related("details__membership__payments")
    )  # fmt: skip
    wanted = {today + datetime.timedelta(days=days): days for days in LEAD_DAYS}
    for batch in batches.iterator(chunk_size=100):
        if batch.installment_offsets:
            rows = [(row["due_date"], row["remaining"]) for row in installments.batch_schedule(batch, today=today)[0]]
        else:
            rows = [(batch.invoice_due_date, pay.summary(batch, today=today)["balance"])]
        for due_date, remaining in rows:
            days = wanted.get(due_date)
            if days is None or remaining <= 0:
                continue
            entry = found.setdefault(days, {"count": 0, "amount": ZERO, "date": due_date})
            entry["count"] += 1
            entry["amount"] += remaining
    return found


def send_due(today: datetime.date | None = None) -> dict:
    """The sweep, in the current club schema. Returns ``{"notices": n, "told": accounts}``."""
    from events.services.attendance import current_club
    from notifications import inbox
    from notifications.models import InboxNotification

    club = current_club()
    if club is None:
        return {"notices": 0, "told": 0}
    today = today or timezone.localdate()
    found = upcoming(today)
    if not found:
        return {"notices": 0, "told": 0}
    managers = finance_managers()
    languages = dict(Member.objects.filter(user__in=managers).values_list("user_id", "language"))
    notices = told = 0
    for days, entry in sorted(found.items(), reverse=True):
        for user in managers:
            texts = TEXTS[_lang(languages.get(user.pk))]
            title = texts["title"].format(days=days)
            if InboxNotification.objects.filter(user=user, club=club, title=title, created__date=today).exists():
                continue  # already told today (the task ran twice)
            body = texts["body"].format(count=entry["count"], date=f"{entry['date']:%d/%m/%Y}", amount=f"{entry['amount']:.2f}")
            try:
                inbox.notify(user, club, title=title, body=body, kind=inbox.Kind.INFO, href=HREF, push=True, category="admin")
            except Exception:  # one broken delivery must not stop the sweep
                logger.exception("Payment reminder for user %s failed", user.pk)
                continue
            told += 1
        notices += 1
    return {"notices": notices, "told": told}
