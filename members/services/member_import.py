"""Creating the members of a validated spreadsheet import (management.bulk_import).

The web confirm step (management.views.MemberImportConfirmView) and the API job
(api.panel.routers.members_import) both call ``import_members``; parsing and validating the rows
stays in management.bulk_import, which runs identically for a preview and for the real thing.
"""

import uuid
from collections.abc import Callable, Mapping

from django.db import transaction
from django.utils import timezone

from club.models import ClubMembership
from club.services.access import current_season
from members.models import Family, FamilyMembership
from members.services.family import attach_to_family, families_of_club, get_or_create_login_user
from members.services.invites import account_exists

#: Prefix of the per-family-group "create new / link existing" choice (see bulk_import.family_choice_field_name).
FAMILY_CHOICE_PREFIX = "family_choice__"


def family_choices_from_post(post) -> dict:
    """The chooser values a browser submitted: ``{family_group: "new" | family pk}``."""
    return {key[len(FAMILY_CHOICE_PREFIX) :]: value for key, value in post.items() if key.startswith(FAMILY_CHOICE_PREFIX)}


def _chosen_family(club, choice):
    """An existing family of ``club`` for a chosen pk; None for "new", an unknown pk or another club's family
    (a choice is client input, so it is re-scoped to the club rather than trusted)."""
    if not choice or choice == "new":
        return None
    try:
        pk = uuid.UUID(str(choice))
    except ValueError:
        return None
    return families_of_club(club).filter(pk=pk).first()


def import_members(club, rows, *, can_set_status_and_fee: bool, family_choices: Mapping[str, str] | None = None, on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Re-validate ``rows`` (bulk_import.read_member_import_workbook output) and create every valid one, with its
    family link and current-season membership, in one transaction. Returns
    ``{"created", "skipped", "rostered", "skipped_rows": [{"line_number", "errors"}]}``; ``rostered`` is False when
    there is no current season, so the new people are created but not signed up."""
    from management.bulk_import import parse_member_import_rows

    family_choices = family_choices or {}
    results = parse_member_import_rows(rows, club, can_set_status_and_fee=can_set_status_and_fee)
    season = current_season(club)

    created = 0
    families_by_group = {}
    with transaction.atomic():
        for index, result in enumerate(results, start=1):
            member = result["member"]
            if member is not None:
                family_role = result["family_role"]
                if family_role in (FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN) and member.email and not account_exists(member.email):
                    # Give the parent/guardian a login before saving, same as registering a family by hand. An email that
                    # already has an account stays a plain contact address on this new, login-less record: an import never
                    # binds a row to someone else's existing account.
                    member.user, _unused = get_or_create_login_user(member.email)

                member.save()

                family_group = result["family_group"]
                if family_group:
                    family = families_by_group.get(family_group)
                    if family is None:
                        family = _chosen_family(club, family_choices.get(family_group, "new")) or Family.objects.create(club=club)
                        families_by_group[family_group] = family
                    attach_to_family(member, role=family_role, family=family, club=club)
                elif family_role == FamilyMembership.FamilyRole.CHILD:
                    # A child with no family_group: a family of their own makes "nobody on file yet" visible, so an admin
                    # can add a parent to it later from the family's own page.
                    attach_to_family(member, role=family_role, club=club)

                if season is not None:
                    ClubMembership.objects.create(club=club, member=member, season=season, signed_up_at=timezone.localdate(), **result["membership_kwargs"])
                created += 1
            if on_progress is not None:
                on_progress(index, len(results))

    skipped_rows = [{"line_number": result["line_number"], "errors": [str(error) for error in result["errors"]]} for result in results if result["member"] is None]
    return {"created": created, "skipped": len(results) - created, "rostered": season is not None, "skipped_rows": skipped_rows}
