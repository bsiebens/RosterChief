"""Private file storage -- for uploads that must never be reachable by a guessable
URL, unlike everything in MEDIA_ROOT (club logos, sponsor logos, team photos, news
photos), which is deliberately public and served straight off disk by Caddy in
production (see deploy/caddy/Caddyfile's `/media/*` block -- it reads from the same
shared volume as Django's own MEDIA_ROOT, so anything placed there is public
regardless of what a Django view's own permission check says).

`PRIVATE_MEDIA_ROOT` is a completely separate directory, on a separate Docker volume
(see compose.yaml) that only the `web` container mounts -- Caddy never sees it. The
only way to read a file stored here is through an authenticated Django view that
streams it explicitly: management.views.MemberRequirementDocumentView for
MemberRequirementStatus.document (e.g. an uploaded medical certificate),
members.views.MemberPhotoView for Member.photo, and the authorised invoice
download views for the rendered-invoice PDF cache (InvoiceCacheStorage below).

Deliberately local disk only, not S3, regardless of AWS_STORAGE_BUCKET_NAME -- unlike
the default storage's public/private split (which follows whether a bucket is
configured), this one is a fixed choice: local disk today, revisit if/when
multi-server deployment needs it (see DEPLOYMENT.md's own local-disk caveat for
MEDIA_ROOT -- the same one applies here until then).
"""

import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """FileSystemStorage with .url() permanently disabled.

    Passing base_url=None to the parent class does NOT do this -- FileSystemStorage
    treats None as "unset" and falls back to settings.MEDIA_URL, so it would happily
    hand back a `/media/...` link for a file that was never written under MEDIA_ROOT
    in the first place (wrong and broken, but not obviously so -- it looks like a
    normal URL until something tries to fetch it). Overriding .url() to always raise
    is the only way to make "this storage has no URL" fail loudly instead."""

    def url(self, name):
        raise ValueError("PrivateStorage has no URL -- read a file through an authenticated view instead, e.g. management.views.MemberRequirementDocumentView.")


class SettingsPrivateStorage(PrivateStorage):
    """PrivateStorage rooted at ``settings.PRIVATE_MEDIA_ROOT``, re-read on every
    access instead of baked in at import time -- the same approach as
    InvoiceCacheStorage below, so a test run's throwaway PRIVATE_MEDIA_ROOT (see
    rosterchief/test_runner.py) is honoured by every private upload, not only the
    invoice cache."""

    @property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)


private_storage = SettingsPrivateStorage()


def get_private_storage() -> PrivateStorage:
    """Callable wrapper for FileField's own ``storage=`` kwarg -- a callable serializes
    into a migration as just this dotted import path, evaluated fresh wherever the
    migration runs. Passing the ``private_storage`` instance directly instead would bake
    its constructed ``location`` (an absolute filesystem path) into the migration file
    verbatim: correct on whichever machine ran makemigrations, wrong everywhere else --
    a dev laptop's checkout path is not a production container's /app. This is exactly
    what happened to club/migrations/0025 and 0026 before this fix, which is why they
    still deconstruct the instance form -- only new/altered FileField declarations need
    the callable; existing migrations don't need touching for their own past state to
    stay valid, only the model (and a fresh migration) need to stop drifting from here."""
    return private_storage


class InvoiceCacheStorage(PrivateStorage):
    """Private, URL-less storage for rendered invoice PDFs (dues, shop, billing).

    A rendered invoice is personal/financial data, so its cached copy must never
    land in the default storage: that one is served publicly (Caddy's `/media/*`
    block locally, a public bucket with unsigned URLs when S3 is configured), and
    the cache key (club slug + invoice pk) is trivially guessable. Every read goes
    through the authorised download views instead.

    Unlike the module-level ``private_storage`` above, the location is resolved
    from ``settings.PRIVATE_MEDIA_ROOT`` on every access rather than baked in at
    import time, so a test run can point it at a throwaway directory (see
    rosterchief/test_runner.py). Overwrites in place: two concurrent renders of the
    same invoice produce the same bytes, and a renamed duplicate would never be read.
    """

    def __init__(self):
        super().__init__(allow_overwrite=True)

    @property
    def base_location(self):
        return os.path.join(str(settings.PRIVATE_MEDIA_ROOT), "invoice-cache")

    @property
    def location(self):
        return os.path.abspath(self.base_location)


invoice_cache_storage = InvoiceCacheStorage()


def read_cached_invoice_pdf(name: str) -> bytes | None:
    """The cached PDF stored under ``name``, or None when there is none yet."""
    if not invoice_cache_storage.exists(name):
        return None
    with invoice_cache_storage.open(name, "rb") as cached:
        return cached.read()


def write_cached_invoice_pdf(name: str, pdf_bytes: bytes) -> None:
    invoice_cache_storage.save(name, ContentFile(pdf_bytes))


def drop_cached_invoice_pdf(name: str) -> None:
    """Delete the cached PDF under ``name``; a no-op when nothing was cached."""
    if invoice_cache_storage.exists(name):
        invoice_cache_storage.delete(name)
