"""The responses of one FormSend: a table, per-choice summaries, who has not responded, and a CSV export."""

import csv
import io
from dataclasses import dataclass, field

from formbuilder.models import Field

from .audience import effective_members
from .options import field_choices

CHOICE_TYPES = {Field.FieldType.CHOICE, Field.FieldType.MULTICHOICE, Field.FieldType.CHECKBOX}
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class ReportRow:
    submission: object
    values: dict  # field id -> answer value (a file answer: its original file name)
    files: dict = field(default_factory=dict)  # field id -> Answer holding a stored file


@dataclass
class FormReport:
    send: object
    columns: list  # Field instances in display order
    rows: list  # ReportRow, newest submission first
    summaries: dict  # field id -> {value: count} for choice-like fields
    count: int
    not_responded: list  # Members in the audience without a submission

    @property
    def form(self):
        return self.send.form


def form_report(send) -> FormReport:
    """One column per field (active ones, plus deactivated ones that have answers here), one row per submission to THIS send."""
    submissions = list(send.submissions.select_related("member", "submitted_by").prefetch_related("answers"))
    answered_ids = {a.field_id for s in submissions for a in s.answers.all()}
    columns = [f for f in send.form.fields.order_by("order") if f.is_active or f.pk in answered_ids]
    summaries = {}
    for column in columns:
        if column.field_type in CHOICE_TYPES:
            if column.field_type == Field.FieldType.CHECKBOX:
                summaries[column.pk] = {"True": 0, "False": 0}
            else:
                summaries[column.pk] = {value: 0 for value, _label in field_choices(column)}

    rows = []
    for submission in submissions:
        answers = list(submission.answers.all())
        values = {a.field_id: (a.file_name if a.file else a.value) for a in answers}
        files = {a.field_id: a for a in answers if a.file}
        rows.append(ReportRow(submission=submission, values=values, files=files))
        for field_id, bucket in summaries.items():
            if field_id in values:
                _tally(bucket, values[field_id])

    responded = {s.member_id for s in submissions if s.member_id}
    not_responded = list(effective_members(send).exclude(pk__in=responded).order_by("last_name", "first_name"))
    return FormReport(send=send, columns=columns, rows=rows, summaries=summaries, count=len(rows), not_responded=not_responded)


def _tally(bucket, value):
    for item in value if isinstance(value, list) else [value]:
        key = str(item)
        bucket[key] = bucket.get(key, 0) + 1


def display_value(value) -> str:
    """An answer value as plain text: a file answer as its original name, a multi-choice answer comma-separated."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("name", ""))
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _safe_cell(text: str) -> str:
    """Neutralise spreadsheet formulas in user-typed text (numbers keep their sign)."""
    if text.startswith(_FORMULA_START):
        try:
            float(text)
        except ValueError:
            return "'" + text
    return text


def report_csv(report: FormReport) -> str:
    """UTF-8 text with a leading BOM (so Excel opens it right): submitted at, member, submitted by, one column per field."""
    from django.utils import timezone

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Submitted at", "Member", "Submitted by", *[_safe_cell(c.label) for c in report.columns]])
    for row in report.rows:
        s = row.submission
        writer.writerow(
            [
                timezone.localtime(s.submitted_at).strftime("%Y-%m-%d %H:%M"),
                _safe_cell(str(s.member)) if s.member_id else "",
                _safe_cell(str(s.submitted_by)) if s.submitted_by_id else "",
                *[_safe_cell(display_value(row.values.get(c.pk))) for c in report.columns],
            ]
        )
    return "﻿" + buffer.getvalue()
