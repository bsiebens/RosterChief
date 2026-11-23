"""Create, edit, delete and grant logins to members. Admin-only; every write is audited."""

import uuid

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import ProtectedError
from django_tenants.utils import schema_context

from club import services as club_services
from club.models import Club
from members.models import FeePayment, Member

from . import audit
from .access import require_admin
from .errors import Invalid

EDITABLE_FIELDS = ("first_name", "last_name", "email", "phone", "emergency_phone", "photo_public_consent", "date_of_birth", "language", "license_number", "club_staff")


MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


def _check(values: dict) -> None:
    unknown = set(values) - set(EDITABLE_FIELDS)
    if unknown:
        raise Invalid(f"Unknown or read-only fields: {sorted(unknown)}", {f: ["Not editable."] for f in unknown})


def _clean(member: Member) -> None:
    try:
        member.full_clean(exclude=["user"])
    except ValidationError as e:
        raise Invalid("Invalid member.", e.message_dict) from e


@transaction.atomic
def create_member(actor, club: Club, *, first_name: str, last_name: str, email: str = "", features=None, **fields) -> Member:
    """Add a person to the club roster (no login). Enforces the club's ``max_members`` limit
    (``features.entitlements.LimitExceeded`` propagates). Extra ``fields`` must be in ``EDITABLE_FIELDS``."""
    require_admin(actor)
    _check(fields)
    if not first_name.strip() or not last_name.strip():
        raise Invalid("First and last name are required.", {"first_name": ["Required."]} if not first_name.strip() else {"last_name": ["Required."]})
    member = club_services.create_member(club, first_name.strip(), last_name.strip(), email, features=features)
    with schema_context(club.schema_name):
        for name, value in fields.items():
            setattr(member, name, value)
        _clean(member)
        if fields:
            member.save()
        audit.record(actor, "member.created", member=member, target=member, changes=audit.snapshot(member, ["first_name", "last_name", "email", *fields]))
    return member


@transaction.atomic
def update_member(actor, member: Member, **values) -> Member:
    """Apply ``values`` (fields in ``EDITABLE_FIELDS``) and audit the diff. No entry if nothing changed."""
    require_admin(actor)
    _check(values)
    changes = audit.diff(member, values)
    for name, value in values.items():
        setattr(member, name, value)
    _clean(member)
    if changes:
        member.save()
        audit.record(actor, "member.updated", member=member, target=member, changes=changes)
    return member


SELF_EDITABLE_FIELDS = ("first_name", "last_name", "phone", "emergency_phone", "date_of_birth", "photo_public_consent")


@transaction.atomic
def update_own_profile(actor, member: Member, **values) -> Member:
    """A person (or the parent of a child) correcting contact details: only ``SELF_EDITABLE_FIELDS`` -- never the email (it is the
    login), the licence or the language. The caller has checked that ``actor`` is this member or manages them. Audited like ``update_member``."""
    unknown = set(values) - set(SELF_EDITABLE_FIELDS)
    if unknown:
        raise Invalid(f"Unknown or read-only fields: {sorted(unknown)}", {f: ["Not editable."] for f in unknown})
    for name in ("first_name", "last_name"):
        if name in values and not str(values[name]).strip():
            raise Invalid("A name is required.", {name: ["Required."]})
    changes = audit.diff(member, values)
    for name, value in values.items():
        setattr(member, name, value)
    _clean(member)
    if changes:
        member.save()
        audit.record(actor, "member.updated", member=member, target=member, changes=changes)
    return member


def user_names(users) -> dict:
    """``{user pk: display name}`` for ``users``: the name of the member the login belongs to, else the account's own name, else its email."""
    users = [u for u in users if u is not None]
    members = {m.user_id: str(m) for m in Member.objects.filter(user__in=users)}
    return {u.pk: members.get(u.pk) or u.get_full_name() or u.email for u in users}


@transaction.atomic
def delete_member(actor, member: Member, *, detach_login: bool = False, authorized: bool = False) -> None:
    """Delete a person and everything hanging off them (family links, memberships, team spots).

    Refused when: it is the actor's own record; the person has recorded fee payments (financial history must stay);
    the person has a login and ``detach_login`` is not set (then the row is deleted but the user account and its
    club access are left alone -- revoke access through the authorization app); or another app protects a row that
    points at them. The audit entry survives (member is nulled) and keeps the name as target label.
    ``authorized``: the caller already checked the actor's right to do this (cancelling a registration is a finance action, not a member-admin one)."""
    if not authorized:
        require_admin(actor)
    if member.user_id and member.user_id == getattr(actor, "pk", None):
        raise Invalid("You cannot delete your own member record.")
    if member.user_id and not detach_login:
        raise Invalid("This person has a login. Pass detach_login=True to delete the member and keep the account.")
    if FeePayment.objects.filter(membership__member=member).exists():
        raise Invalid("This person has recorded fee payments and cannot be deleted.")
    pk, label, had_login = member.pk, str(member), bool(member.user_id)
    from chat.services import privacy  # what the person wrote in chats stays, without their name

    privacy.anonymise_member(member)
    try:
        member.delete()
    except ProtectedError as e:
        raise Invalid("This person is still referenced by other records and cannot be deleted.") from e
    entry = audit.record(actor, "member.deleted", changes={"login_detached": {"from": had_login, "to": False}} if had_login else {})
    entry.target_type, entry.target_id, entry.target_label = "member", str(pk), label[:255]
    entry.save(update_fields=["target_type", "target_id", "target_label", "modified"])


@transaction.atomic
def grant_login(actor, club: Club, member: Member, *, role_slug: str = "member"):
    """Give ``member`` a login using ``member.email`` (see ``club.services.grant_login``). Returns
    ``(user, user_created)``; send the password setup email only when ``user_created``."""
    require_admin(actor)
    try:
        user, created = club_services.grant_login(club, member, role_slug=role_slug)
    except ValueError as e:
        raise Invalid(str(e)) from e
    audit.record(actor, "member.login_granted", member=member, target=member, changes={"user": {"from": None, "to": user.email}, "role": {"from": None, "to": role_slug}})
    return user, created


PHOTO_FIELDS = {"profile_photo": "photo", "player_photo": "player_photo"}  # field -> audit action prefix: member.<prefix>_changed


def _photo_field(field: str) -> str:
    if field not in PHOTO_FIELDS:
        raise ValueError(f"Unknown photo field: {field}")
    return PHOTO_FIELDS[field]


@transaction.atomic
def set_photo(actor, member: Member, image: UploadedFile, field: str = "profile_photo", *, authorized: bool = False) -> Member:
    """Replace the profile picture (self-service, club-wide) or the official player picture (``field="player_photo"``,
    public once ``photo_public_consent`` is set). JPEG, PNG or WebP up to 5 MB (verified by decoding, never by file
    name), stored under a name we choose. The previous file is deleted. Audited without the file content."""
    from PIL import Image, UnidentifiedImageError

    prefix = _photo_field(field)
    if not authorized:
        require_admin(actor)
    if image.size > MAX_PHOTO_BYTES:
        raise Invalid("The picture is larger than 5 MB.", {"photo": ["The picture is larger than 5 MB."]})
    try:
        with Image.open(image) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise Invalid("This is not a valid image.", {"photo": ["This is not a valid image."]}) from e
    if fmt not in PHOTO_FORMATS:
        raise Invalid("Use a JPEG, PNG or WebP picture.", {"photo": ["Use a JPEG, PNG or WebP picture."]})
    image.seek(0)
    current = getattr(member, field)
    old = current.name if current else ""
    current.save(f"{field.split('_')[0]}-{uuid.uuid4().hex[:12]}.{PHOTO_FORMATS[fmt]}", image, save=False)
    member.save(update_fields=[field, "modified"])
    if old:
        current.storage.delete(old)
    audit.record(actor, f"member.{prefix}_changed", member=member, target=member, changes={field: {"from": bool(old), "to": True}})
    return member


@transaction.atomic
def remove_photo(actor, member: Member, field: str = "profile_photo", *, authorized: bool = False) -> Member:
    """Delete the picture (no-op, no audit entry, when there is none)."""
    prefix = _photo_field(field)
    if not authorized:
        require_admin(actor)
    current = getattr(member, field)
    if not current:
        return member
    old = current.name
    setattr(member, field, "")
    member.save(update_fields=[field, "modified"])
    current.storage.delete(old)
    audit.record(actor, f"member.{prefix}_removed", member=member, target=member, changes={field: {"from": True, "to": False}})
    return member


def set_profile_photo(actor, member: Member, image: UploadedFile) -> Member:
    return set_photo(actor, member, image, "profile_photo")


def remove_profile_photo(actor, member: Member) -> Member:
    return remove_photo(actor, member, "profile_photo")
