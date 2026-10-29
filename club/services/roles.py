"""Granting and revoking club-wide roles -- shared by the management screens
(management.views.ClubRoleCreateView and the revoke views) and the management API
(api/panel/routers/roles.py), so the two cannot drift.

``ClubRole`` holds at most one row per member and club (``ADMIN`` / ``EDITOR`` / ``MEMBER_ADMIN``);
shop, referee and evaluation management are additive grants (``ShopManager``, ``RefereeManager``,
``EvaluationManager``) layered on top of whatever role the member already has.
"""

from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist

from club.models import ClubRole, EvaluationManager, RefereeManager, ShopManager
from members.models import Member

SHOP_ADMIN = "shop_admin"
REFEREE_ADMIN = "referee_admin"
EVALUATION_MANAGER = "evaluation_manager"

#: Additive grant kind -> the model holding it.
GRANT_MODELS = {SHOP_ADMIN: ShopManager, REFEREE_ADMIN: RefereeManager, EVALUATION_MANAGER: EvaluationManager}


@dataclass
class RoleGrant:
    """What ``grant_role`` did. ``kind`` is a ``ClubRole.Roles`` value or one of the additive kinds above."""

    kind: str
    member: Member
    created: bool
    role: ClubRole | None = None

    @property
    def role_label(self) -> str:
        return self.role.get_role_display() if self.role is not None else ""


def grant_role(club, member: Member, role: str) -> RoleGrant:
    """Give ``member`` ``role`` (a ``ClubRole.Roles`` value or an additive kind) in ``club``.

    Granting ADMIN/EDITOR/MEMBER_ADMIN promotes the member's existing ClubRole row (the membership-status
    sync in club/signals.py already gave any active member an implicit MEMBER role) rather than adding a
    second one, exactly like controlpanel.services.admins.grant_club_admin."""
    model = GRANT_MODELS.get(role)
    if model is not None:
        _grant, created = model.objects.get_or_create(club=club, member=member)
        return RoleGrant(kind=role, member=member, created=created)

    role_obj, created = ClubRole.objects.get_or_create(club=club, member=member, defaults={"role": role})
    if not created and role_obj.role != role:
        role_obj.role = role
        role_obj.save(update_fields=["role"])
        created = True  # the member did not hold this role before
    return RoleGrant(kind=role, member=member, created=created, role=role_obj)


def revoke_role(club, pk) -> tuple[Member, str]:
    """Delete one ClubRole row of ``club``; returns ``(member, role label)``. ``ObjectDoesNotExist`` for another club's row."""
    role = ClubRole.objects.select_related("member").filter(club=club, pk=pk).first()
    if role is None:
        raise ObjectDoesNotExist
    member, label = role.member, role.get_role_display()
    role.delete()
    return member, label


def revoke_grant(club, kind: str, pk) -> Member:
    """Delete one additive grant (``SHOP_ADMIN`` / ``REFEREE_ADMIN`` / ``EVALUATION_MANAGER``) of ``club``; returns the member.
    ``ObjectDoesNotExist`` for another club's row."""
    grant = GRANT_MODELS[kind].objects.select_related("member").filter(club=club, pk=pk).first()
    if grant is None:
        raise ObjectDoesNotExist
    member = grant.member
    grant.delete()
    return member
