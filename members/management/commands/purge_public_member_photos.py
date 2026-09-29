"""Deletes every file left under ``members/`` in the default (public) storage.

Member photos moved to private storage in migration members/0012, which
deletes the public copy of each photo a Member row still points at. Earlier
uploads that were since replaced are not referenced by any row, so the
migration can't know about them -- this command removes whatever is left.
Nothing else is stored under ``members/`` in the default storage. Run it once,
after migrating; ``--dry-run`` lists the files without deleting them.
"""

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

PREFIX = "members"


def _walk(storage, path):
    directories, files = storage.listdir(path)
    for name in files:
        yield f"{path}/{name}"
    for directory in directories:
        yield from _walk(storage, f"{path}/{directory}")


class Command(BaseCommand):
    help = "Delete leftover public copies of member photos (everything under members/ in the default storage)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List the files without deleting them.")

    def handle(self, *args, **options):
        try:
            names = list(_walk(default_storage, PREFIX))
        except FileNotFoundError:
            names = []

        for name in names:
            if not options["dry_run"]:
                default_storage.delete(name)
            self.stdout.write(name)

        verb = "Would delete" if options["dry_run"] else "Deleted"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(names)} file(s)."))
