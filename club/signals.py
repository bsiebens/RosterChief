"""Keep ClubRole in sync with membership status.

An **active** ClubMembership grants the member a ``MEMBER`` ClubRole; when no
active membership remains in that club (status changed away from active, or the
membership was deleted), the ``MEMBER`` role is withdrawn.

A member holds at most one ClubRole per club (``unique_member_per_club``), so an
elevated role (ADMIN / EDITOR) is never downgraded or removed by this sync — it
simply takes precedence.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import ClubMembership, ClubRole


@receiver(post_save, sender=ClubMembership)
@receiver(post_delete, sender=ClubMembership)
def sync_member_role(sender, instance, **kwargs):
    has_active = ClubMembership.objects.filter(
        club_id=instance.club_id,
        member_id=instance.member_id,
        status=ClubMembership.StatusChoices.ACTIVE,
    ).exists()

    if has_active:
        # get_or_create keeps an existing ADMIN/EDITOR role untouched.
        ClubRole.objects.get_or_create(
            club_id=instance.club_id,
            member_id=instance.member_id,
            defaults={"role": ClubRole.Roles.MEMBER},
        )
    else:
        ClubRole.objects.filter(
            club_id=instance.club_id,
            member_id=instance.member_id,
            role=ClubRole.Roles.MEMBER,
        ).delete()
