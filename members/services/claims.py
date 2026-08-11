"""Linking a parent to a child the club already holds.

The initial-migration path: a club arrives with a list of children and no parent
records. They're imported without logins, each into a family of their own, and
parents come forward afterwards through the public claim form. An admin matches
each claim against a real child and approves it, which is when the account is
created and the family link made.

Why an admin decides: see members.models.ParentClaim. The short version is that
the club is the only party that actually knows its families, and the public form
must never confirm whether a given child exists -- so it takes free text and
matches nothing itself.
"""

from allauth.account.forms import default_token_generator
from allauth.account.utils import user_pk_to_url_str
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from club.models import ClubMembership
from members.models import Family, FamilyMembership, Member, ParentClaim
from members.services.family import add_parent_to_family

User = get_user_model()

#: Suggestions are ranked, never auto-applied -- an exact name-and-birthday match
#: is still only a suggestion, because the whole point of the queue is that a
#: human confirms it.
GUARDIAN_ROLES = (FamilyMembership.FamilyRole.PARENT, FamilyMembership.FamilyRole.GUARDIAN)


def families_awaiting_a_parent(club):
    """Families in ``club`` that have children on them but nobody responsible.

    That shape *is* the state -- there's no "unclaimed" flag to drift out of step
    with reality. A child imported on their own gets a family of one (see
    management/bulk_import.py), and the moment a claim is approved a parent joins
    it, so the family drops out of here by itself.
    """
    # Exists subqueries rather than annotate(Count(..., filter=...)): the club
    # filter and the counts would otherwise share one join, so a parent with no
    # ClubMembership of their own -- which is exactly what a newly linked
    # guardian is before the season row lands -- wouldn't be counted, and the
    # family would look unclaimed forever.
    child_in_this_club = FamilyMembership.objects.filter(family=OuterRef("pk"), role=FamilyMembership.FamilyRole.CHILD, member__member_of__club=club)
    somebody_responsible = FamilyMembership.objects.filter(family=OuterRef("pk"), role__in=GUARDIAN_ROLES)

    return Family.objects.filter(Exists(child_in_this_club)).filter(~Exists(somebody_responsible))


def children_awaiting_a_parent(club):
    """The children on those families -- the admin's worklist, and the set a
    claim may be matched against."""
    return Member.objects.filter(
        family_memberships__role=FamilyMembership.FamilyRole.CHILD,
        family_memberships__family__in=families_awaiting_a_parent(club),
        member_of__club=club,
        member_of__kind=ClubMembership.Kind.MEMBER,
    ).distinct()


def submit_claim(club, *, parent_first_name, parent_last_name, parent_email, child_first_name, child_last_name, child_date_of_birth):
    """Record a claim from the public form. Deliberately does not check whether
    the child exists: the form is public, so telling the submitter either way
    would turn it into a way to enumerate the club's children. Everything is
    judged by a human afterwards."""
    return ParentClaim.objects.create(
        club=club,
        parent_first_name=parent_first_name.strip(),
        parent_last_name=parent_last_name.strip(),
        parent_email=parent_email.strip().lower(),
        child_first_name=child_first_name.strip(),
        child_last_name=child_last_name.strip(),
        child_date_of_birth=child_date_of_birth,
    )


def suggested_children(claim):
    """Children the claim plausibly refers to, best first.

    Only ever a shortlist for the admin to choose from. Ordered by how much of
    the claim matches, but an exact hit on both name and birthday still has to be
    confirmed -- a birthday is not a secret, and the queue exists precisely so
    that guessing one isn't enough.
    """
    candidates = children_awaiting_a_parent(claim.club).filter(Q(last_name__iexact=claim.child_last_name) | Q(date_of_birth=claim.child_date_of_birth))

    def score(child):
        return (
            child.last_name.lower() == claim.child_last_name.lower(),
            child.date_of_birth == claim.child_date_of_birth,
            child.first_name.lower() == claim.child_first_name.lower(),
        )

    return sorted(candidates, key=lambda child: sum(score(child)), reverse=True)


class ClaimError(Exception):
    """A claim could not be approved."""


@transaction.atomic
def approve_claim(claim, *, child, season, reviewed_by=None):
    """Link the claim's parent to ``child`` and close the claim.

    The parent lands as a *guardian* (club.models.ClubMembership.Kind): they get
    the login and the family link, but they aren't a member and owe no fee. If
    they also play, an admin flips that on their membership afterwards --
    approving a claim is not the place to decide it.
    """
    if not claim.is_pending:
        raise ClaimError("This claim has already been dealt with.")

    family = child.family_memberships.first()
    if family is None:
        raise ClaimError("That child is not in a family, so there is nothing to join.")

    add_parent_to_family(
        claim.club,
        season,
        family.family,
        email=claim.parent_email,
        first_name=claim.parent_first_name,
        last_name=claim.parent_last_name,
    )

    claim.status = ParentClaim.Status.APPROVED
    claim.child = child
    claim.reviewed_by = reviewed_by
    claim.reviewed_at = timezone.now()
    claim.save(update_fields=["status", "child", "reviewed_by", "reviewed_at"])
    return claim


def reject_claim(claim, *, reviewed_by=None, note=""):
    if not claim.is_pending:
        raise ClaimError("This claim has already been dealt with.")

    claim.status = ParentClaim.Status.REJECTED
    claim.reviewed_by = reviewed_by
    claim.reviewed_at = timezone.now()
    claim.note = note
    claim.save(update_fields=["status", "reviewed_by", "reviewed_at", "note"])
    return claim


def send_claim_approved_email(claim, *, child, request=None):
    """Tell the parent their account is ready, with a link that sets their password.

    A real one-time link rather than "go to the reset page and type your email":
    the account was created for them with no usable password, so being told to
    "reset" something they never had reads as an error. Built with allauth's own
    token generator so it lands in the same flow the login page would send them
    to, rather than a second, parallel one that could drift out of step with it.

    Never fatal: an approved claim is a real link in the database whether or not
    the mail leaves the building, and losing that link because a mail server was
    briefly unreachable would be far worse than a parent needing a nudge.
    """
    user = User.objects.filter(email__iexact=claim.parent_email).first()
    if user is None:
        return False

    path = reverse("account_reset_password_from_key", kwargs={"uidb36": user_pk_to_url_str(user), "key": default_token_generator.make_token(user)})
    set_password_url = request.build_absolute_uri(path) if request is not None else path

    # "request" rides along in the context (not used by the .txt template, only
    # by the .html one's {% absolute_media_url %} tag) so the club's logo -- if
    # it has one -- renders as an absolute URL an inbox can actually fetch, the
    # same way set_password_url above is made absolute.
    context = {"club": claim.club, "child": child, "parent_first_name": claim.parent_first_name, "set_password_url": set_password_url, "request": request}
    subject = " ".join(render_to_string("members/email/claim_approved_subject.txt", context).split())
    text_body = render_to_string("members/email/claim_approved.txt", context).strip() + "\n"
    html_body = render_to_string("members/email/claim_approved.html", context)

    message = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, [claim.parent_email])
    message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except OSError:
        # Anything the mail backend raises for an unreachable server or a refused
        # connection. The link stands; the club can resend from the queue.
        return False
    return True
