"""Who hears about something that concerns a member: the member themself and, for a child, their parents/guardians.

A notification aimed at a member also goes to the guardians of every family the member is a child in (the people who run
the child's account or have none to run), marked as sent on the child's behalf ("For Alex: ..."). A child without a login is
reached through the guardians alone. Adults in a family are never copied to anybody.
"""

from __future__ import annotations

from django.utils.translation import gettext as _

from members.models import FamilyMembership, Member


def guardians_of(member: Member) -> list[Member]:
    """Guardians (parent / guardian role) with a login in the families where ``member`` is a child."""
    families = FamilyMembership.objects.filter(member=member, role=FamilyMembership.FamilyRole.CHILD).values("family_id")
    return list(
        Member.objects.filter(
            family_memberships__family_id__in=families,
            family_memberships__role__in=FamilyMembership.GUARDIAN_ROLES,
            user__isnull=False,
        )
        .exclude(pk=member.pk)
        .select_related("user")
        .distinct()
    )


def recipients(member: Member) -> list[tuple[object, Member | None]]:
    """``[(user, on_behalf_of)]``: the member's own account (None), then each guardian's account (the member). A user who
    is both is listed once, as themself."""
    out, seen = [], set()
    if member.user_id is not None:
        out.append((member.user, None))
        seen.add(member.user_id)
    for guardian in guardians_of(member):
        if guardian.user_id not in seen:
            out.append((guardian.user, member))
            seen.add(guardian.user_id)
    return out


def on_behalf_title(title: str, child: Member) -> str:
    return _("For %(name)s: %(title)s") % {"name": child.first_name, "title": title}


def deliver(member: Member, club, *, title: str, body: str = "", skip_user_id=None, **kwargs) -> list:
    """Inbox (+ push, per ``kwargs``) for ``member`` and their guardians; the guardians' copies say who it is for.
    ``skip_user_id`` leaves out the person who caused it. Returns the users told."""
    from notifications import inbox

    told = []
    for user, child in recipients(member):
        if skip_user_id is not None and user.pk == skip_user_id:
            continue
        inbox.notify(user, club, title=on_behalf_title(title, child) if child is not None else title, body=body, **kwargs)
        told.append(user)
    return told
