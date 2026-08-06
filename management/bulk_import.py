"""Mass-uploading members from an Excel template: extracting a workbook into plain
row data, and validating that data into what will be created.

Kept as two separate functions so binary parsing happens exactly once (at upload
time) while validation -- the part that must behave identically whether it's
building the preview or actually creating records -- runs against plain data both
times (see MemberImportView / MemberImportConfirmView in management/views.py).
"""

from datetime import date, datetime

import openpyxl
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from openpyxl.worksheet.datavalidation import DataValidation

from club.models import ClubMembership
from members.models import Member

from .forms import MemberForm

TEMPLATE_COLUMNS = ["first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone", "license", "status", "fee_status"]
REQUIRED_HEADER_COLUMNS = {"first_name", "last_name"}

TEMPLATE_EXAMPLE_ROW = ["Alex", "Morgan", date(2012, 5, 14), "alex.morgan@example.com", "+32470123456", "+32470654321", "", ClubMembership.StatusChoices.ACTIVE, ClubMembership.FeeStatus.UNPAID]


def build_member_import_template():
    """The downloadable .xlsx: header row, one example row, and a dropdown on the
    status/fee_status columns so a cell can't be typo'd into an invalid value."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Members"

    sheet.append(TEMPLATE_COLUMNS)
    for cell in sheet[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    sheet.freeze_panes = "A2"

    sheet.append(TEMPLATE_EXAMPLE_ROW)

    for column_name, choices in (("status", ClubMembership.StatusChoices), ("fee_status", ClubMembership.FeeStatus)):
        column_index = TEMPLATE_COLUMNS.index(column_name) + 1
        column_letter = sheet.cell(row=1, column=column_index).column_letter
        options = ",".join(choices.values)
        validation = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True)
        sheet.add_data_validation(validation)
        validation.add(f"{column_letter}2:{column_letter}1000")

    for column_index, column_name in enumerate(TEMPLATE_COLUMNS, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=column_index).column_letter].width = max(12, len(column_name) + 2)

    return workbook


def read_member_import_workbook(file):
    """Uploaded .xlsx -> list[dict], one dict per row keyed by TEMPLATE_COLUMNS.

    Normalizes every cell to a plain str/None here: openpyxl returns typed cells
    (a date-formatted cell comes back as a datetime.date, a phone number typed as
    digits-only can come back as a number), so this is the one place that has to
    deal with that -- everything downstream, including the session, only ever
    sees plain strings.
    """
    workbook = openpyxl.load_workbook(file, data_only=True)
    sheet = workbook.active

    header = [str(cell.value).strip() if cell.value is not None else "" for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    if not REQUIRED_HEADER_COLUMNS.issubset(header):
        missing = REQUIRED_HEADER_COLUMNS - set(header)
        raise ValueError(_("This doesn't look like the template — missing column(s): %(columns)s.") % {"columns": ", ".join(sorted(missing))})

    rows = []
    for excel_row in sheet.iter_rows(min_row=2):
        values = {}
        for column_name, cell in zip(header, excel_row, strict=False):
            if column_name not in TEMPLATE_COLUMNS:
                continue
            values[column_name] = _cell_to_str(cell.value)

        if not any(values.values()):
            continue  # a fully blank row (trailing spreadsheet padding) isn't a row to import
        rows.append(values)

    return rows


def _cell_to_str(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_member_import_rows(rows, club):
    """list[dict] (as returned by read_member_import_workbook) -> one result per
    row: {line_number, raw, member, membership_kwargs, errors}. `member` is an
    unsaved Member instance (ready to .save()) when the row is valid, else None."""
    results = []
    seen_emails = set()

    for line_number, raw in enumerate(rows, start=2):
        errors = []
        member_fields = {key: raw.get(key, "") for key in ("first_name", "last_name", "date_of_birth", "email", "phone", "emergency_phone")}
        form = MemberForm(data=member_fields)

        member = None
        if form.is_valid():
            member = form.save(commit=False)
        else:
            for field_errors in form.errors.values():
                errors.extend(field_errors)

        email = member_fields["email"].strip()
        if email:
            if email.lower() in seen_emails:
                errors.append(_("Duplicate email in this file."))
            seen_emails.add(email.lower())
            already_in_club = Member.objects.filter(member_of__club=club).filter(Q(email__iexact=email) | Q(user__email__iexact=email)).exists()
            if already_in_club:
                errors.append(_("Already a member of this club."))

        membership_kwargs, status_fee_errors = _parse_membership_fields(raw)
        errors.extend(status_fee_errors)

        results.append({"line_number": line_number, "raw": raw, "member": member if not errors else None, "membership_kwargs": membership_kwargs, "errors": errors})

    return results


def _parse_membership_fields(raw):
    errors = []
    license_number = raw.get("license", "").strip()

    status = raw.get("status", "").strip()
    if status:
        status_value = _match_choice(status, ClubMembership.StatusChoices)
        if status_value is None:
            errors.append(_("Invalid status '%(value)s'.") % {"value": status})
    else:
        status_value = ClubMembership.StatusChoices.ACTIVE

    fee_status = raw.get("fee_status", "").strip()
    if fee_status:
        fee_status_value = _match_choice(fee_status, ClubMembership.FeeStatus)
        if fee_status_value is None:
            errors.append(_("Invalid fee status '%(value)s'.") % {"value": fee_status})
    else:
        fee_status_value = ClubMembership.FeeStatus.UNPAID

    return {"license": license_number, "status": status_value, "fee_status": fee_status_value}, errors


def _match_choice(value, choices):
    for choice_value in choices.values:
        if choice_value.lower() == value.lower():
            return choice_value
    return None
