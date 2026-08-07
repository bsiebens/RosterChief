"""Build a tabular overview of all answers for a Form."""

from dataclasses import dataclass

from formbuilder.models import Field

CHOICE_TYPES = {Field.FieldType.CHOICE, Field.FieldType.MULTICHOICE, Field.FieldType.CHECKBOX}


@dataclass
class ReportRow:
    submission: object
    values: dict  # field id -> answer value


@dataclass
class FormReport:
    form: object
    columns: list  # Field instances, in display order
    rows: list  # ReportRow, newest submission first
    summaries: dict  # field id -> {stringified value: count} for choice-like fields
    count: int


def form_report(form):
    """Return a FormReport: one column per field, one row per submission."""
    columns = list(form.fields.order_by("order"))
    summaries = {column.id: {} for column in columns if column.field_type in CHOICE_TYPES}

    rows = []
    submissions = form.submissions.select_related("member").prefetch_related("answers")
    for submission in submissions:
        values = {answer.field_id: answer.value for answer in submission.answers.all()}
        rows.append(ReportRow(submission=submission, values=values))
        for field_id, bucket in summaries.items():
            if field_id in values:
                _tally(bucket, values[field_id])

    return FormReport(form=form, columns=columns, rows=rows, summaries=summaries, count=len(rows))


def _tally(bucket, value):
    items = value if isinstance(value, list) else [value]
    for item in items:
        key = str(item)
        bucket[key] = bucket.get(key, 0) + 1
