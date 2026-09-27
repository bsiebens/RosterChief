"""Neutralising user-supplied text before it goes into a CSV or Excel export.

A spreadsheet application treats a cell starting with ``=``, ``+``, ``-`` or ``@``
(and, in some, a leading tab or carriage return) as a formula, so a form answer or
a personalised name such as ``=HYPERLINK(...)`` would run when a club admin opens
the export ("CSV/formula injection"). Prefixing such a string with an apostrophe
makes every common spreadsheet application show it as plain text instead.

Only strings are touched: numbers, dates and ``None`` pass through unchanged, so
numeric columns stay numeric.
"""

from django.utils.functional import Promise

FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """``value``, with an apostrophe prefixed when it's a string a spreadsheet
    application would otherwise read as a formula."""
    if isinstance(value, Promise):
        value = str(value)
    if isinstance(value, str) and value.startswith(FORMULA_TRIGGERS):
        return "'" + value
    return value


def safe_row(values):
    """``safe_cell`` over a whole row, e.g. for ``csv.writer.writerow``."""
    return [safe_cell(value) for value in values]


def append_safe_row(sheet, values):
    """``sheet.append(values)`` for an openpyxl worksheet, neutralised as above.

    Every cell that received a string is also pinned to the string data type, so
    openpyxl can never store it as a formula whatever it starts with."""
    sheet.append(safe_row(values))
    for cell in sheet[sheet.max_row]:
        if isinstance(cell.value, str):
            cell.data_type = "s"
