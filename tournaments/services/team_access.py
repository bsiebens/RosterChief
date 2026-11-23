"""The password that goes with a team's private link. It is generated when the team applies (or when the organisers first send the link),
emailed to the contact person and stored hashed; sending the link again makes a new one, which also ends every session opened with the old
one. A correct password gives the browser a signed, time-limited access token that the team page sends along with every call."""

import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.core import signing

from ..models import TournamentRegistration

#: No lookalikes (0/o, 1/l/i): the password is read out of an email and typed.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
SALT = "tournament-team-access"
MAX_AGE = 14 * 24 * 3600


def new_password() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def reset_password(registration: TournamentRegistration) -> str:
    """Give the registration a fresh password; returns it in the clear, the only time it exists in the clear."""
    plain = new_password()
    registration.access_password = make_password(plain)
    registration.save(update_fields=["access_password"])
    return plain


def is_locked(registration: TournamentRegistration) -> bool:
    return bool(registration.access_password)


def password_ok(registration: TournamentRegistration, plain: str) -> bool:
    return bool(registration.access_password) and check_password(plain.strip().lower(), registration.access_password)


def _fingerprint(registration: TournamentRegistration) -> str:
    return registration.access_password[-12:]


def make_access(registration: TournamentRegistration) -> str:
    return signing.dumps({"r": str(registration.pk), "f": _fingerprint(registration)}, salt=SALT)


def access_ok(registration: TournamentRegistration, token: str | None) -> bool:
    """Open when the registration has no password (older applications), else the token has to be a fresh one for this registration and password."""
    if not registration.access_password:
        return True
    if not token:
        return False
    try:
        data = signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return False
    return data.get("r") == str(registration.pk) and data.get("f") == _fingerprint(registration)
