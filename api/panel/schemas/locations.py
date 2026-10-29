"""Schemas of the management locations routes (``/manage/locations``)."""

import uuid

from ninja import Field, Schema


class LocationIn(Schema):
    name: str
    address: str
    city: str
    zip_code: str
    country: str = Field(description="ISO 3166-1 alpha-2 code, e.g. `BE`.")


class LocationOut(Schema):
    id: uuid.UUID
    name: str
    address: str
    city: str
    zip_code: str
    country: str = Field(description="ISO 3166-1 alpha-2 code.")
    is_home: bool = Field(description="The club's own ground; set from the control panel, read-only here.")


class LocationListOut(Schema):
    items: list[LocationOut]
