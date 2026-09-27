"""Adding a parent/guardian who may already have an account.

Member is global: a person with a login belongs to themselves, not to whichever
club types their email first. So when staff add a parent by email (management.
views.FamilyAddParentView/FamilyCreateView):

- an email with no account yet is a new person -- created and linked straight
  away, as before (members.services.family.add_parent_to_family);
- an email that already has an account gets a FamilyInvite instead, emailed to
  that address. Nothing is linked until the account holder signs in and accepts
  (members.views.FamilyInviteAcceptView -> accept_family_invite).

Staff see the same confirmation either way (see the views), and pending invites
are listed on the family's page.
"""

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from club.services.access import current_season
from members.models import Family, FamilyInvite, Member
from rosterchief.mail import send_message

from .family import add_child_to_family, add_parent_to_family, register_family

User = get_user_model()


class InviteError(Exception):
    """An invite can't be accepted as asked."""


def account_exists(email) -> bool:
    return bool(email) and User.objects.filter(email__iexact=email.strip()).exists()


def add_or_invite_parent(club, season, family, *, email, first_name="", last_name="", parent_is_member=False, invited_by=None, request=None):
    """Add a parent to ``family`` by email. Returns ``(parent, invite)``:
    ``parent`` when a brand-new person was created and linked, ``invite`` when
    the email already has an account and an invite was sent instead; both
    None when that account is already part of this family."""
    email = email.strip().lower()
    if not account_exists(email):
        parent = add_parent_to_family(club, season, family, email=email, first_name=first_name, last_name=last_name, parent_is_member=parent_is_member)
        return parent, None

    if family.memberships.filter(member__user__email__iexact=email).exists():
        return None, None

    invite = FamilyInvite.objects.filter(club=club, family=family, email__iexact=email, accepted_at__isnull=True, expires_at__gt=timezone.now()).first()
    if invite is None:
        invite = FamilyInvite.objects.create(club=club, family=family, email=email, first_name=first_name, last_name=last_name, parent_is_member=parent_is_member, invited_by=invited_by)
    else:
        # Asking again renews the same link rather than piling up invites.
        invite.expires_at = timezone.now() + FamilyInvite.VALID_FOR
        invite.parent_is_member = parent_is_member
        invite.save(update_fields=["expires_at", "parent_is_member", "modified"])
    send_family_invite_email(invite, request=request)
    return None, invite


@transaction.atomic
def register_family_or_invite(club, season, *, parent_email, parent_first_name, parent_last_name, child_first_name, child_last_name, child_date_of_birth=None, parent_is_member=False, invited_by=None, request=None):
    """members.services.family.register_family, except that a parent whose
    email already has an account is invited rather than linked: the family is
    created with the child only, and the parent joins once they accept."""
    if not account_exists(parent_email):
        return register_family(
            club,
            season,
            parent_email=parent_email,
            parent_first_name=parent_first_name,
            parent_last_name=parent_last_name,
            child_first_name=child_first_name,
            child_last_name=child_last_name,
            child_date_of_birth=child_date_of_birth,
            parent_is_member=parent_is_member,
        )

    family = Family.objects.create(club=club)
    add_child_to_family(club, season, family, first_name=child_first_name, last_name=child_last_name, date_of_birth=child_date_of_birth)
    add_or_invite_parent(club, season, family, email=parent_email, first_name=parent_first_name, last_name=parent_last_name, parent_is_member=parent_is_member, invited_by=invited_by, request=request)
    return family


def pending_invites(family):
    return family.invites.filter(accepted_at__isnull=True, expires_at__gt=timezone.now()).order_by("email")


def invite_problem(invite, user):
    """Why ``user`` can't accept ``invite`` (a translatable message), or None
    when they can. The same wording is used for a token that doesn't exist, so
    the page reveals nothing about invites meant for someone else."""
    if invite is None:
        return _("This invite link isn't valid. Ask the club to send a new one.")
    if invite.accepted_at is not None:
        return _("This invite has already been used.")
    if invite.expires_at <= timezone.now():
        return _("This invite has expired. Ask the club to send a new one.")
    if (user.email or "").strip().lower() != invite.email.strip().lower():
        return _("This invite was sent to a different email address. Sign in with the account it was sent to.")
    # The address must be one this account has confirmed, not just typed in --
    # same rule as controlpanel.services.accounts.account_for_grant.
    if not EmailAddress.objects.filter(user=user, email__iexact=invite.email.strip(), verified=True).exists():
        return _("Confirm your email address first, then open this invite link again.")
    return None


@transaction.atomic
def accept_family_invite(invite, user):
    """Link ``user``'s own Member (created if the account has none yet) into
    the invite's family as a parent, and enrol them for the club's current
    season, same as a direct add. Single use: the row is locked while it is
    checked and marked accepted. Raises InviteError when invite_problem says
    no."""
    invite = FamilyInvite.objects.select_for_update().select_related("club", "family").get(pk=invite.pk)
    problem = invite_problem(invite, user)
    if problem is not None:
        raise InviteError(problem)

    member, _created = Member.objects.get_or_create(user=user, defaults={"first_name": invite.first_name, "last_name": invite.last_name})
    add_parent_to_family(invite.club, current_season(invite.club), invite.family, parent=member, parent_is_member=invite.parent_is_member)

    invite.accepted_at = timezone.now()
    invite.accepted_member = member
    invite.save(update_fields=["accepted_at", "accepted_member", "modified"])
    return invite


def send_family_invite_email(invite, *, request=None) -> bool:
    """Email the accept link to the invited address. Never fatal, same as the
    registration emails: the invite stands whether or not the mail leaves."""
    path = reverse("members:family_invite_accept", kwargs={"token": invite.token})
    accept_url = request.build_absolute_uri(path) if request is not None else path
    context = {"club": invite.club, "invite": invite, "accept_url": accept_url, "request": request}
    subject = " ".join(render_to_string("members/email/family_invite_subject.txt", context).split())
    text_body = render_to_string("members/email/family_invite.txt", context).strip() + "\n"
    html_body = render_to_string("members/email/family_invite.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [invite.email])
    message.attach_alternative(html_body, "text/html")
    try:
        return send_message(message, fail_silently=False)
    except OSError:
        return False
