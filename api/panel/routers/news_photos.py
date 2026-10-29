"""Management news photos (``/manage/news/{id}/photos``): upload, main picture, focal point, delete.

Gate is the web's ``NewsEditRequiredMixin``: ``can_edit_news`` on the item (broad while a draft,
editors and admins once published). Logic is news.services (add_news_photos, set_main_news_photo,
set_news_photo_focal_point, delete_news_photo). The photos also appear in `NewsOut.photos`.
"""

import io
import uuid

from django.utils.translation import gettext_lazy as _
from ninja import File, Router, UploadedFile
from PIL import Image

from api.core.errors import ApiError
from api.core.schemas import ErrorOut
from api.core.uploads import IMAGE_TYPES, validate_upload
from api.panel.guards import panel_club
from api.panel.schemas.news import NewsPhotoOut
from api.panel.schemas.news_photos import FocalPointIn, NewsPhotoListOut
from club.services.access import can_edit_news
from news.models import News, NewsPhoto
from news.services import add_news_photos, delete_news_photo, set_main_news_photo, set_news_photo_focal_point

router = Router(tags=["panel: news photos"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
UPLOAD_ERRORS = {413: ErrorOut, 415: ErrorOut, 422: ErrorOut}
MAX_PHOTO_BYTES = 10 * 1024 * 1024


def _item(request, news_id: uuid.UUID) -> News:
    """The club's news item, if the caller may edit it (404 unknown / other club, 403 not allowed)."""
    club = panel_club(request)
    item = News.objects.filter(club=club, pk=news_id).first()
    if item is None:
        raise ApiError(404, "not_found", _("Not found."))
    if not can_edit_news(request.user, item):
        raise ApiError(403, "forbidden", _("You don't have permission to do this."))
    return item


def _photo(item: News, photo_id: uuid.UUID) -> NewsPhoto:
    photo = NewsPhoto.objects.filter(news_item=item, pk=photo_id).first()
    if photo is None:
        raise ApiError(404, "not_found", _("Not found."))
    return photo


def _out(request, photo: NewsPhoto) -> dict:
    return {"id": photo.pk, "url": request.build_absolute_uri(photo.image.url), "is_main": photo.is_main, "focal_x": photo.focal_x, "focal_y": photo.focal_y}


def _check_image(upload: UploadedFile) -> None:
    """The declared type is the client's word; make sure the bytes really are a picture."""
    try:
        Image.open(io.BytesIO(upload.read())).verify()
    except Exception:
        raise ApiError(422, "validation_error", fields={"photos": [str(_("This file is not a valid image."))]}) from None
    finally:
        upload.seek(0)


@router.get("/manage/news/{uuid:news_id}/photos", response={200: NewsPhotoListOut, **ERRORS}, summary="Photos of a news item")
def list_photos(request, news_id: uuid.UUID):
    item = _item(request, news_id)
    return {"items": [_out(request, photo) for photo in item.photos.all()]}


@router.post("/manage/news/{uuid:news_id}/photos", response={201: NewsPhotoListOut, **ERRORS, **UPLOAD_ERRORS}, summary="Add photos to a news item")
def upload_photos(request, news_id: uuid.UUID, photos: File[list[UploadedFile]] = None):
    """Multipart: one or more `photos` image parts. If the item had no main photo, the first becomes it. Answers the photos just added."""
    item = _item(request, news_id)
    if not photos:
        validate_upload(None, field="photos")
    for upload in photos:
        validate_upload(upload, field="photos", content_types=IMAGE_TYPES, max_bytes=MAX_PHOTO_BYTES)
        _check_image(upload)
    created = add_news_photos(item, photos)
    return 201, {"items": [_out(request, photo) for photo in created]}


@router.post("/manage/news/{uuid:news_id}/photos/{uuid:photo_id}/set-main", response={200: NewsPhotoOut, **ERRORS}, summary="Make a photo the main picture")
def set_main(request, news_id: uuid.UUID, photo_id: uuid.UUID):
    photo = _photo(_item(request, news_id), photo_id)
    return _out(request, set_main_news_photo(photo))


@router.put("/manage/news/{uuid:news_id}/photos/{uuid:photo_id}/focal-point", response={200: NewsPhotoOut, **ERRORS}, summary="Set where the photo crops around")
def set_focal_point(request, news_id: uuid.UUID, photo_id: uuid.UUID, payload: FocalPointIn):
    photo = _photo(_item(request, news_id), photo_id)
    return _out(request, set_news_photo_focal_point(photo, payload.focal_x, payload.focal_y))


@router.delete("/manage/news/{uuid:news_id}/photos/{uuid:photo_id}", response={204: None, **ERRORS}, summary="Delete a photo")
def delete_photo(request, news_id: uuid.UUID, photo_id: uuid.UUID):
    """If it was the main photo, the next remaining one takes over."""
    photo = _photo(_item(request, news_id), photo_id)
    delete_news_photo(photo)
    return 204, None
