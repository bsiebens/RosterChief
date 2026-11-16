"""Member responses to referee and official opportunities."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from events.models import Event, OfficiatingInvitation
from events.services.event_notifications import describe_when_where
from events.services.referees import KINDS, Kind, club_managed_team_ids
from members.services import audit, recipients
from members.services.errors import Invalid
from teams.models import Team


@dataclass(frozen=True)
class Opportunity:
    event: Event
    kind: str
    member_id: UUID
    status: str
    signup_open: bool


def _signup_is_open(event, now):
    return not event.cancelled and now < event.start and (event.deadline is None or now < event.deadline)


def _qualifications(kind, member_ids):
    profiles = kind.profile.objects.filter(member_id__in=member_ids, level__isnull=False, valid_until__isnull=False).select_related("level")
    if kind.code == "referee":
        profiles = profiles.prefetch_related("excluded_teams")
    levels = {}
    qualified = defaultdict(dict)
    for profile in profiles:
        if profile.level_id not in levels:
            levels[profile.level_id] = profile.level.eligible_team_ids()
        teams = levels[profile.level_id]
        if kind.code == "referee":
            teams = profile.eligible_team_ids(teams, ignore_validity=True)
        for team in teams:
            dates = qualified[profile.member_id]
            dates[team] = max(dates.get(team, profile.valid_until), profile.valid_until)
    return qualified


def opportunities(member_ids, *, now=None, days=90):
    """Future home fixtures a member is eligible for, has volunteered for, or has been assigned to."""
    now = now or timezone.now()
    member_ids = set(member_ids)
    if not member_ids:
        return
    qualifications = {kind.code: _qualifications(kind, member_ids) for kind in KINDS.values()}
    games = (
        Event.objects.filter(
            start__gt=now, start__lte=now + timedelta(days=days), cancelled=False, location__is_home=True,
            kind__in=(Event.EventKind.GAME, Event.EventKind.TOURNAMENT),
        )
        .filter(
            Q(teams__referee_management=Team.Management.CLUB) | Q(teams__official_management=Team.Management.CLUB)
            | Q(referees__member_id__in=member_ids) | Q(officials__member_id__in=member_ids)
        )
        .select_related("location", "opponent")
        .prefetch_related("teams", *(
            Prefetch(relation, queryset=model.objects.filter(member_id__in=member_ids))
            for kind in KINDS.values()
            for relation, model in ((kind.related, kind.model), (kind.signup_related, kind.signup))
        ), Prefetch("officiating_invitations", queryset=OfficiatingInvitation.objects.filter(member_id__in=member_ids, declined=True)))
        .distinct().order_by("start", "pk")
    )
    for event in games:
        signup_open = _signup_is_open(event, now)
        declined = {(row.kind, row.member_id) for row in event.officiating_invitations.all()}
        for kind in KINDS.values():
            assigned = {row.member_id for row in getattr(event, kind.related).all()}
            offered = {row.member_id for row in getattr(event, kind.signup_related).all()}
            teams = club_managed_team_ids(event, kind)
            date = timezone.localdate(event.start)
            eligible = {
                member_id for member_id, dates in qualifications[kind.code].items()
                if any(dates[team] >= date for team in teams & dates.keys())
            } if getattr(event, kind.max_field) > 0 else set()
            for member_id in assigned | offered | eligible:
                if member_id in assigned:
                    status = "assigned"
                elif member_id in offered:
                    status = "signed_up"
                else:
                    status = "declined" if (kind.code, member_id) in declined else "invited"
                yield Opportunity(event, kind.code, member_id, status, signup_open)


@transaction.atomic
def respond(actor, event, kind: Kind, member, *, available: bool):
    """A positive reply joins the existing sign-up list and does not reserve a slot."""
    event = Event.objects.select_for_update().get(pk=event.pk)
    if not _signup_is_open(event, timezone.now()):
        raise Invalid("This game is closed for sign-ups.")
    if kind.model.objects.filter(event=event, member=member).exists():
        raise Invalid("You have already been assigned to this game.")
    signup = kind.signup.objects.filter(event=event, member=member).first()
    if signup is None:
        dates = _qualifications(kind, [member.pk]).get(member.pk, {})
        date = timezone.localdate(event.start)
        if not (
            event.is_home_fixture and getattr(event, kind.max_field) > 0
            and any(dates[team] >= date for team in club_managed_team_ids(event, kind) & dates.keys())
        ):
            raise Invalid("You aren't eligible to sign up for this game.")
    invitation, _ = OfficiatingInvitation.objects.select_for_update().get_or_create(event=event, member=member, kind=kind.code)
    if available and signup is None:
        signup = kind.signup.objects.create(event=event, member=member)
        audit.record(actor, f"event.{kind.code}_signup_added", member=member, target=signup, target_label=f"{member} - {event.title}"[:255])
    elif not available and signup is not None:
        audit.record(actor, f"event.{kind.code}_signup_removed", member=member, target=signup, target_label=f"{member} - {event.title}"[:255])
        signup.delete()
    if invitation.declined == available:
        invitation.declined = not available
        invitation.save(update_fields=["declined", "modified"])
    return "signed_up" if available else "declined"


def notify_new_opportunities(club, *, now=None):
    """Send one notification per person for new opportunities in the next 30 days."""
    from features.entitlements import resolve_entitlements
    from members.models import Member

    if not resolve_entitlements(club).has("referee_officials"):
        return 0
    members = Member.objects.filter(Q(referee_profiles__level__isnull=False) | Q(official_profiles__level__isnull=False)).select_related("user").distinct()
    members = {member.pk: member for member in members}
    offers = defaultdict(list)
    for opportunity in opportunities(members, now=now, days=30):
        if opportunity.signup_open and opportunity.status == "invited":
            offers[opportunity.member_id].append(opportunity)
    sent = 0
    for member_id, items in offers.items():
        sent += _notify_member(club, members[member_id], items)
    return sent


@transaction.atomic
def _notify_member(club, member, items):
    known = set(OfficiatingInvitation.objects.filter(member=member, notified=True, event_id__in={item.event.pk for item in items}).values_list("event_id", "kind"))
    pending = []
    games = {}
    for item in items:
        if (item.event.pk, item.kind) in known:
            continue
        invitation, _created = OfficiatingInvitation.objects.select_for_update().get_or_create(event=item.event, member=member, kind=item.kind)
        kind = KINDS[item.kind]
        if not invitation.notified and not invitation.declined and not kind.signup.objects.filter(event=item.event, member=member).exists() and not kind.model.objects.filter(event=item.event, member=member).exists():
            pending.append(invitation.pk)
            games[item.event.pk] = item.event
    if not pending:
        return 0
    first = next(iter(games.values()))
    href = f"/parent?tab=calendar&event={first.pk}" if len(games) == 1 else "/parent?tab=calendar&crew=1"
    body = _("%(game)s, %(when)s.") % {"game": first.title, "when": describe_when_where(first)} if len(games) == 1 else _("There are %(count)s games you can help officiate. Open your calendar to respond.") % {"count": len(games)}
    told = recipients.deliver(member, club, title=_("Games you can help officiate"), body=body, href=href, push=True, category="crew")
    if told:
        OfficiatingInvitation.objects.filter(pk__in=pending).update(notified=True)
    return len(told)
