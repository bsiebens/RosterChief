"""Encryption at rest for allauth.mfa secrets.

allauth.mfa stores the TOTP shared secret (``Authenticator.data["secret"]``) and
the recovery-code seed (``data["seed"]``, plus ``data["migrated_codes"]`` for
codes imported from an older setup) in the database, and routes every read and
write of them through its adapter's ``encrypt()``/``decrypt()`` hooks.
RosterChiefMFAAdapter points those hooks here.

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) under
``settings.MFA_ENCRYPTION_KEY`` and stored with a ``fernet:`` prefix. A value
without that prefix is a legacy plaintext secret written before encryption was
enabled: ``decrypt()`` returns it unchanged, so turning encryption on is safe
before the data migration (authentication/migrations/0006) has run.

MFA_ENCRYPTION_KEY may hold several comma-separated keys: the first encrypts,
all of them decrypt (MultiFernet), which allows rotating the key without
locking anyone out of their authenticator.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PREFIX = "fernet:"


def _keys() -> list[str]:
    raw = getattr(settings, "MFA_ENCRYPTION_KEY", "") or ""
    return [key.strip() for key in raw.split(",") if key.strip()]


def _fernet() -> MultiFernet:
    keys = _keys()
    if not keys:
        raise ImproperlyConfigured("MFA_ENCRYPTION_KEY is not set; it is required to store two-factor secrets.")
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except ValueError as exc:
        raise ImproperlyConfigured("MFA_ENCRYPTION_KEY must be one or more comma-separated Fernet keys (32 url-safe base64-encoded bytes each).") from exc


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(text: str) -> str:
    token = _fernet().encrypt(text.encode()).decode("ascii")
    return PREFIX + token


def decrypt(value: str) -> str:
    """The plaintext of an encrypted value; a legacy plaintext value as-is."""
    if not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX) :].encode("ascii")).decode()
    except InvalidToken as exc:
        raise ImproperlyConfigured("A stored two-factor secret could not be decrypted with MFA_ENCRYPTION_KEY (wrong or rotated-out key?).") from exc


def encrypt_authenticator_data(data: dict) -> dict:
    """``data`` with every allauth.mfa secret field encrypted; already-encrypted
    values are left alone, so this is safe to run repeatedly."""
    return _map_secret_fields(data, lambda value: value if is_encrypted(value) else encrypt(value))


def decrypt_authenticator_data(data: dict) -> dict:
    return _map_secret_fields(data, decrypt)


def _map_secret_fields(data: dict, func) -> dict:
    data = dict(data or {})
    for field in ("secret", "seed"):
        if isinstance(data.get(field), str):
            data[field] = func(data[field])
    if isinstance(data.get("migrated_codes"), list):
        data["migrated_codes"] = [func(code) if isinstance(code, str) else code for code in data["migrated_codes"]]
    return data
