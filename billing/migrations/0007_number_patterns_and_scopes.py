from django.db import migrations, models


def year_to_scope(apps, schema_editor):
    for name in ("InvoiceSequence", "CreditNoteSequence"):
        model = apps.get_model("billing", name)
        for row in model.objects.all():
            row.scope = str(row.year)
            row.save(update_fields=["scope"])


def scope_to_year(apps, schema_editor):
    for name in ("InvoiceSequence", "CreditNoteSequence"):
        model = apps.get_model("billing", name)
        for row in model.objects.all():
            row.year = int(row.scope[:4]) if row.scope[:4].isdigit() else 0
            row.save(update_fields=["year"])


class Migration(migrations.Migration):
    dependencies = [("billing", "0006_void_and_reissue")]

    operations = [
        migrations.AddField(
            model_name="billingsettings",
            name="invoice_number_pattern",
            field=models.CharField(default="INV-{YYYY}-{NNNNN}", help_text="Placeholders {YYYY} {YY} {MM} and one sequence such as {NNNNN}; see billing.services.numbering.", max_length=40, verbose_name="invoice number pattern"),
        ),
        migrations.AddField(
            model_name="billingsettings",
            name="credit_note_number_pattern",
            field=models.CharField(default="CN-{YYYY}-{NNNNN}", max_length=40, verbose_name="credit note number pattern"),
        ),
        # Sequences: keyed by a scope string instead of the year. Existing rows get scope = str(year), last_number kept.
        migrations.AlterField(model_name="invoicesequence", name="year", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AlterField(model_name="creditnotesequence", name="year", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="invoicesequence", name="scope", field=models.CharField(default="", max_length=16)),
        migrations.AddField(model_name="creditnotesequence", name="scope", field=models.CharField(default="", max_length=16)),
        migrations.RunPython(year_to_scope, scope_to_year),
        migrations.AlterField(model_name="invoicesequence", name="scope", field=models.CharField(max_length=16, unique=True)),
        migrations.AlterField(model_name="creditnotesequence", name="scope", field=models.CharField(max_length=16, unique=True)),
        migrations.RemoveField(model_name="invoicesequence", name="year"),
        migrations.RemoveField(model_name="creditnotesequence", name="year"),
    ]
