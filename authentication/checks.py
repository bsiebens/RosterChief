from django.core.checks import Error, register


@register()
def mfa_encryption_key_check(app_configs, **kwargs):
    """Fail `migrate`/`check` (and so a deploy) when two-factor secrets could not be
    encrypted or decrypted -- rather than at the first TOTP sign-in afterwards."""
    from .crypto import _fernet

    try:
        _fernet()
    except Exception as exc:  # ImproperlyConfigured, with the reason in its message
        return [Error(str(exc), hint="Set MFA_ENCRYPTION_KEY (see .env.production.example).", id="authentication.E001")]
    return []
