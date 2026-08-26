"""Data migration: give every pre-existing Form a FormSend to hang its
timing/submissions off, before 0008 removes Form.opens_at/closes_at/
max_submissions_per_user and Submission.form for good.

Defensive rather than a guaranteed no-op: formbuilder shipped with Django-admin
CRUD (formbuilder/admin.py) well before any front-end existed, so a curious
staff member could already have created real Form/Submission rows through it.
No audience is set on the backfilled FormSend -- there's nobody to notify
retroactively, and staff can configure one going forward.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Form = apps.get_model("formbuilder", "Form")
    FormSend = apps.get_model("formbuilder", "FormSend")
    Submission = apps.get_model("formbuilder", "Submission")

    needs_a_send = Form.objects.filter(opens_at__isnull=False) | Form.objects.filter(closes_at__isnull=False) | Form.objects.filter(max_submissions_per_user__isnull=False) | Form.objects.filter(submissions__isnull=False)
    for form in needs_a_send.distinct():
        send = FormSend.objects.create(
            club=form.club,
            form=form,
            opens_at=form.opens_at,
            closes_at=form.closes_at,
            max_submissions_per_user=form.max_submissions_per_user,
            is_active=form.is_active,
        )
        Submission.objects.filter(form=form).update(send=send)


def noop_reverse(apps, schema_editor):
    # Nothing to reverse into -- 0008 (the next migration) removes the fields
    # this backfill reads from, so a real reverse would have nothing to
    # restore anyway. Matches this repo's "prefer additive migrations" house
    # rule: unwinding this app is a restore-from-backup operation, not a
    # migration one (see DEPLOYMENT.md).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("formbuilder", "0006_formsend"),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
