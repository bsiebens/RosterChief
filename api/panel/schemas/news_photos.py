"""Schemas of the management news photo routes (``/manage/news/{id}/photos``)."""

from ninja import Field, Schema

from api.panel.schemas.news import NewsPhotoOut


class NewsPhotoListOut(Schema):
    items: list[NewsPhotoOut]


class FocalPointIn(Schema):
    focal_x: int = Field(description="Percent from the left of the original image; values outside 0..100 are clamped.")
    focal_y: int = Field(description="Percent from the top of the original image; values outside 0..100 are clamped.")
