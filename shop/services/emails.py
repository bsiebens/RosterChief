"""Club-branded HTML emails about an order: "order registered" (``placed``) and "ready for pickup" (``ready``).

``shop.services.notifications.send`` calls ``send_order_emails`` next to the inbox/push notification (inside the
``shop.notify_order`` Celery task, so the PDF rendering and the SMTP work stay off the request). Rules:

* recipients are the ones the inbox notification goes to (the purchaser's own login, the guardians of a child), each in their own
  language; a purchaser without a login is reached on their member email address;
* the account's own preferences apply: category ``shop``, the master email switch and the email mode
  (``notifications.preferences.should_email``);
* one email per distinct address per order event;
* everything goes through ``rosterchief.mail.send_message`` so the platform's email pause holds them back;
* the ``ready`` email carries the invoice PDF (named after the invoice number); without WeasyPrint it goes without.
A mail problem is logged and never fails the notification."""

import logging
import re
from decimal import Decimal

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from rosterchief.mail import send_message

logger = logging.getLogger(__name__)

KINDS = ("placed", "ready", "charge", "charge_reminder")
#: The kinds that carry the invoice PDF and the payment instructions.
INVOICE_KINDS = ("ready", "charge", "charge_reminder")
CATEGORY = "shop"
DEFAULT_PRIMARY, DEFAULT_SECONDARY = "#0f172a", "#e4002b"

TEXTS = {
    "en": {
        "placed": {
            "subject": "{club}: order {number} registered",
            "heading": "Thanks for your order",
            "intro": "We registered your order {number}. Here is what you ordered.",
            "next_title": "What happens next",
            "next": "We prepare your order and let you know as soon as it is ready for pickup.",
        },
        "ready": {
            "subject": "{club}: order {number} is ready for pickup",
            "heading": "Your order is ready",
            "intro": "Your order {number} is ready for pickup.",
            "next_title": "Pickup",
            "next": "",
            "invoice": "The invoice is attached to this email.",
        },
        "charge": {
            "subject": "{club}: invoice {number}",
            "heading": "You have a new invoice",
            "intro": "{club} sent you invoice {number}. Please pay by {due_date}.",
            "next_title": "",
            "next": "",
            "invoice": "The invoice is attached to this email.",
            "on_behalf": "This message is for {child}'s invoice.",
        },
        "charge_reminder": {
            "subject": "{club}: reminder, invoice {number} is overdue",
            "heading": "Invoice overdue",
            "intro": "Invoice {number} was due on {due_date} and is still open. Please pay as soon as you can. If you have already paid, thank you, and please ignore this message.",
            "next_title": "",
            "next": "",
            "invoice": "The invoice is attached to this email.",
            "on_behalf": "This message is for {child}'s invoice.",
        },
        "hello": "Hi {name},",
        "on_behalf": "This message is for {child}'s order.",
        "order": "Order",
        "item": "Item",
        "qty": "Qty",
        "amount": "Amount",
        "for": "For",
        "discount": "Discount",
        "total": "Total",
        "paid": "Paid",
        "due": "Amount due",
        "paid_in_full": "Paid in full. Thank you.",
        "how_to_pay": "How to pay",
        "footer": "This is an automated message from {club}.",
    },
    "nl": {
        "placed": {
            "subject": "{club}: bestelling {number} geregistreerd",
            "heading": "Bedankt voor je bestelling",
            "intro": "We hebben je bestelling {number} geregistreerd. Dit heb je besteld.",
            "next_title": "Wat gebeurt er nu",
            "next": "We maken je bestelling klaar en laten je weten zodra ze kan worden opgehaald.",
        },
        "ready": {
            "subject": "{club}: bestelling {number} ligt klaar",
            "heading": "Je bestelling ligt klaar",
            "intro": "Je bestelling {number} kan worden opgehaald.",
            "next_title": "Ophalen",
            "next": "",
            "invoice": "De factuur zit als bijlage bij deze e-mail.",
        },
        "charge": {
            "subject": "{club}: factuur {number}",
            "heading": "Je hebt een nieuwe factuur",
            "intro": "{club} stuurde je factuur {number}. Betaal graag voor {due_date}.",
            "next_title": "",
            "next": "",
            "invoice": "De factuur zit als bijlage bij deze e-mail.",
            "on_behalf": "Dit bericht gaat over de factuur van {child}.",
        },
        "charge_reminder": {
            "subject": "{club}: herinnering, factuur {number} is vervallen",
            "heading": "Factuur vervallen",
            "intro": "Factuur {number} moest betaald zijn op {due_date} en staat nog open. Betaal graag zo snel mogelijk. Heb je al betaald, dan mag je dit bericht negeren.",
            "next_title": "",
            "next": "",
            "invoice": "De factuur zit als bijlage bij deze e-mail.",
            "on_behalf": "Dit bericht gaat over de factuur van {child}.",
        },
        "hello": "Hallo {name},",
        "on_behalf": "Dit bericht gaat over de bestelling van {child}.",
        "order": "Bestelling",
        "item": "Artikel",
        "qty": "Aantal",
        "amount": "Bedrag",
        "for": "Voor",
        "discount": "Korting",
        "total": "Totaal",
        "paid": "Betaald",
        "due": "Te betalen",
        "paid_in_full": "Volledig betaald. Bedankt.",
        "how_to_pay": "Betalen",
        "footer": "Dit is een automatisch bericht van {club}.",
    },
    "fr": {
        "placed": {
            "subject": "{club} : commande {number} enregistrée",
            "heading": "Merci pour votre commande",
            "intro": "Nous avons enregistré votre commande {number}. Voici ce que vous avez commandé.",
            "next_title": "La suite",
            "next": "Nous préparons votre commande et vous prévenons dès qu'elle peut être retirée.",
        },
        "ready": {
            "subject": "{club} : commande {number} prête",
            "heading": "Votre commande est prête",
            "intro": "Votre commande {number} peut être retirée.",
            "next_title": "Retrait",
            "next": "",
            "invoice": "La facture est jointe à cet e-mail.",
        },
        "charge": {
            "subject": "{club} : facture {number}",
            "heading": "Vous avez une nouvelle facture",
            "intro": "{club} vous a envoyé la facture {number}. Merci de payer avant le {due_date}.",
            "next_title": "",
            "next": "",
            "invoice": "La facture est jointe à cet e-mail.",
            "on_behalf": "Ce message concerne la facture de {child}.",
        },
        "charge_reminder": {
            "subject": "{club} : rappel, la facture {number} est échue",
            "heading": "Facture échue",
            "intro": "La facture {number} était à payer le {due_date} et reste ouverte. Merci de payer dès que possible. Si vous avez déjà payé, veuillez ignorer ce message.",
            "next_title": "",
            "next": "",
            "invoice": "La facture est jointe à cet e-mail.",
            "on_behalf": "Ce message concerne la facture de {child}.",
        },
        "hello": "Bonjour {name},",
        "on_behalf": "Ce message concerne la commande de {child}.",
        "order": "Commande",
        "item": "Article",
        "qty": "Qté",
        "amount": "Montant",
        "for": "Pour",
        "discount": "Remise",
        "total": "Total",
        "paid": "Payé",
        "due": "Reste à payer",
        "paid_in_full": "Payée en totalité. Merci.",
        "how_to_pay": "Comment payer",
        "footer": "Ceci est un message automatique de {club}.",
    },
}
LANGUAGES = tuple(TEXTS)


def money(value) -> str:
    return f"EUR {Decimal(value):.2f}"


def branding(club, logo_url: str = "") -> dict:
    """The layout's header values for ``club``."""
    return {
        "club_name": club.name,
        "club_initials": "".join(w[0] for w in club.name.split()[:2]).upper(),
        "logo_url": logo_url,
        "primary": club.primary_color or DEFAULT_PRIMARY,
        "primary_content": (club.primary_content_color or "#ffffff") if club.primary_color else "#ffffff",
        "secondary": club.secondary_color or DEFAULT_SECONDARY,
        "secondary_content": (club.secondary_content_color or "#ffffff") if club.secondary_color else "#ffffff",
    }


def _logo_url(club) -> str:
    """Absolute URL of the logo (mail clients cannot resolve a relative one); empty when there is none or no domain is known."""
    if not club.logo:
        return ""
    url = club.logo.url
    if url.startswith(("http://", "https://")):
        return url
    from club.models import Domain

    domain = Domain.objects.filter(tenant=club, is_primary=True).values_list("domain", flat=True).first()
    return f"https://{domain}{url}" if domain else ""


def build_context(
    club,
    kind: str,
    lang: str,
    *,
    number: str,
    lines: list[dict],
    discounts: list[dict],
    total,
    paid,
    due,
    is_paid: bool,
    how_to_pay: str = "",
    pickup_instructions: str = "",
    name: str = "",
    on_behalf_of: str = "",
    has_invoice: bool = False,
    due_date: str = "",
    message: str = "",
    logo_url: str | None = None,
) -> dict:
    lang = lang if lang in TEXTS else "en"
    texts = TEXTS[lang]
    values = {"club": club.name, "number": number, "name": name, "child": on_behalf_of, "due_date": due_date}
    t = {k: v.format(**values) for k, v in texts.items() if isinstance(v, str)}
    t.update({k: v.format(**values) for k, v in texts[kind].items()})
    return {
        **branding(club, _logo_url(club) if logo_url is None else logo_url),
        "lang": lang,
        "kind": kind,
        "t": t,
        "name": name,
        "on_behalf_of": on_behalf_of,
        "number": number,
        "lines": [{**ln, "line_total": money(ln["line_total"])} for ln in lines],
        "discounts": [{**d, "amount": money(d["amount"])} for d in discounts],
        "total": money(total),
        "paid": money(paid),
        "due": money(due),
        "show_paid": Decimal(paid) > 0,
        "is_paid": is_paid,
        "how_to_pay": how_to_pay if not is_paid else "",
        "pickup_instructions": pickup_instructions if kind == "ready" else "",
        "has_invoice": has_invoice,
        "message": message,
    }


def render(ctx: dict) -> tuple[str, str, str]:
    """(subject, plain text, html)."""
    subject = re.sub(r"\s+", " ", ctx["t"]["subject"]).strip()
    return subject, render_to_string("shop/email/order.txt", ctx), render_to_string("shop/email/order.html", ctx)


def _order_data(order) -> dict:
    from shop.services import payments

    lines = []
    for ln in order.lines.select_related("product", "variant", "beneficiary"):
        personal = " ".join(x for x in (ln.personalization_number, ln.personalization_name) if x)
        lines.append(
            {
                "description": ln.description or ln.product.name + (f" - {ln.variant.name}" if ln.variant else ""),
                "quantity": ln.quantity,
                "line_total": ln.line_total,
                "personalization": personal,
                "beneficiary": str(ln.beneficiary) if ln.beneficiary else "",
            }
        )
    discounts = [{"description": d.description or str(d.discount), "amount": d.amount} for d in order.applied_discounts.select_related("discount")]
    paid = payments.amount_paid(order)
    return {
        "number": _display_number(order),
        "lines": lines,
        "discounts": discounts,
        "total": order.total,
        "paid": paid,
        "due": payments.amount_due(order),
        "is_paid": order.payment_status == "paid",
        "message": (order.invoice_text or "").strip(),
    }


def _display_number(order) -> str:
    """A charge is known by its invoice number; the internal order number is for shop orders."""
    from shop.models import Invoice

    if order.kind == "charge":
        return Invoice.objects.filter(order=order).values_list("number", flat=True).first() or order.number
    return order.number


def _due_date(order) -> str:
    """The invoice's due date as dd/mm/yyyy, or empty when it has none."""
    from shop.models import Invoice

    due = Invoice.objects.filter(order=order).values_list("due_date", flat=True).first()
    return due.strftime("%d/%m/%Y") if due else ""


def _invoice_attachment(order):
    """(filename, pdf bytes) of the order's invoice, or None when it cannot be made (never raises)."""
    from shop.services import invoices

    try:
        invoice = invoices.ensure_invoice(order)
    except Exception:
        logger.exception("shop.email_invoice_missing order=%s", order.pk)
        return None
    try:
        return f"{invoice.number}.pdf", invoices.render_pdf(invoice)
    except invoices.PDFUnavailable:
        logger.warning("shop.email_invoice_pdf_unavailable order=%s: sending the email without the attachment", order.pk)
    except Exception:
        logger.exception("shop.email_invoice_pdf_failed order=%s", order.pk)
    return None


def send_order_emails(kind: str, order, extra: dict | None, entries: list[tuple[object, object, str]]) -> int:
    """Email ``entries`` = ``[(user or None, on_behalf_of member or None, language)]``; returns the emails sent."""
    from events.services.attendance import current_club
    from notifications import preferences
    from shop.services import notifications

    club = current_club()
    if club is None or kind not in KINDS:
        return 0
    member = order.purchaser
    data = _order_data(order)
    how_to_pay = ((order.payment_instructions or "").strip() or notifications._how_to_pay(extra)) if not data["is_paid"] else ""
    attachment = _invoice_attachment(order) if kind in INVOICE_KINDS else None
    due_date = _due_date(order)
    seen, sent = set(), 0
    for user, child, lang in entries:
        address = (user.email if user is not None else "") or (member.contact_email if child is None else "")
        key = address.strip().lower()
        if not key or key in seen:
            continue
        if user is not None and not preferences.should_email(user, CATEGORY, club):
            continue
        seen.add(key)
        recipient = child if child is not None else member
        if child is not None:  # a guardian is greeted by their own name
            from members.models import Member

            guardian = Member.objects.filter(user=user).first()
            name = guardian.first_name if guardian else ""
        else:
            name = member.first_name
        try:
            ctx = build_context(
                club, kind, lang, **data, how_to_pay=how_to_pay, pickup_instructions=(order.pickup_instructions or "").strip(), name=name,
                on_behalf_of=recipient.first_name if child is not None else "", has_invoice=attachment is not None, due_date=due_date,
            )  # fmt: skip
            subject, text, html = render(ctx)
            message = EmailMultiAlternatives(subject, text, to=[address])
            message.attach_alternative(html, "text/html")
            if attachment is not None:
                message.attach(attachment[0], attachment[1], "application/pdf")
            sent += bool(send_message(message))
        except Exception:
            logger.exception("shop.email_failed kind=%s order=%s", kind, order.pk)
    return sent
