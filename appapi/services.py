"""Read models for the app API's account and context endpoints. Rules come from the same
services the web uses (club.services.access, mobile.access, members.services.family) --
nothing here re-decides who may see what."""

from django.conf import settings
from waffle import get_waffle_flag_model

from club.models import Club, ClubMembership, ClubRole, EvaluationManager, ShopManager
from club.services.access import is_platform_superuser, teams_staffed_by
from members.models import FamilyMembership, Member
from members.services.family import managed_people
from mobile.access import has_club_tie
from teams.models import StaffAssignment

#: The waffle flags a client needs to know about, by the name the API reports them under.
FEATURE_FLAGS = {"shop": "shop", "forms": "formbuilder", "officials": "officials", "evaluations": "evaluations"}


def club_features(club: Club) -> dict:
    flags = {flag.name: flag for flag in get_waffle_flag_model().objects.filter(name__in=FEATURE_FLAGS.values())}

    def on(flag_name):
        flag = flags.get(flag_name)
        return flag is not None and flag.is_active_for_club(club)

    features = {api_name: on(flag_name) for api_name, flag_name in FEATURE_FLAGS.items()}
    features["shop_open"] = features["shop"] and club.shop_open
    return features


def club_host(request, club: Club) -> str:
    """``<slug>.<base domain>`` with the request's own port (dev servers), or, with no base
    domain configured, the slug in front of the request's host."""
    host, _colon, port = request.get_host().partition(":")
    base = settings.ROSTERCHIEF_BASE_DOMAIN or host
    # The port the client used (in the Host header), not the one the server listens on.
    suffix = f":{port}" if port and port not in ("80", "443") else ""
    return f"{club.slug}.{base}{suffix}"


def club_api_base_url(request, club: Club) -> str:
    return f"{request.scheme}://{club_host(request, club)}/api/app/v1"


def logo_url(request, club: Club) -> str | None:
    if not club.logo:
        return None
    return request.build_absolute_uri(club.logo.url)


def _candidate_club_ids(member: Member) -> set:
    """Clubs the member has *some* recorded link to. has_club_tie then decides; this only
    keeps a platform superuser (tied to every club) from getting the whole platform."""
    ids = set(ClubMembership.objects.filter(member=member).values_list("club_id", flat=True))
    ids |= set(FamilyMembership.objects.filter(member=member).values_list("family__club_id", flat=True))
    ids |= set(ClubRole.objects.filter(member=member).values_list("club_id", flat=True))
    ids |= set(ShopManager.objects.filter(member=member).values_list("club_id", flat=True))
    ids |= set(EvaluationManager.objects.filter(member=member).values_list("club_id", flat=True))
    ids |= set(StaffAssignment.objects.filter(member=member).values_list("team__club_id", flat=True))
    return ids


def relationship(user, member: Member, club: Club) -> list[str]:
    kinds = []
    live = ClubMembership.objects.filter(club=club, member=member).exclude(status=ClubMembership.StatusChoices.CANCELLED)
    if live.filter(kind=ClubMembership.Kind.MEMBER).exists():
        kinds.append("member")
    if len(managed_people(member, club)) > 1 or live.filter(kind=ClubMembership.Kind.GUARDIAN).exists():
        kinds.append("guardian")
    if teams_staffed_by(user, club).exists():
        kinds.append("coach")
    # Board-level roles only: a coach already says "coach", and /context's
    # permissions.has_management_access covers "may open management" for both.
    if is_platform_superuser(user) or ClubRole.objects.filter(club=club, member=member, role__in=(ClubRole.Roles.ADMIN, ClubRole.Roles.EDITOR, ClubRole.Roles.MEMBER_ADMIN)).exists():
        kinds.append("staff")
    return kinds


def clubs_for(user) -> list[Club]:
    member = Member.objects.filter(user=user).first()
    if member is None:
        return []
    clubs = Club.objects.active().filter(pk__in=_candidate_club_ids(member)).order_by("name")
    return [club for club in clubs if has_club_tie(user, club)]
