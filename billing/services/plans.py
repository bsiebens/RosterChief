"""Deleting a plan.

Due.plan is PROTECT -- a plan that has ever billed anyone can never truly be removed, and
must not be: `amount`, `period_end` and `grace_until` are frozen on a Due precisely so a
later change can't rewrite what was actually charged, and losing the plan link off an old
Due would do exactly that to every historical invoice. So "delete" means one of two things,
chosen automatically depending on whether the plan has billing history:

* No Due ever referenced it (created, then never actually used to bill anyone) -- the row
  itself is removed.
* At least one Due references it -- soft-deleted instead (Plan.deleted_at set, is_active
  turned off): hidden from every picker and listing (Plan.objects.visible()), but the row
  survives so every old invoice still says what it was for.

Either way, every club CURRENTLY on the plan is unsubscribed outright -- its Subscription
row deleted, not just its `plan` field cleared. "No plan" is already a state the rest of the
app fully understands (every billing view already handles `getattr(club, "subscription",
None)` being None), so there is no new state to teach it.

A second, easy-to-miss group: a club on a DIFFERENT plan, mid-trial, configured to convert
to THIS plan once its trial ends (Subscription.post_trial_plan). Deleting the target plan
out from under that trial can't be allowed to raise a stale IntegrityError days or weeks
later when the trial tries to convert -- so it's handled now, at delete time: that club's
trial is ended (trial_ends_at and post_trial_plan both cleared, per the CheckConstraint that
requires them set together or not at all), leaving it on the trial plan with no scheduled
conversion until a platform admin picks a new one.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from billing.models import Plan, Subscription


@dataclass(frozen=True)
class PlanDeletionImpact:
    plan: Plan
    #: Clubs currently ON this plan -- lose it entirely (Subscription row deleted).
    unsubscribed_clubs: list
    #: Clubs on a different plan, mid-trial, configured to convert to this one -- their
    #: trial's landing plan is cleared, leaving no scheduled conversion.
    broken_trial_clubs: list
    #: Whether the Plan row itself will be removed (True) or only hidden (False, because
    #: it has billing history).
    will_hard_delete: bool

    @property
    def has_impact(self) -> bool:
        return bool(self.unsubscribed_clubs or self.broken_trial_clubs)


def plan_deletion_impact(plan: Plan) -> PlanDeletionImpact:
    """What deleting `plan` right now would do -- read-only, for the confirmation screen.

    delete_plan() re-derives the same two lists itself rather than trust one computed here
    moments earlier and possibly stale by the time the platform admin actually confirms.
    """
    unsubscribed = Subscription.objects.filter(plan=plan).select_related("club").order_by("club__name")
    broken_trial = Subscription.objects.filter(post_trial_plan=plan).exclude(plan=plan).select_related("club").order_by("club__name")

    return PlanDeletionImpact(
        plan=plan,
        unsubscribed_clubs=[subscription.club for subscription in unsubscribed],
        broken_trial_clubs=[subscription.club for subscription in broken_trial],
        will_hard_delete=not plan.dues.exists(),
    )


@transaction.atomic
def delete_plan(plan: Plan) -> PlanDeletionImpact:
    impact = plan_deletion_impact(plan)

    Subscription.objects.filter(plan=plan).delete()
    Subscription.objects.filter(post_trial_plan=plan).update(trial_ends_at=None, post_trial_plan=None)

    if impact.will_hard_delete:
        plan.delete()
    else:
        plan.deleted_at = timezone.now()
        plan.is_active = False
        plan.save(update_fields=["deleted_at", "is_active", "modified"])

    return impact
