"""Building the downloadable "who ordered what" spreadsheet for one Product
-- what a club actually hands to a manufacturer/printer to place a bulk
merchandise order. Kept separate from bulk_import.py: that module is
member-import-specific by name and docstring, this is shop-specific and has
nothing to do with importing anything.
"""

import openpyxl
from django.utils.translation import gettext_lazy as _

from shop.models import Order, OrderLine

TEMPLATE_COLUMNS = [_("Order"), _("Last name"), _("First name"), _("Option"), _("Number"), _("Name"), _("Quantity")]


def build_product_order_export(product):
    """One row per OrderLine for this product, across every order in its
    club -- excluding cancelled orders (nothing to manufacture there), but
    not filtered by payment status: a club may need to place the bulk order
    with its supplier before every payment has actually been collected."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Order list"

    sheet.append([str(column) for column in TEMPLATE_COLUMNS])
    for cell in sheet[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    sheet.freeze_panes = "A2"

    lines = OrderLine.objects.filter(product=product).exclude(order__fulfillment_status=Order.FulfillmentStatus.CANCELLED).select_related("order", "order__purchaser", "beneficiary", "variant").order_by("order__number")
    for line in lines:
        person = line.beneficiary or line.order.purchaser
        sheet.append([line.order.number, person.last_name, person.first_name, line.variant.name if line.variant_id else "", line.personalization_number, line.personalization_name, line.quantity])

    for column_index, column_name in enumerate(TEMPLATE_COLUMNS, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=column_index).column_letter].width = max(12, len(str(column_name)) + 2)

    return workbook
