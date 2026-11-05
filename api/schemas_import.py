"""Schemas for the member import (``/api/v1/members/import``)."""

import uuid

from ninja import Schema


class ImportRowIn(Schema):
    line: int = 0
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    emergency_phone: str = ""
    date_of_birth: str = ""
    language: str = ""
    license_number: str = ""
    team: str = ""
    family: str = ""
    family_role: str = ""


class ImportRowOut(ImportRowIn):
    status: str  # new | duplicate | error (preview); created | duplicate | error (commit)
    errors: dict[str, list[str]] = {}
    note: str = ""


class ImportCounts(Schema):
    new: int = 0
    duplicate: int = 0
    error: int = 0


class ImportPreviewOut(Schema):
    season_id: uuid.UUID | None = None
    counts: ImportCounts
    rows: list[ImportRowOut]


class ImportCommitIn(Schema):
    season_id: uuid.UUID | None = None
    rows: list[ImportRowIn]


class ImportCommitCounts(Schema):
    created: int = 0
    duplicate: int = 0
    error: int = 0


class ImportCommitOut(Schema):
    counts: ImportCommitCounts
    rows: list[ImportRowOut]


class ImportJobOut(Schema):
    id: uuid.UUID
    status: str  # queued | running | done | failed
    total: int
    processed: int
    error: str = ""
    result: ImportCommitOut | None = None  # when done
