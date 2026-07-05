from django.core.management.base import BaseCommand, CommandError

from authentication.services.member_csv_importer import MemberCsvImporter


class Command(BaseCommand):
    help = "Import club members from a CSV file."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "csv_file",
            type=str,
            help="Path to the CSV file to import.",
        )
        parser.add_argument(
            "--date-format",
            default="%Y-%m-%d",
            help="Date format for date_of_birth. Default: %%Y-%%m-%%d",
        )

    def handle(self, *args, **options) -> str | None:
        importer = MemberCsvImporter(date_format=options["date_format"])

        try:
            result = importer.import_path(options["csv_file"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        for error in result.errors:
            self.stderr.write(self.style.ERROR(f"Row {error.row_number} skipped: {error.message}"))

        self.stdout.write(
            self.style.SUCCESS(
                "Import complete. "
                f"Members created: {result.created_members}. "
                f"Members updated: {result.updated_members}. "
                f"Users created: {result.created_users}. "
                f"Clubs created: {result.created_clubs}. "
                f"Memberships created: {result.created_memberships}. "
                f"Memberships updated: {result.updated_memberships}. "
                f"Rows skipped: {result.skipped_rows}."
            )
        )
