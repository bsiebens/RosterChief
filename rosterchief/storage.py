"""Private file storage -- for uploads that must never be reachable by a guessable
URL, unlike everything in MEDIA_ROOT (club logos, sponsor logos, team photos, news
photos), which is deliberately public and served straight off disk by Caddy in
production (see deploy/caddy/Caddyfile's `/media/*` block -- it reads from the same
shared volume as Django's own MEDIA_ROOT, so anything placed there is public
regardless of what a Django view's own permission check says).

`PRIVATE_MEDIA_ROOT` is a completely separate directory, on a separate Docker volume
(see compose.yaml) that only the `web` container mounts -- Caddy never sees it. The
only way to read a file stored here is through an authenticated Django view that
streams it explicitly (see management.views.MemberRequirementDocumentView, the one
current use: MemberRequirementStatus.document, e.g. an uploaded medical certificate).

Deliberately local disk only, not S3, regardless of AWS_STORAGE_BUCKET_NAME -- unlike
the default storage's public/private split (which follows whether a bucket is
configured), this one is a fixed choice: local disk today, revisit if/when
multi-server deployment needs it (see DEPLOYMENT.md's own local-disk caveat for
MEDIA_ROOT -- the same one applies here until then).
"""

from django.conf import settings
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


private_storage = PrivateStorage(location=str(settings.PRIVATE_MEDIA_ROOT))
