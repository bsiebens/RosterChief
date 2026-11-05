"""Event tasks: logistics asks on an event ("bring fruit for half-time", "drive to the away game") that people claim.

Who may do what
* **See** an event's tasks and who claimed them (``can_view_event_extras``, shared with attachments): everyone who got
  into the management app (``access.can_access_management``), and the event's audience: an account whose own member,
  or a child in a family where that member is a parent/guardian, has an attendance row on the event.
* **Create, edit, delete** tasks (``can_manage_tasks``): ``access.can_manage_attendance`` -- settings holders and
  managers (a management position) of any of the event's teams. Assistant staff without a management position and
  managers of other teams only see them.
* **Claim** a slot: a member claims for themselves (the actor's own member record, which must be able to see the
  event's tasks). Task managers may also claim, or remove a claim, on behalf of any member. A claimant can always give
  their own claim back.

Capacity (``EventTask.needed_quantity``) is a hard ceiling, enforced with the task row locked so two people taking the
last slot at the same moment cannot both get it; one member claims a task at most once (also a DB constraint).

Claims are shown to others under the claimant's family label (``claim_label_for``): "<Family> family" when the member
is in a family, else their own name. The team only needs to know it is handled, not which parent or child does it.

Notifications (inbox + push, category "event", only to members with a login, never to the actor themself)
* ``create_task(..., notify=True)``: the event's audience (members with an attendance row) hears about the new task.
* A manager claiming / removing a claim on someone's behalf: that member is told.
* A member giving their own claim back: the task's creator is told (a slot opened up again).
* Deleting a task that had claims: the claimants are told it is no longer needed.
"""

from __future__ import annotations

from django.db import transaction

from events.models import Attendance, Event, EventTask, EventTaskClaim
from events.services.attendance import current_club
from members.models import FamilyMembership, Member
from members.services import access, audit
from members.services.errors import Invalid, NotAllowed

TITLE_MAX = 255
DESCRIPTION_MAX = 2000
MAX_NEEDED_QUANTITY = 999
TASK_FIELDS = ("title", "description", "needed_quantity")


# --- permissions ---------------------------------------------------------------------------------------------------


def _actor_member(actor) -> Member | None:
    if actor is None or not getattr(actor, "pk", None) or not actor.is_authenticated:
        return None
    return Member.objects.filter(user=actor).first()


def people_of(actor) -> set:
    """Ids of the members ``actor`` speaks for: their own member, plus the children of families in which that member is
    a parent or guardian."""
    me = _actor_member(actor)
    if me is None:
        return set()
    family_ids = FamilyMembership.objects.filter(member=me, role__in=FamilyMembership.GUARDIAN_ROLES).values("family_id")
    children = FamilyMembership.objects.filter(family_id__in=family_ids, role=FamilyMembership.FamilyRole.CHILD).values_list("member_id", flat=True)
    return {me.pk, *children}


def can_view_event_extras(user, event: Event) -> bool:
    """May ``user`` see ``event``'s tasks and attachments? Everyone in the management app, plus the event's audience
    (their own member or one of their children has an attendance row on it)."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    if access.can_access_management(user):
        return True
    people = people_of(user)
    return bool(people) and Attendance.objects.filter(event=event, member_id__in=people).exists()


def can_manage_tasks(user, event: Event) -> bool:
    return access.can_manage_attendance(user, event)


def require_task_manager(actor, event: Event) -> None:
    if not can_manage_tasks(actor, event):
        raise NotAllowed("You are not allowed to manage the tasks of this event.")


def require_view(actor, event: Event) -> None:
    if not can_view_event_extras(actor, event):
        raise NotAllowed("You are not allowed to see this event's tasks.")


# --- labels and listing --------------------------------------------------------------------------------------------


def claim_label_for(member: Member) -> str:
    """How ``member`` reads on a claim others can see: "<Family> family" (Family.__str__: its name, else the surnames)
    for the first family they belong to, else their own name."""
    membership = member.family_memberships.select_related("family").order_by("family__name", "created").first()
    if membership is not None:
        return f"{membership.family} family"
    return member.get_full_name()


def list_tasks(actor, event: Event) -> list[EventTask]:
    """``event``'s tasks (oldest first), each annotated with ``claim_list`` (the claims with their members),
    ``claim_labels``, ``claimed_count``, ``open_slots`` and ``claimed_by_me`` (the actor's own member claimed it)."""
    require_view(actor, event)
    me = _actor_member(actor)
    tasks = list(event.tasks.prefetch_related("claims__member").order_by("created"))
    for task in tasks:
        claims = list(task.claims.all())
        task.claim_list = claims
        task.claim_labels = [claim_label_for(claim.member) for claim in claims]
        task.claimed_count = len(claims)
        task.open_slots = max(task.needed_quantity - len(claims), 0)
        task.claimed_by_me = me is not None and any(claim.member_id == me.pk for claim in claims)
    return tasks


# --- notifications -------------------------------------------------------------------------------------------------


def _href(event: Event) -> str:
    return f"/calendar/events/{event.pk}"


def _notify_users(users, *, title: str, body: str, href: str, actor=None) -> None:
    club = current_club()
    if club is None:
        return
    from notifications import inbox

    seen = set()
    for user in users:
        if user is None or user.pk in seen or (actor is not None and user.pk == getattr(actor, "pk", None)):
            continue
        seen.add(user.pk)
        inbox.notify(user, club, title=title, body=body, kind=inbox.Kind.INFO, href=href, push=True, category="event")


def _notify_members(members, **kwargs) -> None:
    _notify_users([m.user for m in members if m is not None and m.user_id], **kwargs)


# --- tasks ---------------------------------------------------------------------------------------------------------


def _clean(*, title=None, description=None, needed_quantity=None, partial=False) -> dict:
    errors, data = {}, {}
    if title is not None or not partial:
        title = (title or "").strip()
        if not title:
            errors["title"] = ["A title is required."]
        elif len(title) > TITLE_MAX:
            errors["title"] = [f"At most {TITLE_MAX} characters."]
        data["title"] = title
    if description is not None or not partial:
        description = (description or "").strip()
        if len(description) > DESCRIPTION_MAX:
            errors["description"] = [f"At most {DESCRIPTION_MAX} characters."]
        data["description"] = description
    if needed_quantity is not None or not partial:
        quantity = 1 if needed_quantity is None else needed_quantity
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_NEEDED_QUANTITY:
            errors["needed_quantity"] = [f"A whole number from 1 to {MAX_NEEDED_QUANTITY}."]
        data["needed_quantity"] = quantity
    if errors:
        raise Invalid("Please correct the task.", errors)
    return data


@transaction.atomic
def create_task(actor, event: Event, *, title: str, description: str = "", needed_quantity: int = 1, notify: bool = False) -> EventTask:
    """Add a task to ``event``. With ``notify`` the event's audience (members with an attendance row and a login) is told."""
    require_task_manager(actor, event)
    data = _clean(title=title, description=description, needed_quantity=needed_quantity)
    task = EventTask.objects.create(event=event, created_by=_actor_member(actor), **data)
    audit.record(actor, "event.task_created", target=task, changes=audit.snapshot(task, TASK_FIELDS))
    if notify:
        audience = Member.objects.filter(attendances__event=event, user__isnull=False).select_related("user").distinct()
        transaction.on_commit(lambda: _notify_members(list(audience), title="New task", body=f"{event.title}: “{task.title}” needs {task.needed_quantity} {'person' if task.needed_quantity == 1 else 'people'}.", href=_href(event), actor=actor))
    return task


@transaction.atomic
def update_task(actor, task: EventTask, *, title: str | None = None, description: str | None = None, needed_quantity: int | None = None) -> EventTask:
    """Change the given fields. ``needed_quantity`` may not drop below the number of claims already made."""
    task = EventTask.objects.select_for_update().select_related("event").get(pk=task.pk)
    require_task_manager(actor, task.event)
    data = _clean(title=title, description=description, needed_quantity=needed_quantity, partial=True)
    if "needed_quantity" in data:
        claimed = task.claims.count()
        if data["needed_quantity"] < claimed:
            raise Invalid("Please correct the task.", {"needed_quantity": [f"{claimed} already signed up; remove claims first or keep at least {claimed}."]})
    changes = audit.diff(task, data)
    if changes:
        for name, value in data.items():
            setattr(task, name, value)
        task.save(update_fields=[*data, "modified"])
        audit.record(actor, "event.task_updated", target=task, changes=changes)
    return task


@transaction.atomic
def delete_task(actor, task: EventTask) -> None:
    """Delete the task and its claims; the claimants are told it is no longer needed."""
    event = task.event
    require_task_manager(actor, event)
    claimants = [claim.member for claim in task.claims.select_related("member__user")]
    title, label = task.title, str(task)
    audit.record(actor, "event.task_deleted", target=task, target_label=label, changes={**audit.snapshot(task, TASK_FIELDS, deleted=True), "claims": {"from": len(claimants), "to": None}})
    task.delete()
    if claimants:
        transaction.on_commit(lambda: _notify_members(claimants, title="Task removed", body=f"{event.title}: “{title}” is no longer needed.", href=_href(event), actor=actor))


# --- claims --------------------------------------------------------------------------------------------------------


@transaction.atomic
def claim_task(actor, task: EventTask, member: Member | None = None) -> EventTaskClaim:
    """Claim one slot of ``task`` for ``member`` (default: the actor's own member). Claiming for someone else needs task
    management rights. Raises ``Invalid`` when the member already claimed it or every slot is taken."""
    task = EventTask.objects.select_for_update().select_related("event").get(pk=task.pk)
    event = task.event
    me = _actor_member(actor)
    member = member or me
    if member is None:
        raise Invalid("There is no member record for this account.", {"member": ["There is no member record for this account."]})
    on_behalf = me is None or member.pk != me.pk
    if on_behalf:
        require_task_manager(actor, event)
    else:
        require_view(actor, event)
    if task.claims.filter(member=member).exists():
        raise Invalid(f"{member.get_full_name()} has already claimed this.", {"member": [f"{member.get_full_name()} has already claimed this."]})
    if task.claims.count() >= task.needed_quantity:
        raise Invalid("This is already fully covered.", {"task": ["This is already fully covered."]})
    claim = EventTaskClaim.objects.create(task=task, member=member)
    audit.record(actor, "event.task_claimed", member=member, target=task, changes={"claimed_by": {"from": None, "to": str(member.pk)}})
    if on_behalf:
        transaction.on_commit(lambda: _notify_members([member], title="You were signed up for a task", body=f"{event.title}: you are down for “{task.title}”.", href=_href(event), actor=actor))
    return claim


@transaction.atomic
def unclaim_task(actor, claim: EventTaskClaim) -> None:
    """Give a claim back: the claimant themself, or a task manager on their behalf (the member is then told). When the
    claimant gives it back themself the task's creator is told a slot opened up."""
    claim = EventTaskClaim.objects.select_related("task__event", "task__created_by__user", "member__user").get(pk=claim.pk)
    task, member, event = claim.task, claim.member, claim.task.event
    me = _actor_member(actor)
    own = me is not None and me.pk == member.pk
    if not own:
        require_task_manager(actor, event)
    claim.delete()
    audit.record(actor, "event.task_unclaimed", member=member, target=task, changes={"claimed_by": {"from": str(member.pk), "to": None}})
    if own:
        creator = task.created_by
        transaction.on_commit(lambda: _notify_members([creator], title="Task slot open again", body=f"{event.title}: {claim_label_for(member)} can no longer do “{task.title}”.", href=_href(event), actor=actor))
    else:
        transaction.on_commit(lambda: _notify_members([member], title="Removed from a task", body=f"{event.title}: you are no longer down for “{task.title}”.", href=_href(event), actor=actor))


def unclaim_member(actor, task: EventTask, member: Member | None = None) -> bool:
    """Remove ``member``'s (default: the actor's own) claim on ``task`` if there is one. Safe to repeat: ``False`` when
    there was nothing to remove."""
    me = _actor_member(actor)
    member = member or me
    if member is None:
        return False
    if me is None or member.pk != me.pk:
        require_task_manager(actor, task.event)
    claim = task.claims.filter(member=member).first()
    if claim is None:
        return False
    unclaim_task(actor, claim)
    return True
