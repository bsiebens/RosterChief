"""Previews of the club-branded documents the platform sends on behalf of a club.

What exists today: the "login granted" email and the allauth account emails (all share ``club/email/layout.html``), and the
referee / official payment forms (PDF, ``events.services.payment_forms``; only for clubs with the ``referee_officials`` feature).
Invoices and credit notes are issued by RosterChief to the club (platform branding), not by the club.
``kind="pdf"`` entries are HTML (``render_email`` returns it) that ``render_pdf`` turns into the file with WeasyPrint."""

import base64
import mimetypes
import re
from dataclasses import dataclass
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.utils import translation

from club.emails import AUTH_KINDS, auth_email_context, build_context, render_login_granted

LANGUAGES = ("en", "nl", "fr")
SAMPLE_EMAIL = "alex.sample@example.com"
SAMPLE_URL = "https://example.com/sample-link"


@dataclass(frozen=True)
class Preview:
    key: str
    kind: str  # "email" | "pdf"
    title: str
    description: str


_AUTH = {
    "password_reset_key": ("Password reset", "Sent when someone asks to reset their password."),
    "email_confirmation": ("Email verification", "Asks a user to confirm their email address."),
    "email_confirmation_signup": ("Email verification (sign-up)", "Email verification sent right after an account is created."),
    "password_changed": ("Password changed", "Security notice after a password change."),
    "password_set": ("Password set", "Security notice after a password was set."),
    "password_reset": ("Password was reset", "Security notice after a password reset."),
    "email_changed": ("Email address changed", "Security notice after the account email changed."),
    "email_deleted": ("Email address removed", "Security notice after an email address was removed."),
    "email_confirm": ("Email address confirmed", "Confirmation that an email address was verified."),
    "account_already_exists": ("Account already exists", "Sent when someone signs up with an address that already has an account."),
    "unknown_account": ("Unknown account", "Sent when someone tries to access an address without an account."),
    "login_code": ("Sign-in code", "One-time code to finish signing in."),
    "password_reset_code": ("Password reset code", "One-time code to reset a password."),
}

PREVIEWS = [Preview("login_granted", "email", "Login granted", "Sent to a member when the club gives them a login.")]
PREVIEWS += [Preview(key, "email", title, desc) for key, (title, desc) in _AUTH.items()]
PDF_PREVIEWS = [
    Preview("referee_payment_form", "pdf", "Referee payment form", "The sheet printed for a game's referees to sign for their fee and kilometres."),
    Preview("official_payment_form", "pdf", "Official payment form", "The same sheet for a game's match officials."),
]
PREVIEWS += PDF_PREVIEWS
# Only for clubs with the ``shop`` feature (shop/services/emails.py and shop/templates/shop/order_invoice_pdf.html)
SHOP_PREVIEWS = [
    Preview("shop_order_registered", "email", "Shop: order registered", "Sent to the purchaser (and a child's guardians) when a shop order is placed: lines, discounts, total, what is due and how to pay."),
    Preview("shop_order_ready", "email", "Shop: order ready for pickup", "Sent when a shop order is ready: pickup instructions and, for an unpaid order, how to pay. The order's invoice PDF is attached."),
    Preview("shop_invoice", "pdf", "Shop: order invoice", "The invoice PDF of a shop order (also attached to the 'ready for pickup' email), shown with sample data."),
]
PREVIEWS += SHOP_PREVIEWS
SHOP_KEYS = {p.key for p in SHOP_PREVIEWS}
BY_KEY = {p.key: p for p in PREVIEWS}
PAYMENT_FORM_KINDS = {"referee_payment_form": "referee", "official_payment_form": "official"}


def _logo_data_uri(club) -> str:
    """The logo inline (browsers cannot follow cid: images, and the media URL may not be reachable from the preview)."""
    if not club.logo:
        return ""
    try:
        with club.logo.open("rb") as f:
            raw = f.read()
    except Exception:
        return ""
    mime = mimetypes.guess_type(club.logo.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _sample_extras(key: str) -> dict:
    extras = {"email": SAMPLE_EMAIL, "password_reset_url": SAMPLE_URL, "activate_url": SAMPLE_URL, "signup_url": SAMPLE_URL}
    if key in ("login_code", "password_reset_code"):
        extras["code"] = "482 910"
    if key == "email_changed":
        extras.update(from_email="alex.old@example.com", to_email=SAMPLE_EMAIL)
    if key == "email_deleted":
        extras["deleted_email"] = "alex.old@example.com"
    if key in ("password_changed", "password_set", "password_reset", "email_changed", "email_deleted", "email_confirm"):
        from django.utils import timezone

        extras.update(ip="203.0.113.7", user_agent="Mozilla/5.0 (Macintosh) Safari/605.1", timestamp=timezone.now())
    return extras


def render_email(request, key: str, lang: str = "en") -> tuple[str, str]:
    """(subject, full html) of the email ``key`` with the current saved branding of ``request.tenant`` and sample data."""
    club = request.tenant
    logo = _logo_data_uri(club)
    if key in PAYMENT_FORM_KINDS:
        from events.services import payment_forms

        return BY_KEY[key].title, payment_forms.render_html(payment_forms.sample_context(club, PAYMENT_FORM_KINDS[key], lang))
    if key in SHOP_KEYS:
        return _render_shop(club, key, lang, logo)
    with translation.override(lang):
        if key == "login_granted":
            member = SimpleNamespace(first_name="Alex", language=lang)
            user = SimpleNamespace(email=SAMPLE_EMAIL)
            ctx = build_context(request, club, member, user, user_created=True, lang=lang, set_password_url=SAMPLE_URL, login_url=request.build_absolute_uri("/login"))
            if logo or not club.logo:
                ctx["logo_url"] = logo
            subject, _text, html = render_login_granted(ctx)
            return subject, html
        prefix = f"account/email/{key}"
        assert prefix in AUTH_KINDS
        ctx = {**_sample_extras(key), **auth_email_context(request, prefix)}
        if logo or not club.logo:
            ctx["logo_url"] = logo
        subject = re.sub(r"\s+", " ", render_to_string(f"{prefix}_subject.txt", ctx)).strip()
        return subject, render_to_string(f"{prefix}_message.html", ctx)


def _render_shop(club, key: str, lang: str, logo: str) -> tuple[str, str]:
    from decimal import Decimal

    from shop.services import emails

    if key == "shop_invoice":
        return BY_KEY[key].title, render_to_string("shop/order_invoice_pdf.html", _sample_invoice_context(club, logo))
    kind = "placed" if key == "shop_order_registered" else "ready"
    ctx = emails.build_context(
        club, kind, lang,
        number="2026-0042",
        lines=[
            {"description": "Club jersey - M", "quantity": 2, "line_total": Decimal("80.00"), "personalization": "7 DOE", "beneficiary": "Sam Sample"},
            {"description": "Training cap", "quantity": 1, "line_total": Decimal("15.00"), "personalization": "", "beneficiary": ""},
        ],
        discounts=[{"description": "Member discount", "amount": Decimal("9.50")}],
        total=Decimal("85.50"), paid=Decimal("20.00"), due=Decimal("65.50"), is_paid=False,
        how_to_pay=(club.payment_instructions or "").strip() or "IBAN BE00 0000 0000 0000, reference 2026-0042",
        pickup_instructions="Tuesday 18:00-19:30 at the club desk." if kind == "ready" else "",
        name="Alex", has_invoice=kind == "ready", logo_url=logo if (logo or not club.logo) else None,
    )  # fmt: skip
    subject, _text, html = emails.render(ctx)
    return subject, html


def _sample_invoice_context(club, logo: str) -> dict:
    """Made-up order, invoice and payments for ``shop/order_invoice_pdf.html`` (no database rows), with the club's real branding."""
    import datetime
    from decimal import Decimal

    from django.utils import timezone

    now = timezone.now()
    jersey = SimpleNamespace(
        product=SimpleNamespace(name="Club jersey"), variant=SimpleNamespace(name="M"), beneficiary="Sam Sample",
        quantity=2, unit_price=Decimal("40.00"), line_total=Decimal("80.00"), personalization_number="7", personalization_name="DOE",
    )  # fmt: skip
    cap = SimpleNamespace(
        product=SimpleNamespace(name="Training cap"), variant=None, beneficiary=None,
        quantity=1, unit_price=Decimal("15.00"), line_total=Decimal("15.00"), personalization_number="", personalization_name="",
    )  # fmt: skip
    order = SimpleNamespace(number="2026-0042", total=Decimal("85.50"), payment_status="partially_paid", purchaser="Alex Sample")
    invoice = SimpleNamespace(number="INV-2026-0017", issued_at=now, due_date=(now + datetime.timedelta(days=30)).date())
    snapshot = {
        "club": {"name": club.legal_name or club.name, "address": club.legal_address or "Sample Street 1", "zip_code": club.legal_zip_code or "1000", "city": club.legal_city or "Brussels", "contact_email": club.contact_email, "vat_id": club.vat_id,
                 "payment_instructions": club.payment_instructions},
        "purchaser": {"name": "Alex Sample", "email": SAMPLE_EMAIL},
    }  # fmt: skip
    payments = [
        {"label": "Voucher V-0003", "reference": "", "date": now - datetime.timedelta(days=2), "amount": Decimal("10.00")},
        {"label": "Cash", "reference": "", "date": now - datetime.timedelta(days=1), "amount": Decimal("10.00")},
    ]
    return {
        "club": club,
        "invoice": invoice,
        "order": order,
        "lines": [jersey, cap],
        "discounts": [SimpleNamespace(description="Member discount", discount_type="fixed_amount", discount_amount=Decimal("9.50"), amount=Decimal("9.50"))],
        "payments": payments,
        "amount_paid": Decimal("20.00"),
        "amount_due": Decimal("65.50"),
        "snapshot": snapshot,
        "logo_data_uri": logo or None,
        "qr_data_uri": None,
    }


def render_pdf(html: str) -> bytes:
    """Raises ``RuntimeError`` when WeasyPrint (or its system libraries) is unavailable."""
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:
        raise RuntimeError("PDF rendering is not available on this server.") from e
    return HTML(string=html).write_pdf()
