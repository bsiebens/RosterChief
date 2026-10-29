"""Schemas shared by every router (protected and panel)."""

from ninja import Field, Schema


class ErrorDetail(Schema):
    code: str = Field(description="Stable machine-readable code, e.g. `not_a_member`. Never translated.")
    message: str = Field(description="Localised (Accept-Language), safe to show to the user.")
    fields: dict[str, list[str]] | None = Field(None, description="Validation messages per field; only on 422.")


class ErrorOut(Schema):
    error: ErrorDetail


class JobOut(Schema):
    id: str = Field(description="Poll `GET /manage/jobs/{id}` until `status` is `succeeded` or `failed`.")
    status: str = Field(description="`pending`, `running`, `succeeded` or `failed`.")
    progress: int | None = Field(None, description="0-100 when the task reports it.")
    result: dict | list | None = Field(None, description="The task's JSON result; only when `succeeded`.")
    error: dict | None = Field(None, description="`{code, message}`; only when `failed`.")
