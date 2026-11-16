"""The stock count sheet as a PDF: the stock as it is now, ready to be filled in by hand and entered in the system afterwards.

A4 table (product / variant, on hand at printing time, a blank box for the counted number, a blank notes column) grouped by category,
with lines for who counted, the date and a signature. Narrowed like the count screen: ``q`` (product / variant text) and ``category``
(an id, or ``none`` for products without one). Rendered with WeasyPrint like the invoices (``PDFUnavailable`` when it isn't installed)."""

import datetime

from django.template.loader import render_to_string
from django.utils import timezone

from shop.services import inventory, invoices


def sheet_rows(q: str = "", category: str = "") -> list:
    """The stock lines (inventory-managed products and variants) that match the filters, in overview order."""
    rows = inventory.inventory_overview()
    q = (q or "").strip().lower()
    if q:
        rows = [r for r in rows if q in r.product.name.lower() or (r.variant is not None and q in r.variant.name.lower())]
    if category:
        rows = [r for r in rows if (r.product.category_id is None if category == "none" else str(r.product.category_id) == category)]
    return rows


def groups(rows) -> list[dict]:
    """``[{"name": category name or None, "rows": [...]}]``: categories by name, products without one last."""
    by_category: dict = {}
    for r in rows:
        by_category.setdefault(r.product.category.name if r.product.category else None, []).append(r)
    ordered = sorted((name for name in by_category if name is not None), key=str.lower)
    if None in by_category:
        ordered.append(None)
    return [{"name": name, "rows": by_category[name]} for name in ordered]


def render_pdf(q: str = "", category: str = "", *, printed_at: datetime.datetime | None = None) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise invoices.PDFUnavailable("PDF rendering is not available on this server.") from error
    rows = sheet_rows(q, category)
    club = invoices._club()
    html = render_to_string(
        "shop/stock_count_sheet.html",
        {"club": club, "groups": groups(rows), "line_count": len(rows), "printed_at": timezone.localtime(printed_at or timezone.now()), "logo_data_uri": invoices._logo_uri(club)},
    )
    return HTML(string=html).write_pdf()
