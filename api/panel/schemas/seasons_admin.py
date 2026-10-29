"""Schemas of the season write routes (api/panel/routers/seasons_admin.py); reads use ``schemas.teams.SeasonOut``."""

import datetime

from ninja import Field, Schema


class SeasonIn(Schema):
    start_date: datetime.date
    end_date: datetime.date = Field(description="After `start_date`; seasons of a club never overlap.")


class SeasonPatch(Schema):
    start_date: datetime.date | None = None
    end_date: datetime.date | None = None
