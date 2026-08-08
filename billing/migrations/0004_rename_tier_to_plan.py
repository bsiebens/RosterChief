"""Rename Tier -> Plan, and every field that pointed at it.

Hand-written rather than generated. `makemigrations` only detects a rename by asking
interactively; run non-interactively it emits DeleteModel + CreateModel instead, which drops
every price, subscription and due in the table. RenameModel/RenameField preserve the data.

The two RemoveConstraints have to come FIRST. Both constraints name fields this migration is
about to rename (`tier`, `post_trial_tier`), and SQLite implements a rename by rebuilding the
table -- which re-renders every constraint on it. Left in place, the rebuild tries to emit a
constraint over a column that no longer exists under that name and dies with
FieldDoesNotExist. 0005 adds them back under the new field names.

Split from the field additions (0005) so this migration is pure renaming and can be read --
and if necessary reversed -- without any other change mixed into it.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0003_due_is_trial_subscription_post_trial_tier_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="tierprice", name="unique_tier_price_per_start_date"),
        migrations.RemoveConstraint(model_name="subscription", name="trial_fields_set_together"),
        migrations.RenameModel(old_name="Tier", new_name="Plan"),
        migrations.RenameModel(old_name="TierPrice", new_name="PlanPrice"),
        migrations.RenameField(model_name="planprice", old_name="tier", new_name="plan"),
        migrations.RenameField(model_name="subscription", old_name="tier", new_name="plan"),
        migrations.RenameField(model_name="subscription", old_name="post_trial_tier", new_name="post_trial_plan"),
        migrations.RenameField(model_name="due", old_name="tier", new_name="plan"),
    ]
