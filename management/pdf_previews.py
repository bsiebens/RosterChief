"""Sample renders of every PDF this app can generate -- the PDF tab on the
Club identity page (management/templates/management/club_settings.html).

Both PDFs (management.pdf.event_referee_form_pdf, and
club.services.invoicing.invoice_pdf) are themselves just WeasyPrint run over
an HTML template -- see that HTML directly here rather than running
WeasyPrint for a preview: it's the same document, without needing the native
pango/cairo libraries installed just to look at it, and it's exactly what
management.views.EmailPreviewRenderView's sibling PDFPreviewRenderView shows
in an iframe. Same "hand-built sample context, no real DB rows" reasoning as
email_previews.py.
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .pdf import referee_form_colors


@dataclass(frozen=True)
class PDFPreview:
    key: str
    label: str
    description: str
    template: str
    build_context: Callable[..., dict]


def _dues_invoice_pdf_context(club, request):
    invoice = SimpleNamespace(
        number="DUE-2026-00042",
        amount=Decimal("45.00"),
        due_date=timezone.now().date() + datetime.timedelta(days=14),
        sent_at=timezone.now(),
        is_paid=False,
        sent_to_email="jamie.doe@example.com",
        sent_to_guardian=False,
    )
    membership = SimpleNamespace(season="2026-2027")
    return {"club": club, "invoice": invoice, "membership": membership, "member": "Jamie Doe"}


def _referee_form_pdf_context(club, request):
    referees = [
        SimpleNamespace(display_name="Jamie Doe", fee=Decimal("35.00"), km=Decimal("12.0"), km_rate=Decimal("0.35"), total_payable=Decimal("39.20")),
        SimpleNamespace(display_name="Alex Referee", fee=Decimal("35.00"), km=None, km_rate=None, total_payable=Decimal("35.00")),
    ]
    event = SimpleNamespace(
        start=timezone.now() + datetime.timedelta(days=7),
        teams=SimpleNamespace(all=lambda: [SimpleNamespace(short_name="U16")]),
        opponent="Leuven",
        external_game_id="",
    )
    home_location = SimpleNamespace(address="Sportlaan 1", zip_code="1000", city="Brussels")
    grand_total = sum((referee.total_payable for referee in referees), Decimal("0"))
    return {"club": club, "event": event, "referees": referees, "home_location": home_location, "grand_total": grand_total} | referee_form_colors(club)


PDF_PREVIEWS = [
    PDFPreview(
        key="dues_invoice",
        label=_("Membership invoice"),
        description=_("The PDF attached to the membership invoice email, also downloadable from a member's invoice page."),
        template="club/dues_invoice_pdf.html",
        build_context=_dues_invoice_pdf_context,
    ),
    PDFPreview(
        key="referee_form",
        label=_("Referee payment form"),
        description=_("Downloadable from a game's page, for the club-arranged referee(s) to sign."),
        template="management/event_referee_form_pdf.html",
        build_context=_referee_form_pdf_context,
    ),
]

PDF_PREVIEWS_BY_KEY = {preview.key: preview for preview in PDF_PREVIEWS}


def render_pdf_preview(preview: PDFPreview, *, club, request) -> str:
    return render_to_string(preview.template, preview.build_context(club, request))
