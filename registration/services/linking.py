"""Linking an anonymous registration to an existing member (ported from the old ``club.services.signup_linking``).

A public registration always creates fresh members (never matched by email). When staff see that the person already exists, they merge the
sign-up into them: the pending membership (with its payments, checklist and registration entry), family links and roster spots move to the
existing member and the empty duplicate is removed. Needs ``members.manage_members``.
"""

from django.db import transaction
from django.db.models import Q

from members.models import ClubMembership, FamilyMembership, Member
from members.services import audit
from members.services.access import require_admin
from members.services.errors import Invalid
from registration.models import RegistrationBatch, RegistrationDetails
from teams.models import TeamMembership


def possible_duplicates(membership: ClubMembership) -> list[dict]:
    """People who may be the same person as the sign-up: same email, or same name (and birthday when both are known). Newest first."""
    member = membership.member
    q = Q(first_name__iexact=member.first_name, last_name__iexact=member.last_name)
    if member.email:
        q |= Q(email__iexact=member.email)
    found = Member.objects.filter(q).exclude(pk=member.pk).order_by("last_name", "first_name")
    rows = []
    for other in found:
        same_name = other.first_name.lower() == member.first_name.lower() and other.last_name.lower() == member.last_name.lower()
        if same_name and member.date_of_birth and other.date_of_birth and other.date_of_birth != member.date_of_birth and not (member.email and other.email.lower() == member.email.lower()):
            continue  # same name, different birthday: a different person
        rows.append({"member": other, "same_email": bool(member.email) and other.email.lower() == member.email.lower(), "has_membership": ClubMembership.objects.filter(member=other, season=membership.season).exists()})
    return rows


@transaction.atomic
def link_to_existing_member(actor, membership: ClubMembership, existing: Member) -> ClubMembership:
    require_admin(actor)
    duplicate = membership.member
    if existing.pk == duplicate.pk:
        raise Invalid("Choose a different person.", {"member_id": ["Choose a different person."]})
    if membership.status != ClubMembership.StatusChoices.PENDING:
        raise Invalid("Only a pending sign-up can be linked.", {"__all__": ["This sign-up is not pending."]})
    if ClubMembership.objects.filter(member=existing, season=membership.season).exists():
        raise Invalid(f"{existing} already has a membership for {membership.season}.", {"member_id": ["Already has a membership this season."]}, code="already_member")
    membership.member = existing
    membership.save(update_fields=["member", "modified"])
    RegistrationDetails.objects.filter(membership=membership).update(member=existing)
    RegistrationBatch.objects.filter(contact=duplicate).update(contact=existing)
    for link in FamilyMembership.objects.filter(member=duplicate):
        if FamilyMembership.objects.filter(family=link.family, member=existing).exists():
            link.delete()
        else:
            link.member = existing
            link.save(update_fields=["member", "modified"])
    for spot in TeamMembership.objects.filter(member=duplicate):
        if TeamMembership.objects.filter(team=spot.team, season=spot.season, member=existing).exists():
            spot.delete()
        else:
            spot.member = existing
            spot.save(update_fields=["member", "modified"])
    audit.record(actor, "membership.linked", member=existing, target=membership, changes={"member": {"from": str(duplicate), "to": str(existing)}}, target_label=f"{membership.season} - {existing}")
    duplicate.delete()
    return membership
