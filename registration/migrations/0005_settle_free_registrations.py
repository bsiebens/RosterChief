from django.db import migrations


def settle(apps, schema_editor):
    """One-off: a person with nothing to pay on a confirmed registration (a volunteer, a credit line) now reads as paid, as it does for new ones."""
    ClubMembership = apps.get_model("members", "ClubMembership")
    ClubMembership.objects.filter(
        registration_details__batch__confirmed_at__isnull=False,
        registration_details__batch__cancelled_at__isnull=True,
        fee_amount=0,
        fee_status="unpaid",
        payments__isnull=True,
    ).update(fee_status="paid")


class Migration(migrations.Migration):
    dependencies = [
        ("registration", "0004_registrationdetails_order_discount_share"),
        ("members", "0015_feepayment_voucher"),
    ]

    operations = [migrations.RunPython(settle, migrations.RunPython.noop)]
