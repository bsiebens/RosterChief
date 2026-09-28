"""Bulk-attach member photos from a directory of ``<licence>.<ext>`` files.

Each file's name (without extension) is matched against ``ClubMembership.license``
in the given club, any season. A licence that matches no member, or more than one
member, is reported and skipped. A member who already has a photo is skipped
unless ``--replace`` is given. Every file is checked with Pillow before anything is
stored. ``photo_public_consent`` is left as it is unless ``--public`` is given,
which turns it on for every member whose photo this run stores -- only use it
when the club has that consent for the whole batch.

On a Docker deployment the command runs inside the ``web`` container, so mount the
directory into a one-off container (see DEPLOYMENT.md, "Bulk-importing member photos")::

    docker compose run --rm --no-deps -v "$PWD/photos:/import:ro" web \\
        python manage.py import_member_photos <club-slug> /import --dry-run
"""

from pathlib import Path

from django.core.files import File
from PIL import Image, UnidentifiedImageError

from club.models import Club, ClubMembership
from features.commands import CommandError, MaintenanceAwareCommand
from members.models import Member

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _is_valid_image(path: Path) -> bool:
    try:
        with Image.open(path) as image:
            image.verify()
    except UnidentifiedImageError, OSError, SyntaxError:
        return False
    return True


class Command(MaintenanceAwareCommand):
    help = "Attach member photos in bulk from a directory of <licence>.<ext> files (licence as on the club's memberships)."

    def add_arguments(self, parser):
        parser.add_argument("club", help="The club's slug (its subdomain).")
        parser.add_argument("directory", help="Directory holding the photos, named <licence>.png/.jpg/.jpeg/.webp/.gif.")
        parser.add_argument("--replace", action="store_true", help="Replace a photo the member already has. Without it, those members are skipped.")
        parser.add_argument(
            "--public",
            action="store_true",
            help="Also turn on 'show this photo on the public website' for every member whose photo is stored. Only when the club has that consent for all of them.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report what would happen without storing anything.")

    def handle(self, *args, **options):
        try:
            club = Club.objects.get(slug=options["club"])
        except Club.DoesNotExist as exc:
            raise CommandError(f"No club with slug {options['club']!r}.") from exc

        directory = Path(options["directory"])
        if not directory.is_dir():
            raise CommandError(f"{directory} is not a directory.")

        dry_run, replace, public = options["dry_run"], options["replace"], options["public"]
        counts = {"stored": 0, "skipped": 0, "problems": 0}

        files = sorted(path for path in directory.iterdir() if path.is_file() and not path.name.startswith("."))
        for path in files:
            outcome, message = self._import_one(club, path, dry_run=dry_run, replace=replace, public=public)
            counts[outcome] += 1
            style = {"stored": self.style.SUCCESS, "skipped": self.style.WARNING, "problems": self.style.ERROR}[outcome]
            self.stdout.write(style(f"{path.name}: {message}"))

        verb = "Would store" if dry_run else "Stored"
        summary = f"{verb} {counts['stored']} photo(s); {counts['skipped']} skipped; {counts['problems']} problem(s)."
        self.stdout.write(self.style.SUCCESS(summary) if not counts["problems"] else self.style.WARNING(summary))

    def _import_one(self, club, path: Path, *, dry_run: bool, replace: bool, public: bool):
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return "skipped", "not an image file extension"

        licence = path.stem.strip()
        member_ids = set(ClubMembership.objects.filter(club=club, license__iexact=licence).values_list("member_id", flat=True))
        if not member_ids:
            return "problems", f"no member with licence {licence!r} at {club.slug}"
        if len(member_ids) > 1:
            return "problems", f"licence {licence!r} is on {len(member_ids)} different members -- fix the memberships first"

        member = Member.objects.get(pk=member_ids.pop())
        if member.photo and not replace:
            return "skipped", f"{member} already has a photo (use --replace)"
        if not _is_valid_image(path):
            return "problems", "not a readable image"

        visibility = " (public)" if public else ""
        if dry_run:
            return "stored", f"would attach to {member}{visibility}"

        old_name = member.photo.name if member.photo else None
        with path.open("rb") as handle:
            member.photo.save(f"{licence}{path.suffix.lower()}", File(handle), save=False)
        update_fields = ["photo"]
        if public:
            member.photo_public_consent = True
            update_fields.append("photo_public_consent")
        member.save(update_fields=update_fields)
        if old_name and old_name != member.photo.name:
            member.photo.storage.delete(old_name)
        return "stored", f"attached to {member}{visibility}"
