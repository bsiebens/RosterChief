"""What a club's own admins are told about money they owe the platform.

Separate from dues.py because the audience is different: everything in dues.py is read by
platform staff in the control panel, and this is the one piece of billing a *club* sees. It
returns data, never rendered text — the wording lives in the template so it can be translated,
and the same notice feeds both the on-screen banner and the reminder email.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.utils import timezone

from billing.models import Due

#: Inside this many days of being archived, the notice stops being a warning and becomes a
#: final one — which is also the point at which it follows the admin onto every page.
URGENT_DAYS = 7

INFO = "info"
WARNING = "warning"
ERROR = "error"


@dataclass(frozen=True)
class BillingNotice:
    """The single most pressing thing a club owes, and how alarmed to be about it."""

    level: str
    due: Due
    amount_outstanding: Decimal
    period_start: date
    grace_until: date
    days_until_archive: int
    #: False when the subscription has auto_archive off. Money is still owed and still worth
    #: saying so, but the countdown must not claim an archiving that will never happen.
    will_archive: bool

    @property
    def is_urgent(self) -> bool:
        return self.level == ERROR


def club_billing_notice(club, today: date | None = None) -> BillingNotice | None:
    """The notice for ``club``, or None when it owes nothing.

    Picks the due with the earliest ``grace_until`` when several are owing: that is the one
    that will archive the club first, so it is the one worth shouting about.
    """
    today = today or timezone.localdate()

    due = club.dues.filter(status__in=Due.OWING).select_related("plan").order_by("grace_until").first()
    if due is None:
        return None

    subscription = getattr(club, "subscription", None)
    will_archive = subscription.auto_archive if subscription is not None else False
    days_left = due.days_until_archive(today)

    if due.is_overdue(today):
        level = ERROR
    elif due.is_in_grace(today):
        level = ERROR if days_left <= URGENT_DAYS else WARNING
    else:
        # Issued during the plan's renewal lead window: billed, but nothing is late yet.
        level = INFO

    return BillingNotice(
        level=level,
        due=due,
        amount_outstanding=due.balance,
        period_start=due.period_start,
        grace_until=due.grace_until,
        days_until_archive=days_left,
        will_archive=will_archive,
    )
