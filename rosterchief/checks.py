from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured

# Substrings that mark a SECRET_KEY as a template/example/build value rather than a real secret.
# Compared case-insensitively. A randomly generated 50+ character key containing one of these by
# chance is vanishingly unlikely.
PLACEHOLDER_MARKERS = ("change", "insecure", "build-only", "not-a-secret", "placeholder", "example", "your-secret", "secret-key")
MIN_SECRET_KEY_LENGTH = 50
MIN_SECRET_KEY_UNIQUE_CHARACTERS = 5


def weak_secret_key_reason(key):
    """Why `key` is unfit for production, or None if it looks like a real secret."""
    if not key:
        return "DJANGO_SECRET_KEY is empty."
    if len(key) < MIN_SECRET_KEY_LENGTH:
        return f"DJANGO_SECRET_KEY is shorter than {MIN_SECRET_KEY_LENGTH} characters."
    if len(set(key)) < MIN_SECRET_KEY_UNIQUE_CHARACTERS:
        return f"DJANGO_SECRET_KEY has fewer than {MIN_SECRET_KEY_UNIQUE_CHARACTERS} unique characters."
    lowered = key.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in lowered:
            return f"DJANGO_SECRET_KEY contains the placeholder marker {marker!r}."
    return None


@register()
def secret_key_check(app_configs, **kwargs):
    """Fail `check`/`migrate` (and so a deploy) when DEBUG is off and SECRET_KEY is a placeholder.

    A system check rather than an import-time exception in settings.py: the image build runs
    collectstatic/compilemessages with DEBUG off and a throwaway key, and those must keep working.
    Skipped under the test runner, which always runs with DEBUG off and a throwaway key.
    """
    if settings.DEBUG or getattr(settings, "TESTING", False):
        return []
    try:
        key = settings.SECRET_KEY
    except ImproperlyConfigured:  # Django refuses to even read back an empty SECRET_KEY
        key = ""
    reason = weak_secret_key_reason(key)
    if reason is None:
        return []
    return [
        Error(
            reason,
            hint='Generate a real key, e.g. python3 -c "import secrets; print(secrets.token_urlsafe(64))", and set DJANGO_SECRET_KEY (see .env.production.example).',
            id="rosterchief.E001",
        )
    ]
