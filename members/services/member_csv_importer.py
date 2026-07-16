import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any

from django.contrib.auth import get_user_model
from django.db import transaction

from club.models import Club, ClubMembership, Season
from club.tenancy import reset_current_club, set_current_club
from members.models import Member

REQUIRED_COLUMNS = {"first_name", "last_name", "email", "date_of_birth", "create_account", "club_name", "license_number"}
TRUE_VALUES = {"1", "true", "yes", "y", "on"}

User = get_user_model()


@dataclass
class MemberImportRowError:
    row_number: int
    message: str


@dataclass
class MemberImportResult:
    created_members: int = 0
    updated_members: int = 0
    created_users: int = 0
    created_memberships: int = 0
    updated_memberships: int = 0
    skipped_rows: int = 0
    errors: list[MemberImportRowError] = field(default_factory=list)

    @property
    def successful_rows(self):
        return self.created_members + self.updated_members


@dataclass
class ImportedMemberRowResult:
    member_created: bool
    user_created: bool
    membership_created: bool


class MemberCsvImporter:
    def __init__(self, *, date_format="%Y-%m-%d") -> None:
        self.date_format = date_format

    def import_path(self, csv_path) -> MemberImportResult:
        path = Path(csv_path)

        if not path.exists():
            raise FileNotFoundError(f"CSV file does not exist: {path}")

        with path.open(newline="", encoding="utf-8-sig") as csv_file:
            return self.import_file(csv_file)

    def import_file(self, csv_file: IO[str]) -> MemberImportResult:
        result = MemberImportResult()
        reader = csv.DictReader(csv_file)

        if reader.fieldnames is None:
            raise ValueError("CSV file is empty or missing a header row.")

        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing_columns:
            raise ValueError("CSV file is missing required columns: " + ", ".join(sorted(missing_columns)))

        for row_number, row in enumerate(reader, start=2):
            try:
                row_result = self.import_row(row)
            except Exception as exc:
                result.skipped_rows += 1
                result.errors.append(
                    MemberImportRowError(
                        row_number=row_number,
                        message=str(exc),
                    )
                )
                continue

            if row_result.member_created:
                result.created_members += 1
            else:
                result.updated_members += 1

            if row_result.user_created:
                result.created_users += 1

            if row_result.membership_created:
                result.created_memberships += 1
            else:
                result.updated_memberships += 1

        return result

    @transaction.atomic
    def import_row(self, row) -> ImportedMemberRowResult:
        first_name = self.clean_required(row, "first_name")
        last_name = self.clean_required(row, "last_name")
        email = self.clean_required(row, "email").lower()
        date_of_birth = self.parse_date(self.clean_required(row, "date_of_birth"))
        create_account = self.as_bool(row.get("create_account", ""))
        club_name = self.clean_required(row, "club_name")
        license_number = row.get("license_number", "").strip()

        user = None
        user_created = False

        if create_account:
            user, user_created = self.get_or_create_user(email)

        member, member_created = Member.objects.update_or_create(
            email=email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "date_of_birth": date_of_birth,
                "user": user,
            },
        )

        club = self.get_club(club_name)
        season = self.get_current_season(club)

        _, membership_created = ClubMembership.objects.update_or_create(
            club=club,
            member=member,
            season=season,
            defaults={
                "license": license_number,
            },
        )

        return ImportedMemberRowResult(
            member_created=member_created,
            user_created=user_created,
            membership_created=membership_created,
        )

    def get_club(self, club_name) -> Club:
        # Never get_or_create: Club.name isn't unique, so a typo'd or differently-cased
        # value would otherwise either spin up a duplicate club or raise
        # MultipleObjectsReturned against one that already exists. Matching
        # case-insensitively absorbs the harmless variety (a CSV export's casing rarely
        # matches the platform's own); an unknown club is a data problem the importer
        # must not paper over by inventing one.
        club = Club.objects.filter(name__iexact=club_name).first()
        if club is None:
            raise ValueError(f"Unknown club '{club_name}'.")

        return club

    def get_current_season(self, club) -> Season:
        # Season.get_current() is tenant-scoped, so bind the row's club as the
        # active tenant for the lookup.
        token = set_current_club(club)
        try:
            season = Season.get_current()
        finally:
            reset_current_club(token)

        if season is None:
            raise ValueError(f"No current season for club '{club.name}'.")

        return season

    def get_or_create_user(self, email) -> tuple[User, bool]:
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "is_active": True,
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        return user, created

    def clean_required(self, row, field_name) -> Any:
        value = row.get(field_name, "").strip()

        if not value:
            raise ValueError(f"{field_name} is required.")

        return value

    def parse_date(self, value) -> date:
        try:
            return datetime.strptime(value, self.date_format).date()
        except ValueError as exc:
            raise ValueError(f"Invalid date_of_birth '{value}'. Expected format: {self.date_format}.") from exc

    def as_bool(self, value) -> bool:
        return value.strip().lower() in TRUE_VALUES
