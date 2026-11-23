"""A guest team's tournament fees as invoices in the club's finance. The deposit is invoiced when the team is accepted, the balance (entry
fee minus the deposit, plus lunches) once the roster is final; both are shop *charges*, so they live and are paid in finance like any other
invoice, and the team's payment status follows the payments instead of being set by hand.

* The payer of an outside team is its contact person, kept as a member record without membership (finance needs a member to bill).
* A sent invoice is never edited: a roster that changes after the balance invoice was sent is handled in finance (a supplementary charge).
* Home (club) teams are not invoiced. A tournament without fees creates nothing."""

import datetime
import logging
from decimal import Decimal

from django.db.models import Q
from django.utils.translation import gettext as _

from members.models import Member
from shop.models import Order
from shop.services import charges, payers, payments

from ..models import Tournament, TournamentTeam

log = logging.getLogger(__name__)

#: The balance is invoiced this many days before the tournament starts (the roster is final by then), by the daily job.
BALANCE_LEAD_DAYS = 7


def lunch_totals(t: Tournament, rows) -> tuple[Decimal, Decimal, int]:
    """(mandatory lunch total, total lunch cost, person-days) for the people in ``rows`` at the tournament's price per day."""
    if t.lunch_mode == Tournament.LunchMode.NONE:
        return Decimal("0"), Decimal("0"), 0
    price = t.lunch_price_per_day or Decimal("0")
    if t.lunch_mode == Tournament.LunchMode.MANDATORY:
        days = len(t.date_range()) * len(rows)
        return price * days, price * days, days
    days = sum(len(r.lunch_days or []) for r in rows)
    return Decimal("0"), price * days, days


def billable(team: TournamentTeam) -> bool:
    return team.team_id is None and team.withdrawn_at is None


def payer_for(team: TournamentTeam) -> Member:
    """The contact person as a member record (found by email, else made): the person the invoices are addressed to."""
    if team.payer_id:
        return team.payer
    member = payers.external_payer(team.foreign_contact_name or team.foreign_name, team.foreign_contact_email, fallback_name=team.foreign_name)
    team.payer = member
    team.save(update_fields=["payer"])
    return member


def _alive(order) -> bool:
    return order is not None and order.fulfillment_status != Order.FulfillmentStatus.CANCELLED


def ensure_deposit_invoice(team: TournamentTeam, actor=None) -> Order | None:
    """Invoice the deposit once (when there is one to ask for). Returns the order, or None when nothing is due."""
    t = team.tournament
    if not billable(team) or _alive(team.deposit_order) or not t.deposit_amount or t.deposit_amount <= 0:
        return team.deposit_order if _alive(team.deposit_order) else None
    order = charges.create_draft(actor, member=payer_for(team), lines=[charges.ChargeLine(_("Deposit %(team)s, %(tournament)s") % {"team": team.display_name, "tournament": t.name}, 1, t.deposit_amount)])
    charges.send(actor, order)
    team.deposit_order = order
    team.save(update_fields=["deposit_order"])
    refresh_status(team)
    return order


def balance_lines(team: TournamentTeam) -> list:
    t = team.tournament
    fee = t.entry_fee_amount or Decimal("0")
    deposit = (team.deposit_order.total if _alive(team.deposit_order) else Decimal("0"))
    lines = []
    rest = fee - deposit
    if rest > 0:
        label = _("Entry fee %(team)s, %(tournament)s") % {"team": team.display_name, "tournament": t.name}
        lines.append(charges.ChargeLine(label + (_(" (balance after deposit)") if deposit else ""), 1, rest))
    if t.lunch_billed_with_fee:
        _mandatory, _total, days = lunch_totals(t, list(team.roster_entries.all()))
        if days and t.lunch_price_per_day:
            lines.append(charges.ChargeLine(_("Lunch %(team)s (person-days)") % {"team": team.display_name}, days, t.lunch_price_per_day))
    return lines


def ensure_balance_invoice(team: TournamentTeam, actor=None) -> Order | None:
    """Invoice the rest (entry fee minus deposit, plus lunches for the roster as it is now) once. None when nothing is left to bill."""
    if not billable(team) or _alive(team.balance_order):
        return team.balance_order if _alive(team.balance_order) else None
    lines = balance_lines(team)
    if not lines:
        return None
    order = charges.create_draft(actor, member=payer_for(team), lines=lines)
    charges.send(actor, order)
    team.balance_order = order
    team.save(update_fields=["balance_order"])
    refresh_status(team)
    return order


def refresh_status(team: TournamentTeam) -> None:
    """The team's payment status from its invoices' payments. Teams without invoices keep the hand-set status."""
    orders = [o for o in (team.deposit_order, team.balance_order) if _alive(o)]
    if not orders:
        return
    paid = sum((payments.amount_paid(o) for o in orders), Decimal("0"))
    billed = sum((o.total for o in orders), Decimal("0"))
    deposit = team.tournament.deposit_amount or Decimal("0")
    Status = TournamentTeam.PaymentStatus
    if _alive(team.balance_order) and paid >= billed:
        new = Status.FULLY_PAID
    elif deposit > 0 and paid >= deposit:
        new = Status.DEPOSIT_RECEIVED
    elif not _alive(team.balance_order) and paid >= billed > 0 and not deposit:
        new = Status.FULLY_PAID
    else:
        new = Status.UNPAID
    if team.payment_status != new:
        team.payment_status = new
        team.save(update_fields=["payment_status"])


def on_order_changed(order) -> None:
    """Called by finance after a payment or a cancellation: update the team an invoice belongs to."""
    for team in TournamentTeam.objects.filter(Q(deposit_order=order) | Q(balance_order=order)).select_related("tournament", "deposit_order", "balance_order"):
        refresh_status(team)


def managed(team: TournamentTeam) -> bool:
    return _alive(team.deposit_order) or _alive(team.balance_order)


def _doc(order) -> dict | None:
    if order is None:
        return None
    invoice = getattr(order, "invoice", None)
    return {
        "id": order.pk, "number": getattr(invoice, "number", None), "total": f"{order.total:.2f}", "paid": f"{payments.amount_paid(order):.2f}", "payment_status": order.payment_status,
        "cancelled": order.fulfillment_status == Order.FulfillmentStatus.CANCELLED, "url": f"/finance/charges/{order.pk}",
    }  # fmt: skip


def state(team: TournamentTeam) -> dict:
    t = team.tournament
    return {
        "billable": billable(team), "managed": managed(team), "deposit": _doc(team.deposit_order), "balance": _doc(team.balance_order),
        "can_deposit": billable(team) and not _alive(team.deposit_order) and bool(t.deposit_amount), "can_balance": billable(team) and not _alive(team.balance_order) and bool(balance_lines(team)),
    }  # fmt: skip


def invoice_due_balances(today: datetime.date | None = None) -> int:
    """The daily job (in one club's schema): guest teams of tournaments starting within ``BALANCE_LEAD_DAYS`` get their balance invoice."""
    today = today or datetime.date.today()
    n = 0
    for tournament in Tournament.objects.filter(status__in=[Tournament.Status.PUBLISHED, Tournament.Status.IN_PROGRESS], start_date__lte=today + datetime.timedelta(days=BALANCE_LEAD_DAYS), end_date__gte=today):
        for team in tournament.teams.filter(team__isnull=True, withdrawn_at__isnull=True).select_related("tournament", "deposit_order", "balance_order", "payer"):
            try:
                if ensure_balance_invoice(team) is not None:
                    n += 1
            except Exception:  # one team's problem (no contact email ...) must not stop the others
                log.exception("tournaments.balance_invoice_failed", extra={"team": str(team.pk)})
    return n
