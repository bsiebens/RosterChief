"""Club archives are sensitive and available only to platform superusers."""

from uuid import UUID

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from ninja import File, Form, Router, Schema, UploadedFile

from club.models import Club
from controlpanel import audit
from controlpanel.services import club_backup

from ..auth import superuser_auth
from ..errors import ApiError

router = Router(tags=["club backups"], auth=superuser_auth)


class ReportItem(Schema):
    id: str
    label: str
    reasons: list[str]


class ReportSection(Schema):
    key: str
    name: str
    group: str
    total: int
    blocked: int
    truncated: int
    items: list[ReportItem]


class BackupReport(Schema):
    total: int
    restorable: int
    blocked: int
    club: list[str]
    sections: list[ReportSection]
    refs: dict[str, list[str]]


class BackupPreview(Schema):
    version: int
    name: str
    slug: str
    suggested_slug: str
    accounts: int
    records: int
    files: int
    warnings: list[str]
    report: BackupReport


class RestoreResult(Schema):
    id: UUID
    slug: str
    new_accounts: int
    warnings: list[str]


def _public(report):
    """The report without its ``skip`` lists (every blocked id), which only the restore itself needs."""
    return {key: value for key, value in report.items() if key != "skip"} if report else None


@router.get("/clubs/{club_id}/backup", summary="Download a club backup")
def download(request, club_id: UUID):
    club = get_object_or_404(Club.objects.exclude(schema_name="public"), pk=club_id)
    try:
        archive = club_backup.export_club(club)
    except club_backup.BackupError as error:
        raise ApiError(422, "backup_error", str(error)) from error
    try:
        audit.record(request.user, "club.backup.export", club=club, target=club)
    except Exception:
        archive.close()
        raise
    response = FileResponse(archive, as_attachment=True, filename=f"{club.slug}-rosterchief-v2.zip", content_type="application/zip")
    response["Cache-Control"] = "private, no-store"
    return response


@router.post("/club-backups/preview", response=BackupPreview, summary="Validate a v1 or v2 club backup and preview its contents")
def preview(request, archive: File[UploadedFile]):
    try:
        details = club_backup.preview(archive)
        return {**details, "suggested_slug": club_backup.suggest_restore_slug(details["slug"])}
    except club_backup.BackupError as error:
        raise ApiError(422, "backup_error", str(error)) from error


@router.post("/club-backups/restore", response={201: RestoreResult}, summary="Restore a club backup to a new tenant")
def restore(request, archive: File[UploadedFile], name: Form[str] = "", slug: Form[str] = "", partial: Form[bool] = False, exclude: Form[str] = ""):
    def record_restore(club, version):
        audit.record(request.user, "club.backup.restore", club=club, target=club, data={"version": version})

    try:
        club, new_accounts, warnings = club_backup.restore_club(archive, name=name, slug=slug, on_restore=record_restore, partial=partial, exclude=[key.strip() for key in exclude.split(",")])
    except club_backup.BackupError as error:
        fields = {error.field: [str(error)]} if error.field else None
        raise ApiError(422, "backup_error", str(error), fields, report=_public(error.report)) from error
    return 201, {"id": club.pk, "slug": club.slug, "new_accounts": new_accounts, "warnings": warnings}
