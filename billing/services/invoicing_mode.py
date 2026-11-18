"""Automatic invoicing on or off (``BillingSettings.auto_invoicing``) and the warnings that go with "off".

On (the default) the daily ``billing.renew_subscriptions`` job opens each club's next period and issues the invoice with the next
automatic number, exactly as before. Off, nothing is opened or invoiced by the platform: a platform admin opens the period (without an
invoice) and creates the invoice by hand with a typed number (invoices may be shared with another platform whose numbering has to be
kept in step, so no automatic number is ever taken). While it is off, every platform admin gets

* a **weekly digest** of the clubs already inside their invoicing window (the next period starts within the plan's
  ``renewal_lead_days``) or entering it within the next 7 days, and
* a **first-time alert** the day a club enters its window (recorded in ``InvoicingAlert``, so once per club and period).
"""

import datetime

from django.db.models import Exists, OuterRef

from billing.emails import send_branded, texts

from ..models import BillingSettings, Due, Invoice, InvoicingAlert
from . import BillingError
from . import dues as dues_svc

DIGEST_DAYS_AHEAD = 7


def auto_invoicing_enabled() -> bool:
    return BillingSettings.current().auto_invoicing


def needs_invoice(day: datetime.date | None = None, ahead_days: int = 0) -> list[dict]:
    """Clubs that need an invoice made by hand, soonest first. Two kinds of row:

    * ``open``: the next period is not opened yet and its invoicing window opens on or before ``day + ahead_days``
      (same selection as ``upcoming_renewals``: auto-renew clubs, plus clubs that lapsed);
    * ``invoice``: a period exists (opened by hand) but has no current invoice, and its window has opened.

    Each row: ``kind``, ``club``, ``plan``, ``period_start``, ``opens_on``, ``days`` (negative = the window opened that many
    days ago), ``due`` (the period, for ``invoice`` rows) and ``amount`` (the total incl. VAT of the period when it can be quoted).
    """
    day = day or dues_svc.today()
    limit = day + datetime.timedelta(days=ahead_days)
    rows = []
    for r in dues_svc.upcoming_renewals(day):
        if r["opened"] or r["invoice_opens_on"] > limit:
            continue
        rows.append({"kind": "open", "club": r["club"], "plan": r["plan"], "period_start": r["next_period_start"], "opens_on": r["invoice_opens_on"], "due": None})
    uninvoiced = (
        Due.objects.filter(club__archived_at__isnull=True)
        .exclude(status=Due.CANCELLED)
        .annotate(has_invoice=Exists(Invoice.objects.filter(due=OuterRef("pk"), voided_at__isnull=True)))
        .filter(has_invoice=False)
        .select_related("club", "plan")
    )
    for due in uninvoiced:
        lead = dues_svc.cycle_of_due(due).renewal_lead_days
        opens_on = due.period_start - datetime.timedelta(days=lead)
        if opens_on <= limit:
            rows.append({"kind": "invoice", "club": due.club, "plan": due.plan, "period_start": due.period_start, "opens_on": opens_on, "due": due})
    for row in rows:
        row["days"] = (row["opens_on"] - day).days
        row["amount"] = _amount(row)
    return sorted(rows, key=lambda r: (r["opens_on"], r["club"].name))


def _amount(row) -> str | None:
    if row["due"] is not None:
        return f"{row['due'].total:.2f}"
    try:
        return f"{dues_svc.quote_period(row['club'], row['plan'], row['period_start']).total:.2f}"
    except BillingError:
        return None


def recipients() -> list[str]:
    from controlpanel.services.platform_admins import platform_admins

    return [p.profile.email for p in platform_admins() if p.profile.is_active and p.profile.email]


def _line(row: dict) -> tuple[str, str]:
    when = f"window opened {-row['days']} day(s) ago" if row["days"] < 0 else ("window opens today" if row["days"] == 0 else f"window opens in {row['days']} day(s)")
    what = "invoice missing" if row["kind"] == "invoice" else "period not opened"
    amount = f" · EUR {row['amount']}" if row["amount"] else ""
    return row["club"].name, f"{row['plan'].name} · period from {row['period_start']} · {what} · {when}{amount}"


def _send(rows: list[dict], *, subject_key: str, heading_key: str, intro_key: str) -> bool:
    to = recipients()
    if not rows or not to:
        return False
    t = texts(count=len(rows))
    lines = [_line(r) for r in rows]
    body = "\n".join([t[intro_key], "", *(f"- {name}: {text}" for name, text in lines)])
    return send_branded(subject=t[subject_key], text=body, template="billing/email/notice.html", ctx={"t": {**t, "notice_heading": t[heading_key], "notice_intro": t[intro_key]}, "rows": lines}, to=to, fail_silently=True)


def send_digest(day: datetime.date | None = None) -> int:
    """The weekly digest. Nothing when automatic invoicing is on or nothing needs an invoice. Returns the number of clubs listed."""
    if auto_invoicing_enabled():
        return 0
    rows = needs_invoice(day, DIGEST_DAYS_AHEAD)
    if rows:
        _send(rows, subject_key="digest_subject", heading_key="digest_heading", intro_key="digest_intro")
    return len(rows)


def send_window_alerts(day: datetime.date | None = None) -> int:
    """Mail the clubs that entered their invoicing window and were not alerted for that period yet (one mail listing them all)."""
    if auto_invoicing_enabled():
        return 0
    rows = [r for r in needs_invoice(day, 0) if not InvoicingAlert.objects.filter(club=r["club"], period_start=r["period_start"]).exists()]
    if not rows:
        return 0
    if _send(rows, subject_key="alert_subject", heading_key="alert_heading", intro_key="alert_intro"):
        InvoicingAlert.objects.bulk_create([InvoicingAlert(club=r["club"], period_start=r["period_start"]) for r in rows], ignore_conflicts=True)
    return len(rows)


def pending_count(day: datetime.date | None = None) -> int:
    """Clubs that need an invoice right now (window opened); what the banner in the control panel shows."""
    return len(needs_invoice(day, 0))


# kept for symmetry with the other services: the manual flow lives in ``dues.open_period(invoice=False)`` and ``dues.issue_period_invoice``
__all__ = ["auto_invoicing_enabled", "needs_invoice", "pending_count", "send_digest", "send_window_alerts"]
