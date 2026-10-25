"""One event as a member (or parent) sees it -- shared by the member app's event page
(mobile.views.EventDetailView) and the app API's ``GET /events/{id}``.

Nothing here reveals another household's answers: the squad summary is counts only,
task claims read as a family label (members.services.family.claim_label_for), and the
per-person answers are only for the managed people passed in.
"""

from django.db.models import Count, Q
from django.utils import timezone

from club.services.access import current_season
from club.services.onboarding import open_requirements_blocking
from events.models import Attendance, Lineup, OfficialSignup, RefereeSignup
from events.services.lineup import selected_members_by_position
from events.services.rsvp import has_started, rsvp_is_closed
from members.services.family import claim_label_for
from teams.models import TeamMembership


def squad_summary(event) -> dict | None:
    """Counts of in/out/no reply across the whole invitation list, or ``None`` when the
    event has no team (nothing meaningful to aggregate) or nobody is invited."""
    if not event.teams.exists():
        return None
    counts = Attendance.objects.filter(event=event).aggregate(
        in_count=Count("id", filter=Q(status__in=[Attendance.AttendanceStatus.PRESENT, Attendance.AttendanceStatus.SELECTED])),
        out_count=Count("id", filter=Q(status__in=[Attendance.AttendanceStatus.ABSENT, Attendance.AttendanceStatus.NOT_SELECTED])),
        no_reply_count=Count("id", filter=Q(status__in=[Attendance.AttendanceStatus.MAYBE, Attendance.AttendanceStatus.NO_RESPONSE])),
    )
    total = counts["in_count"] + counts["out_count"] + counts["no_reply_count"]
    if not total:
        return None
    return {
        "total": total,
        "responded": counts["in_count"] + counts["out_count"],
        "in_count": counts["in_count"],
        "out_count": counts["out_count"],
        "no_reply_count": counts["no_reply_count"],
        "in_pct": round(100 * counts["in_count"] / total),
        "out_pct": round(100 * counts["out_count"] / total),
        "no_reply_pct": round(100 * counts["no_reply_count"] / total),
    }


def event_tasks(event, me) -> list:
    """Every task on ``event``, each annotated with ``claim_labels`` (family labels,
    never who exactly), ``is_full`` and ``claimed_by_me`` (tasks are claimed as the
    account's own member, not per child)."""
    tasks = list(event.tasks.prefetch_related("claims__member").order_by("created_at"))
    for task in tasks:
        claims = list(task.claims.all())
        task.claim_labels = [claim_label_for(claim.member, event.club) for claim in claims]
        task.is_full = len(claims) >= task.needed_quantity
        task.claimed_by_me = me is not None and any(claim.member_id == me.pk for claim in claims)
    return tasks


def member_event_view(event, club, people, me, *, officials_enabled: bool, include_declined: bool = False, now=None) -> dict:
    """Keys: ``rsvp_closed``, ``started``, ``lineup`` (the published one or ``None``) and
    ``lineup_categories``; ``referee_signups`` / ``official_signups`` (managed people's
    open or accepted invites -- plus declined ones until the event starts, with
    ``include_declined``, as the app API's calendar does); ``tasks``; ``attachments`` (the event's files, only when a managed person is invited); ``your_answers`` (``{"member", "attendance",
    "membership"}`` per invited managed person); ``blocked_signups`` (``{"member",
    "requirements"}`` for managed people kept off by onboarding); ``squad_summary``."""
    now = now or timezone.now()
    people = list(people)
    season = event.season or current_season(club)
    # A published line-up supersedes ordinary RSVP: the roster is locked in.
    lineup = Lineup.objects.filter(event=event, published_at__isnull=False).first()

    view = {
        "rsvp_closed": rsvp_is_closed(event, now),
        "started": has_started(event, now),
        "lineup": lineup,
        "lineup_categories": selected_members_by_position(lineup) if lineup is not None else [],
        "referee_signups": [],
        "official_signups": [],
        "tasks": event_tasks(event, me),
        "attachments": [],
        "your_answers": [],
        "blocked_signups": [],
        "squad_summary": squad_summary(event),
    }
    if not people:
        return view

    def signups(model):
        statuses = [model.Status.INVITED, model.Status.ACCEPTED, model.Status.CONFIRMED]
        if include_declined and not view["started"]:
            statuses.append(model.Status.DECLINED)
        return list(model.objects.filter(event=event, member__in=people, status__in=statuses).select_related("member"))

    view["referee_signups"] = signups(RefereeSignup)
    if officials_enabled:
        view["official_signups"] = signups(OfficialSignup)

    managed_ids = [person.pk for person in people]
    attendances_by_member = {attendance.member_id: attendance for attendance in Attendance.objects.filter(event=event, member_id__in=managed_ids).select_related("member")}
    memberships_by_member = {}
    if season is not None:
        memberships_by_member = {membership.member_id: membership for membership in TeamMembership.objects.filter(member_id__in=managed_ids, team__in=event.teams.all(), season=season).select_related("team", "position")}

    for person in people:
        attendance = attendances_by_member.get(person.pk)
        if attendance is None:
            # Not invited -- or kept off by an open onboarding requirement
            # (events.services.attendance.effective_members), which deserves an explanation.
            if season is not None:
                requirements = open_requirements_blocking(person, club, season, event.kind)
                if requirements:
                    view["blocked_signups"].append({"member": person, "requirements": requirements})
            continue
        view["your_answers"].append({"member": person, "attendance": attendance, "membership": memberships_by_member.get(person.pk)})
    # Attachments are for the invited: anyone answering for an invited person gets the list.
    if view["your_answers"]:
        view["attachments"] = list(event.attachments.all())
    return view
