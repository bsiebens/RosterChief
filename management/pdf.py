"""HTML-to-PDF rendering for exports (currently just the memberships list).

Same lazy-import shape as billing.services.invoices.render_pdf -- WeasyPrint binds
to native pango/cairo libraries, and a machine without them must still be able to
run the app, the tests and every other page, so the import happens here, not at
module scope, and only fails when someone actually asks for a PDF. Kept separate
from billing's version rather than shared: the two exports have nothing else in
common, and sharing would make one app depend on the other's error type for no
real benefit.
"""

from django.template.loader import render_to_string


class PDFExportError(Exception):
    """Raised when WeasyPrint's native libraries aren't available."""


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise PDFExportError("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).") from error

    return HTML(string=html).write_pdf()


def membership_list_pdf(context: dict) -> bytes:
    html = render_to_string("management/membership_list_pdf.html", context)
    return render_pdf(html)


def event_referee_form_pdf(context: dict) -> bytes:
    html = render_to_string("management/event_referee_form_pdf.html", context)
    return render_pdf(html)
