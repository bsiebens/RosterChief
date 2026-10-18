"""Everything platform staff need to debug one account, minus personal data.

Deliberately left out: phone numbers, dates of birth/ages, pictures, addresses, push tokens and
other credentials. Names and the account email are shown (they are how an account is found and
recognised). Built from plain querysets across apps, grouped per club, for
``controlpanel.views.UserDebugView``; read-only.
"""

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator

from appapi.models import PushDevice
from club.models import ClubMembership, ClubRole, ShopManager
from members.models import FamilyMembership
from notifications.models import NotificationCategory, NotificationDelivery, NotificationPreference, QuietHours
from notifications.quiet_hours import window_end
from registration.models import RegistrationDetails
from teams.models import StaffAssignment, TeamMembership

RECENT_DELIVERIES = 15


def account_info(user) -> dict:
    addresses = list(EmailAddress.objects.filter(user=user).order_by("-primary", "email"))
    return {
        "email_addresses": addresses,
        "authenticators": sorted(Authenticator.objects.filter(user=user).values_list("type", flat=True)),
    }


def devices(user) -> list:
    """Native installs; the push token itself is never shown."""
    return list(PushDevice.objects.filter(user=user).only("installation_id", "platform", "environment", "app_version", "locale", "enabled", "last_seen", "user_id"))


def club_sections(member) -> list:
    """One dict per club the member is attached to (by membership, role, roster, staff spot or family)."""
    if member is None:
        return []
    sections = {}

    def section(club):
        return sections.setdefault(club.pk, {"club": club, "roles": [], "shop_manager": False, "memberships": [], "teams": [], "staff": [], "families": []})

    for role in ClubRole.objects.filter(member=member).select_related("club"):
        section(role.club)["roles"].append(role)
    for grant in ShopManager.objects.filter(member=member).select_related("club"):
        section(grant.club)["shop_manager"] = True

    memberships = list(ClubMembership.objects.filter(member=member).select_related("club", "season").order_by("club__name", "-season__start_date"))
    details = {}
    for entry in RegistrationDetails.objects.filter(membership__in=memberships).select_related("requested_team", "batch"):
        details.setdefault(entry.membership_id, []).append(entry)
    for membership in memberships:
        membership.registration_entries = details.get(membership.pk, [])
        section(membership.club)["memberships"].append(membership)

    for place in TeamMembership.objects.filter(member=member).select_related("team__club", "season", "position").order_by("-season__start_date"):
        section(place.team.club)["teams"].append(place)
    for spot in StaffAssignment.objects.filter(member=member).select_related("team__club", "season", "position").order_by("-season__start_date"):
        section(spot.team.club)["staff"].append(spot)

    # The household: who else is in each family and in what role (names only).
    for link in FamilyMembership.objects.filter(member=member).select_related("family__club"):
        others = FamilyMembership.objects.filter(family=link.family).exclude(member=member).select_related("member").order_by("role", "member__last_name")
        section(link.family.club)["families"].append({"family": link.family, "role": link, "others": list(others)})

    return sorted(sections.values(), key=lambda item: item["club"].name.lower())


def notification_section(user) -> dict:
    """Saved switches per scope (no stored row = on) plus quiet hours and the latest sends."""
    rows = NotificationPreference.objects.filter(user=user).select_related("club", "member").order_by("club__name", "member__last_name", "member__first_name")
    by_scope = {}
    for row in rows:
        by_scope.setdefault((row.club, row.member), []).append(row)
    scopes = []
    for (club, member), scope_rows in sorted(by_scope.items(), key=lambda item: (item[0][0] is not None, item[0][0].name if item[0][0] else "", item[0][1] is not None)):
        stored = {(row.category, row.channel): row for row in scope_rows}
        lines = [
            {"label": label, "push": stored.get((value, NotificationPreference.Channel.PUSH)), "email": stored.get((value, NotificationPreference.Channel.EMAIL))}
            for value, label in NotificationCategory.choices
            if (value, NotificationPreference.Channel.PUSH) in stored or (value, NotificationPreference.Channel.EMAIL) in stored
        ]
        scopes.append({"club": club, "member": member, "lines": lines})
    hours = QuietHours.objects.filter(user=user).first()
    quiet = {"hours": hours, "active_until": window_end(hours), "is_window": hours.start != hours.end} if hours is not None else None
    # Delivery targets (email address / install id) are left out; the outcome is what matters.
    deliveries = list(NotificationDelivery.objects.filter(user=user).select_related("club", "notification").order_by("-created")[:RECENT_DELIVERIES])
    return {"scopes": scopes, "quiet": quiet, "deliveries": deliveries}
