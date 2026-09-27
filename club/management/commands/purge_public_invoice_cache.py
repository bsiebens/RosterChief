"""Delete invoice PDFs that earlier releases cached in the default (public) storage.

Dues, shop and billing invoice PDFs used to be cached under
``clubs/<slug>/{dues,shop,billing}/invoices/cache/<pk>.pdf`` in the default storage,
which is served publicly (Caddy's ``/media/*`` block, or an unsigned bucket URL).
The cache now lives in ``rosterchief.storage.invoice_cache_storage``; this one-off
cleanup removes the stale public copies. Safe to run repeatedly; ``--dry-run`` only
lists what would be deleted.
"""

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

CACHE_DIRS = ("dues/invoices/cache", "shop/invoices/cache", "billing/invoices/cache")


def _listdir(path):
    try:
        return default_storage.listdir(path)
    except FileNotFoundError, NotADirectoryError:
        return [], []


def public_cached_invoice_paths():
    club_dirs, _files = _listdir("clubs")
    for slug in sorted(club_dirs):
        for cache_dir in CACHE_DIRS:
            base = f"clubs/{slug}/{cache_dir}"
            _dirs, files = _listdir(base)
            for name in sorted(files):
                if name.endswith(".pdf"):
                    yield f"{base}/{name}"


class Command(BaseCommand):
    help = "Delete invoice PDFs cached in the public default storage by earlier releases."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List the files that would be deleted without deleting them.")

    def handle(self, *args, **options):
        paths = list(public_cached_invoice_paths())
        for path in paths:
            self.stdout.write(path)
            if not options["dry_run"]:
                default_storage.delete(path)

        verb = "Would delete" if options["dry_run"] else "Deleted"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(paths)} cached invoice PDF(s) from the default storage."))
