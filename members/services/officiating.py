"""Referee and official eligibility of a member (``teams.RefereeProfile`` / ``teams.OfficialProfile``).

One profile per member and kind: the first row is *the* profile and is updated in place. Admin only; every write is
audited. Whether the club may use this at all (feature ``referee_officials``) is the API layer's concern."""

import datetime

from django.db import transaction
from django.utils import timezone

from members.models import Member
from teams.models import OfficialLevel, OfficialProfile, RefereeLevel, RefereeProfile, Team

from . import audit
from .access import require_admin
from .errors import Invalid

REFEREE, OFFICIAL = "referee", "official"
_KINDS = {REFEREE: (RefereeProfile, RefereeLevel, "referee_profiles"), OFFICIAL: (OfficialProfile, OfficialLevel, "official_profiles")}


def _ref(obj) -> dict:
    return {"id": obj.pk, "name": obj.name}


def get_profile(member: Member, kind: str):
    model, _level, related = _KINDS[kind]
    return getattr(member, related).select_related("level").order_by("created", "pk").first()


def profile_out(profile) -> dict | None:
    """The API shape of a profile (``None`` when the member has none)."""
    if profile is None:
        return None
    is_referee = isinstance(profile, RefereeProfile)
    today = timezone.localdate()
    return {
        "id": profile.pk,
        "level": _ref(profile.level) if profile.level_id else None,
        "valid_until": profile.valid_until,
        "is_valid": profile.valid_until is not None and profile.valid_until >= today,
        "is_eligible": profile.is_eligible,
        "eligible_teams": [_ref(t) for t in profile.eligible_teams.order_by("name")],
        "excluded_teams": [_ref(t) for t in profile.excluded_teams.order_by("name")] if is_referee else [],
    }


def officiating(member: Member) -> dict:
    return {"referee": profile_out(get_profile(member, REFEREE)), "official": profile_out(get_profile(member, OFFICIAL))}


def options() -> dict:
    return {
        "referee_levels": [_ref(level) for level in RefereeLevel.objects.order_by("name")],
        "official_levels": [_ref(level) for level in OfficialLevel.objects.order_by("name")],
        "teams": [_ref(team) for team in Team.objects.order_by("name")],
    }


def _level(kind: str, level_id):
    if level_id is None:
        return None
    level = _KINDS[kind][1].objects.filter(pk=level_id).first()
    if level is None:
        raise Invalid("Unknown level.", {"level_id": ["Unknown level."]})
    return level


def _teams(team_ids) -> list[Team]:
    wanted = set(team_ids)
    teams = list(Team.objects.filter(pk__in=wanted))
    if len(teams) != len(wanted):
        raise Invalid("Unknown team.", {"excluded_team_ids": ["Unknown team."]})
    return teams


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime.date) else None


@transaction.atomic
def save_profile(actor, member: Member, kind: str, *, level_id, valid_until: datetime.date | None, excluded_team_ids=()):
    """Create or update the member's profile of ``kind``; returns it. No audit entry when nothing changed."""
    require_admin(actor)
    model = _KINDS[kind][0]
    level = _level(kind, level_id)
    excluded = _teams(excluded_team_ids) if kind == REFEREE else []
    profile = get_profile(member, kind)
    created = profile is None
    if created:
        profile = model(member=member)
        before = {"level": None, "valid_until": None, "excluded_teams": []}
    else:
        before = {"level": profile.level.name if profile.level_id else None, "valid_until": _iso(profile.valid_until), "excluded_teams": sorted(t.name for t in profile.excluded_teams.all()) if kind == REFEREE else []}
    profile.level, profile.valid_until = level, valid_until
    profile.save()
    if kind == REFEREE:
        profile.excluded_teams.set(excluded)
    after = {"level": level.name if level else None, "valid_until": _iso(valid_until), "excluded_teams": sorted(t.name for t in excluded)}
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k] and (kind == REFEREE or k != "excluded_teams")}
    if created or changes:
        audit.record(actor, f"member.{kind}_updated", member=member, target=profile, changes=changes, target_label=f"{member} ({kind})")
    return get_profile(member, kind)


@transaction.atomic
def remove_profile(actor, member: Member, kind: str) -> None:
    """Delete the member's profile of ``kind`` (no-op, no audit entry, when there is none)."""
    require_admin(actor)
    profile = get_profile(member, kind)
    if profile is None:
        return
    label = f"{member} ({kind})"
    changes = {"level": {"from": profile.level.name if profile.level_id else None, "to": None}, "valid_until": {"from": _iso(profile.valid_until), "to": None}}
    pk = profile.pk
    profile.delete()
    entry = audit.record(actor, f"member.{kind}_removed", member=member, changes=changes)
    entry.target_type, entry.target_id, entry.target_label = f"{kind}profile", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])
