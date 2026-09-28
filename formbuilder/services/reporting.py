"""Build a tabular overview of all answers for one FormSend."""

from dataclasses import dataclass, field

from formbuilder.models import Field

CHOICE_TYPES = {Field.FieldType.CHOICE, Field.FieldType.MULTICHOICE, Field.FieldType.CHECKBOX}


@dataclass
class ReportRow:
    submission: object
    values: dict  # field id -> answer value
    files: dict = field(default_factory=dict)  # field id -> Answer with a stored file


@dataclass
class FormReport:
    send: object
    columns: list  # Field instances, in display order
    rows: list  # ReportRow, newest submission first
    summaries: dict  # field id -> {stringified value: count} for choice-like fields
    count: int

    @property
    def form(self):
        return self.send.form


def form_report(send):
    """Return a FormReport for one occasion of sending a form: one column per
    field, one row per submission to THIS send -- not every send of the same
    form ever, which would mix logically separate response sets together
    (see FormSend's own docstring)."""
    columns = list(send.form.fields.order_by("order"))
    summaries = {column.id: {} for column in columns if column.field_type in CHOICE_TYPES}

    rows = []
    submissions = send.submissions.select_related("member", "submitted_by").prefetch_related("answers")
    for submission in submissions:
        values = {answer.field_id: answer.value for answer in submission.answers.all()}
        files = {answer.field_id: answer for answer in submission.answers.all() if answer.file}
        rows.append(ReportRow(submission=submission, values=values, files=files))
        for field_id, bucket in summaries.items():
            if field_id in values:
                _tally(bucket, values[field_id])

    return FormReport(send=send, columns=columns, rows=rows, summaries=summaries, count=len(rows))


def _tally(bucket, value):
    items = value if isinstance(value, list) else [value]
    for item in items:
        key = str(item)
        bucket[key] = bucket.get(key, 0) + 1


def display_value(value):
    """An answer value as plain text: a file answer as its original file name
    (never its storage path), a multi-choice answer as a comma-separated list."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("name", ""))
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)
