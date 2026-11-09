"""The two emails of a registration: the confirmation (status link, no money) and the invoice (with the PDF). Club-branded, in the registrant's
language, sent through ``rosterchief.mail.send_message`` so the platform's email pause holds them back. A mail problem is logged and never
fails the registration. No account is created and no notification preferences apply: the contact has no login."""

import logging
import re

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from registration.models import RegistrationBatch
from registration.services import config, invoicing
from rosterchief.mail import send_message
from shop.services.emails import _logo_url, branding, money
from shop.services.invoices import PDFUnavailable

log = logging.getLogger(__name__)

TEXTS = {
    "en": {
        "hello": "Hi {name},",
        "footer": "This is an automated message from {club}.",
        "confirmation": {
            "subject": "{club}: we received your registration",
            "heading": "Thanks for registering",
            "intro": "We received your registration for {season}. We will check it and come back to you with the invoice.",
            "people": "Registered",
            "button": "Follow your registration",
            "next": "On the status page you can see what is still needed and upload documents. Keep this email: the link is personal.",
        },
        "invoice": {
            "subject": "{club}: invoice {number}",
            "heading": "Your invoice",
            "intro": "Your registration for {season} is confirmed. Invoice {number} is attached to this email.",
            "item": "Registration",
            "discount": "Discount",
            "credit": "Credit",
            "credit_unused": "Credit that could not be used",
            "total": "Total",
            "due": "Please pay before {due}.",
            "early": "Pay on or before {deadline} and {person} pays {fee} instead.",
            "how_to_pay": "How to pay",
            "button": "Follow your registration",
        },
    },
    "nl": {
        "hello": "Hallo {name},",
        "footer": "Dit is een automatisch bericht van {club}.",
        "confirmation": {
            "subject": "{club}: we ontvingen je inschrijving",
            "heading": "Bedankt voor je inschrijving",
            "intro": "We ontvingen je inschrijving voor {season}. We bekijken ze en bezorgen je daarna de factuur.",
            "people": "Ingeschreven",
            "button": "Volg je inschrijving",
            "next": "Op de statuspagina zie je wat nog nodig is en kan je documenten uploaden. Bewaar deze e-mail: de link is persoonlijk.",
        },
        "invoice": {
            "subject": "{club}: factuur {number}",
            "heading": "Je factuur",
            "intro": "Je inschrijving voor {season} is bevestigd. Factuur {number} zit als bijlage bij deze e-mail.",
            "item": "Inschrijving",
            "discount": "Korting",
            "credit": "Tegoed",
            "credit_unused": "Tegoed dat niet gebruikt kon worden",
            "total": "Totaal",
            "due": "Gelieve te betalen voor {due}.",
            "early": "Betaal op of voor {deadline} en {person} betaalt {fee} in plaats daarvan.",
            "how_to_pay": "Hoe betalen",
            "button": "Volg je inschrijving",
        },
    },
    "fr": {
        "hello": "Bonjour {name},",
        "footer": "Ceci est un message automatique de {club}.",
        "confirmation": {
            "subject": "{club} : nous avons reçu votre inscription",
            "heading": "Merci de votre inscription",
            "intro": "Nous avons reçu votre inscription pour {season}. Nous la vérifions et revenons vers vous avec la facture.",
            "people": "Inscrits",
            "button": "Suivre votre inscription",
            "next": "Sur la page de suivi, vous voyez ce qu'il manque encore et pouvez téléverser des documents. Gardez cet e-mail : le lien est personnel.",
        },
        "invoice": {
            "subject": "{club} : facture {number}",
            "heading": "Votre facture",
            "intro": "Votre inscription pour {season} est confirmée. La facture {number} est jointe à cet e-mail.",
            "item": "Inscription",
            "discount": "Remise",
            "credit": "Crédit",
            "credit_unused": "Crédit non utilisé",
            "total": "Total",
            "due": "Merci de payer avant le {due}.",
            "early": "Payez au plus tard le {deadline} et {person} paie {fee} au lieu de cela.",
            "how_to_pay": "Comment payer",
            "button": "Suivre votre inscription",
        },
    },
}


def _club():
    from events.services.attendance import current_club

    return current_club()


def base_url(club) -> str:
    """``https://<primary domain>`` of the club (plain http for ``localhost`` development hosts)."""
    from club.models import Domain

    domain = Domain.objects.filter(tenant=club, is_primary=True).values_list("domain", flat=True).first()
    if not domain:
        return ""
    return f"{'http' if domain.split(':')[0].endswith('localhost') else 'https'}://{domain}"


def status_url(club, token: str) -> str:
    return f"{base_url(club)}/join/status/{token}"


def _lang(batch: RegistrationBatch) -> str:
    lang = batch.language or (batch.contact.language if batch.contact else "")
    return lang if lang in TEXTS else "en"


class _Keep(dict):
    """``format_map`` source that leaves unknown ``{placeholders}`` in place (the early-bird sentence is filled in per person later)."""

    def __missing__(self, key):
        return "{" + key + "}"


def _t(batch, kind: str, club, **values) -> dict:
    texts = TEXTS[_lang(batch)]
    base = {"club": club.name, "season": batch.season.name, "name": batch.contact.first_name if batch.contact else "", "number": batch.invoice_number or "", **values}
    t = {k: v.format_map(_Keep(base)) for k, v in texts.items() if isinstance(v, str)}
    t.update({k: v.format_map(_Keep(base)) for k, v in texts[kind].items()})
    return t


def _send(subject: str, text: str, html: str, to: str, attachment=None) -> bool:
    message = EmailMultiAlternatives(re.sub(r"\s+", " ", subject).strip(), text, to=[to])
    message.attach_alternative(html, "text/html")
    if attachment is not None:
        message.attach(*attachment)
    return send_message(message)


def send_confirmation(batch_id) -> bool:
    try:
        batch = RegistrationBatch.objects.select_related("contact", "season").get(pk=batch_id)
        club = _club()
        if club is None or batch.contact is None or not batch.contact.email:
            return False
        people = [f"{d.member} ({d.description})" if d.description else str(d.member) for d in batch.details.select_related("member")]
        ctx = {**branding(club, _logo_url(club)), "lang": _lang(batch), "t": _t(batch, "confirmation", club), "people": people, "status_url": status_url(club, batch.status_token)}
        sent = _send(ctx["t"]["subject"], render_to_string("registration/email/confirmation.txt", ctx), render_to_string("registration/email/confirmation.html", ctx), batch.contact.email)
        if sent:
            batch.confirmation_sent_at = timezone.now()
            batch.save(update_fields=["confirmation_sent_at", "modified"])
        return sent
    except Exception:
        log.exception("Could not send the registration confirmation for %s", batch_id)
        return False


def invoice_context(batch: RegistrationBatch, club) -> dict:
    details = [d for d in batch.details.select_related("member") if not d.excluded]
    lines = [{"name": str(d.member), "description": d.description, "amount": f"-{money(-d.price)}" if d.is_credit else money(d.price), "credit": d.is_credit} for d in details]
    credit_total, credit_applied = invoicing.credits(batch)
    unused_credit = credit_total - credit_applied
    early = [
        {"name": str(d.member), "deadline": d.membership.early_payment_deadline, "fee": money(max(d.membership.fee_amount - d.membership.early_payment_discount, 0))}
        for d in batch.details.select_related("member", "membership")
        if d.membership.early_payment_deadline and d.membership.early_payment_discount
    ]
    return {
        "club": club, "batch": batch, "lines": lines, "unused_credit": money(unused_credit) if unused_credit > 0 else "",
        "discount": money(batch.manual_discount_amount) if batch.manual_discount_amount else "", "discount_note": batch.manual_discount_note,
        "total": money(invoicing.total(batch)), "early": early, "payment_instructions": config.get_settings().payment_instructions,
    }  # fmt: skip


def render_invoice_pdf(batch: RegistrationBatch) -> bytes:
    from shop.services import invoices

    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise PDFUnavailable("PDF rendering is not available on this server.") from error
    club = _club()
    ctx = {**invoice_context(batch, club), "logo_data_uri": invoices._logo_uri(club), "issued": batch.confirmed_at, "contact": batch.contact}
    return HTML(string=render_to_string("registration/invoice_pdf.html", ctx)).write_pdf()


def send_invoice(batch_id) -> bool:
    try:
        batch = RegistrationBatch.objects.select_related("contact", "season").get(pk=batch_id)
        club = _club()
        if club is None or batch.contact is None or not batch.contact.email or not batch.invoice_number:
            return False
        base = invoice_context(batch, club)
        t = _t(batch, "invoice", club, due=batch.invoice_due_date.strftime("%d/%m/%Y") if batch.invoice_due_date else "")
        t["early_lines"] = [t["early"].format(person=e["name"], deadline=e["deadline"].strftime("%d/%m/%Y"), fee=e["fee"]) for e in base["early"]]
        ctx = {
            **branding(club, _logo_url(club)),
            "lang": _lang(batch),
            "t": t,
            "lines": base["lines"],
            "unused_credit": base["unused_credit"],
            "discount": base["discount"],
            "discount_note": base["discount_note"],
            "total": base["total"],
            "due": bool(batch.invoice_due_date),
            "how_to_pay": base["payment_instructions"],
            "status_url": status_url(club, batch.status_token),
        }
        try:
            attachment = (f"{batch.invoice_number}.pdf", render_invoice_pdf(batch), "application/pdf")
        except PDFUnavailable:
            attachment = None
        sent = _send(t["subject"], render_to_string("registration/email/invoice.txt", ctx), render_to_string("registration/email/invoice.html", ctx), batch.contact.email, attachment)
        if sent:
            batch.invoice_sent_at = timezone.now()
            batch.save(update_fields=["invoice_sent_at", "modified"])
        return sent
    except Exception:
        log.exception("Could not send the registration invoice for %s", batch_id)
        return False
