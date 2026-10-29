"""Saving the fee line (fee, travel km / rate, "No fee") of the referees and officials on one game.

Shared by the /manage fees form (management.views.EventFeesUpdateView) and the management API.
``data`` is what a browser posts: each assignment's fields prefixed ``referee_<pk>-`` /
``official_<pk>-`` (``fee``, ``km``, ``km_rate``, ``no_fee``). A row whose ``-fee`` key isn't
present is left alone, and so is one that didn't change.
"""

from dataclasses import dataclass, field

from events.services.officials import set_official_fee
from events.services.referees import set_referee_fee


@dataclass
class FeeRowFailure:
    kind: str  # "referee" | "official"
    assignment: object
    errors: dict


@dataclass
class FeeUpdate:
    saved: list[str] = field(default_factory=list)  # display names
    failed: list[FeeRowFailure] = field(default_factory=list)


def update_fees(event, data, *, include_officials: bool, all_or_nothing: bool = False) -> FeeUpdate:
    """Validate and save the posted fee rows. A row that doesn't validate is reported in ``failed``;
    the others are still saved, unless ``all_or_nothing`` (then nothing is written when any failed)."""
    from management.forms import EventOfficialFeeForm, EventRefereeFeeForm  # deferred: management imports events

    rows = [("referee", assignment, EventRefereeFeeForm, set_referee_fee) for assignment in event.referees.select_related("member")]
    if include_officials:
        rows += [("official", assignment, EventOfficialFeeForm, set_official_fee) for assignment in event.officials.select_related("member")]

    result = FeeUpdate()
    to_save = []
    for kind, assignment, form_class, save in rows:
        prefix = f"{kind}_{assignment.pk}"
        if f"{prefix}-fee" not in data:
            continue
        form = form_class(data, instance=assignment, prefix=prefix)
        if not form.is_valid():
            result.failed.append(FeeRowFailure(kind, assignment, {name: [str(message) for message in messages] for name, messages in form.errors.items()}))
            continue
        if form.has_changed():
            to_save.append((assignment, save, form.cleaned_data))

    if result.failed and all_or_nothing:
        return result
    for assignment, save, cleaned in to_save:
        save(assignment, fee=cleaned["fee"], km=cleaned["km"], km_rate=cleaned["km_rate"], no_fee=cleaned["no_fee"])
        result.saved.append(assignment.display_name)
    return result
