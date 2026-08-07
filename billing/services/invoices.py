"""Invoice PDFs.

The PDF is rendered on demand from the Due's frozen snapshot (tier, amount, dates), so it
carries no state of its own beyond the number. Only the number is stored — an accountant
reconciles against it, so it is allocated once, never recomputed.
"""

from django.template.loader import render_to_string

from billing.models import Due, Invoice
from billing.services import BillingError


def issue_invoice(due: Due) -> Invoice:
    """One invoice per due, allocated once. Re-issuing returns the existing one rather than
    burning a number — a gap in an invoice series is a question you do not want to answer."""
    invoice, _created = Invoice.objects.get_or_create(due=due)

    return invoice


def render_pdf(html: str) -> bytes:
    """HTML to PDF.

    WeasyPrint is imported here, not at module scope: it binds to native pango/cairo
    libraries, and a machine without them must still be able to run the app, the tests and
    every other page — it should only fail when someone actually asks for a PDF, and say why.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise BillingError("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).") from error

    return HTML(string=html).write_pdf()


def invoice_pdf(invoice: Invoice, base_url: str | None = None) -> bytes:
    html = render_to_string("billing/invoice.html", {"invoice": invoice, "due": invoice.due, "club": invoice.due.club, "payments": invoice.due.payments.all()})

    return render_pdf(html)
