import uuid

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def terms_to_cycles(apps, schema_editor):
    """Every plan's single set of terms becomes its first billing cycle; its prices, its clubs and its periods point to it."""
    Plan = apps.get_model("features", "Plan")
    Subscription = apps.get_model("features", "Subscription")
    PlanTerms = apps.get_model("billing", "PlanTerms")
    PlanCycle = apps.get_model("billing", "PlanCycle")
    PlanPrice = apps.get_model("billing", "PlanPrice")
    ClubBilling = apps.get_model("billing", "ClubBilling")
    Due = apps.get_model("billing", "Due")
    cycles = {}
    for plan in Plan.objects.all():
        terms = PlanTerms.objects.filter(plan_id=plan.pk).first()
        values = {"duration_months": terms.duration_months, "renewal_lead_days": terms.renewal_lead_days, "grace_days": terms.grace_days} if terms else {}
        cycles[plan.pk] = PlanCycle.objects.create(plan_id=plan.pk, position=0, **values)
    for plan_id, cycle in cycles.items():
        PlanPrice.objects.filter(plan_id=plan_id).update(cycle=cycle)
        Due.objects.filter(plan_id=plan_id).update(cycle=cycle)
    for billing in ClubBilling.objects.all():
        current = Subscription.objects.filter(club_id=billing.club_id, ends_at__isnull=True).first()
        if current is not None and current.plan_id in cycles:
            ClubBilling.objects.filter(pk=billing.pk).update(cycle=cycles[current.plan_id])


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0009_invoicing_mode"),
        ("features", "0003_plan_position"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlanCycle",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created", models.DateTimeField(auto_now_add=True, verbose_name="created")),
                ("modified", models.DateTimeField(auto_now=True, verbose_name="modified")),
                ("label", models.CharField(blank=True, help_text="Optional. Empty = named after its length (Monthly, Quarterly, Yearly ...).", max_length=60, verbose_name="name")),
                ("duration_months", models.PositiveSmallIntegerField(default=12, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(120)], verbose_name="period length (months)")),
                ("renewal_lead_days", models.PositiveSmallIntegerField(default=30, verbose_name="invoice this many days before the period starts")),
                ("grace_days", models.PositiveSmallIntegerField(default=30, verbose_name="grace days, counted from the period start or from the day it was opened, whichever is later")),
                ("position", models.PositiveIntegerField(db_index=True, default=0, verbose_name="position")),
                ("is_active", models.BooleanField(default=True, help_text="Inactive cycles cannot be chosen for a club any more. Clubs already on it keep renewing.", verbose_name="active")),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="cycles", to="features.plan", verbose_name="plan")),
            ],
            options={"verbose_name": "plan billing cycle", "verbose_name_plural": "plan billing cycles", "ordering": ["plan", "position", "duration_months"]},
        ),
        migrations.AddField(
            model_name="clubbilling",
            name="cycle",
            field=models.ForeignKey(blank=True, help_text="How the club is billed on its plan (monthly, yearly ...). Empty = the plan's first cycle.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="club_billings", to="billing.plancycle", verbose_name="billing cycle"),
        ),
        migrations.AddField(
            model_name="due",
            name="cycle",
            field=models.ForeignKey(blank=True, help_text="The cycle this period was opened on (its figures are frozen on the period itself).", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="dues", to="billing.plancycle", verbose_name="billing cycle"),
        ),
        migrations.AddField(
            model_name="planprice",
            name="cycle",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="prices", to="billing.plancycle", verbose_name="billing cycle"),
        ),
        migrations.RunPython(terms_to_cycles, migrations.RunPython.noop),
        migrations.AlterModelOptions(name="planprice", options={"ordering": ["plan", "cycle", "-active_from"], "verbose_name": "plan price", "verbose_name_plural": "plan prices"}),
        migrations.RemoveConstraint(model_name="planprice", name="uniq_plan_price_date"),
        migrations.AddConstraint(model_name="planprice", constraint=models.UniqueConstraint(fields=("cycle", "active_from"), name="uniq_cycle_price_date")),
        migrations.AddConstraint(model_name="plancycle", constraint=models.UniqueConstraint(fields=("plan", "duration_months"), name="uniq_plan_cycle_length")),
        migrations.DeleteModel(name="PlanTerms"),
    ]
