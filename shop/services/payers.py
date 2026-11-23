"""Billing someone who is not a member: finance invoices need a member as the purchaser, so an outside party (a guest team's contact, a sponsor,
anyone invoiced once) gets a login-less ``Member`` record made from a name and an email address."""

from members.models import Member


def external_payer(name: str, email: str = "", *, fallback_name: str = "") -> Member:
    """The member record for an outside payer: found by email (any case) when that address is known, else made (without membership or login)."""
    email = (email or "").strip()
    member = Member.objects.filter(email__iexact=email).first() if email else None
    if member is not None:
        return member
    name = (name or fallback_name or "").strip()
    first, _sep, last = name.partition(" ")
    return Member.objects.create(first_name=first[:75] or fallback_name[:75] or "?", last_name=last[:75], email=email)
