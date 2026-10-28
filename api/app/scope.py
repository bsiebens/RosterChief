"""Per-request scope for the club-plane endpoints (API.md §5).

``ClubScope`` bundles the club, the account's own member and the managed people
(members.services.family.managed_people), computed once. The person scope is
explicit and stateless (§5.1): reads take ``member_id=<uuid>`` or ``all`` (the
default), writes always name one managed person, and anyone outside the managed set
is a 404 -- the same answer as a person who doesn't exist.
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

from members.models import Member
from members.services.family import managed_people

from .auth import require_club_member
from .errors import ApiError
from .services import club_features

ALL = "all"


@dataclass
class ClubScope:
    club: object
    me: Member | None
    people: list

    def people_for(self, member_id: str | None) -> list:
        """Aggregate reads: every managed person for ``None``/``all``, else just the named one."""
        if member_id in (None, "", ALL):
            return self.people
        return [self.person(member_id)]

    def person(self, member_id) -> Member:
        """Writes and per-person reads: one managed person, or 404."""
        for person in self.people:
            if str(person.pk) == str(member_id):
                return person
        raise ApiError(404, "not_found", _("No such person."))

    def require_feature(self, name: str) -> None:
        """404 ``feature_disabled`` when a licensed module is off for this club."""
        if not club_features(self.club)[name]:
            raise ApiError(404, "feature_disabled", _("This feature isn't available for this club."))

    def feature(self, name: str) -> bool:
        return club_features(self.club)[name]


def club_scope(request) -> ClubScope:
    club = require_club_member(request)
    me = Member.objects.filter(user=request.user).first()
    return ClubScope(club=club, me=me, people=managed_people(me, club))
