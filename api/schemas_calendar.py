"""Schemas for the calendar data (``/api/v1/locations`` and ``/api/v1/opponents``)."""

from uuid import UUID

from ninja import Schema


class CountryOut(Schema):
    code: str
    name: str


class LocationOut(Schema):
    id: UUID
    name: str
    address: str
    city: str
    zip_code: str
    country: str
    country_name: str
    is_home: bool
    latitude: float | None = None
    longitude: float | None = None
    event_count: int


class LocationIn(Schema):
    name: str
    address: str
    city: str
    zip_code: str
    country: str
    latitude: float | None = None
    longitude: float | None = None


class LocationPatch(Schema):
    name: str | None = None
    address: str | None = None
    city: str | None = None
    zip_code: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class OpponentOut(Schema):
    id: UUID
    name: str
    logo_url: str | None = None
    alternative_names: list[str]
    event_count: int


class OpponentIn(Schema):
    name: str
    alternative_names: list[str] = []


class OpponentPatch(Schema):
    name: str | None = None
    alternative_names: list[str] | None = None
