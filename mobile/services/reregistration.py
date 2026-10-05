"""Re-registering the account's managed people for a season -- the steps behind the
member web app's Register screen (mobile.views.ReRegisterView) and the app API's
/registration endpoints. Both run the same RegistrationEntryFormSet validation, the same
pricing and the same registration.services.submit_registration as the public page.
"""

from registration.forms import RegistrationEntryFormSet, entries_from_formset
from registration.services import price_entries, resolve_registration_season, submit_registration, team_number_pools
from teams.models import Team
from teams.services.numbers import member_current_number

#: The formset prefix both the web form and the API's generated data use.
PREFIX = "entries"


def member_current_numbers(club, people, season) -> dict:
    """``{str(member_id): {str(team_id): number}}``: each managed person's current number in
    every team with a number pool, so "keep #N" can be offered (and validates)."""
    pools_by_team = {team.pk: team.pool for team in Team.objects.filter(club=club, pool__isnull=False).select_related("pool")}
    result = {}
    for person in people:
        person_numbers = {}
        for team_id, pool in pools_by_team.items():
            current = member_current_number(person, pool, season)
            if current is not None:
                person_numbers[str(team_id)] = current
        if person_numbers:
            result[str(person.pk)] = person_numbers
    return result


def entry_formset(club, people, season, data=None, initial=None):
    """The entry formset scoped to ``people`` and ``season``. ``enforce_single_contact`` is
    off: here ``is_contact`` is each person's "Include this person" toggle, and including
    yourself and a child together is the normal case."""
    kwargs = {
        "club": club,
        "people": people,
        "season": season,
        "team_number_pools": team_number_pools(club, season),
        "member_current_numbers": member_current_numbers(club, people, season),
        "prefix": PREFIX,
        "enforce_single_contact": False,
    }
    if data is None and initial is not None:
        kwargs["initial"] = initial
    return RegistrationEntryFormSet(data, **kwargs)


def price_formset(formset) -> tuple[list, list]:
    """``(entries, priced)`` for a valid formset. Raises PricingError when the chosen
    products don't share one season."""
    entries = entries_from_formset(formset)
    resolve_registration_season([entry.product_variant for entry in entries])
    return entries, price_entries([entry.product_variant for entry in entries])


def submit_reregistration(club, user, me, entries):
    """Submit as the signed-in account (the contact is its own person). Raises
    RegistrationError."""
    contact_member = me or (entries[0].existing_member if entries and entries[0].existing_member else None)
    return submit_registration(
        club,
        submitted_by_user=user,
        entries=entries,
        contact_first_name=contact_member.first_name if contact_member else "",
        contact_last_name=contact_member.last_name if contact_member else "",
        contact_email=contact_member.contact_email if contact_member else "",
        contact_phone="",
    )
