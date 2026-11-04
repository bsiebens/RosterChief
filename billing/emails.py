"""Branded emails for platform billing (RosterChief invoicing a club). Same layout as the account emails
(``club/email/layout.html``) but always with the *platform's* branding: RosterChief name, default colours, no club logo.

Texts live in ``TEXTS`` (English; billing documents are English today) so translations can be added per language."""

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from club.emails import attach_platform_logo, branding_context
from rosterchief.mail import send_message

TEXTS = {
    "en": {
        "footer": "This is an automated message from {platform}.",
        "invoice_subject": "Invoice {number} from {platform}",
        "invoice_heading": "Invoice {number}",
        "invoice_intro": "Hello {club}, your invoice for {period} is attached to this email as a PDF.",
        "notice_subject": "New invoice {number} for {club}",
        "notice_heading": "New invoice issued",
        "notice_intro": "Invoice {number} was issued for {club}.",
        "reminder_heading": "{title}",
    },
}


def platform_branding_context() -> dict:
    """Layout context for platform emails: no request, so ``branding_context`` yields the RosterChief brand."""
    ctx = branding_context(None)
    ctx["lang"] = "en"
    return ctx


def texts(**params) -> dict:
    """The English texts with ``{placeholders}`` filled (``platform`` is always RosterChief)."""
    params.setdefault("platform", "RosterChief")

    class Lenient(dict):  # texts for other emails keep their {placeholders} untouched
        def __missing__(self, key):
            return "{" + key + "}"

    return {k: v.format_map(Lenient(params)) for k, v in TEXTS["en"].items()}


def send_branded(*, subject: str, text: str, template: str, ctx: dict, to: list[str], attachments=(), fail_silently: bool = False) -> bool:
    """Render ``template`` (HTML) with the platform branding and send it multipart through ``send_message`` (so the
    platform's email pause applies). Returns False when nothing was sent."""
    ctx = {**platform_branding_context(), **ctx}
    message = EmailMultiAlternatives(subject, text, to=to)
    message.attach_alternative(render_to_string(template, ctx), "text/html")
    for name, content, mimetype in attachments:
        message.attach(name, content, mimetype)
    attach_platform_logo(message)
    return send_message(message, fail_silently=fail_silently)
