"""Coach news submission (API.md §7.3): ``/teams/{team_id}/news``.

Same rules as the web coach screen (mobile.coach_views.CoachCreateNewsView): the post is for the
path's team, lands as ``pending_review`` and goes to the editors' queue; only ADMIN, EDITOR and
coach managers may post (``permissions.can_add_news`` in ``GET /context``).
"""

import uuid
from typing import Annotated

from django import forms
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from ninja import Query, Router

from api.core.errors import ApiError
from api.core.forms import form_error
from api.core.idempotency import idempotent
from api.core.pagination import paginate
from club.services.access import can_add_news
from management.forms import NewsForm
from news.models import News, NewsPhoto
from news.services import dispatch_notify_editors_of_pending_review
from rosterchief.uploads import MAX_DOCUMENT_UPLOAD_BYTES
from teams.models import Team

from ..coach import CoachScope, coach_scope
from ..coach_schemas import CoachNewsListOut, CoachNewsOut
from ..presenters import team_ref
from ..schemas import ErrorOut

router = Router(tags=["coach"])

ERRORS = {401: ErrorOut, 403: ErrorOut, 404: ErrorOut}
MAX_PHOTOS = 10

SUBMIT_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["title", "body"],
                    "properties": {
                        "title": {"type": "string", "maxLength": 255},
                        "body": {"type": "string", "description": "Markdown."},
                        "photos": {
                            "type": "array",
                            "maxItems": MAX_PHOTOS,
                            "items": {"type": "string", "format": "binary"},
                            "description": f"Up to {MAX_PHOTOS} JPEG, PNG or WebP images (HEIC: convert to JPEG first), each at most 10 MB. The first is the cover.",
                        },
                    },
                }
            }
        },
    }
}


def news_out(request, item: News) -> dict:
    return {
        "id": item.pk,
        "title": item.title,
        "body": item.body,
        "status": item.status,
        "published_at": item.published_at,
        "created": item.created,
        "teams": [team_ref(team) for team in item.teams.all()],
        "photos": [{"id": photo.pk, "url": request.build_absolute_uri(photo.image.url), "is_main": photo.is_main} for photo in item.photos.all()],
    }


def _news(coach: CoachScope):
    """The caller's own submissions for this team."""
    me = coach.club_scope.me
    queryset = News.objects.filter(club=coach.club, teams=coach.team).prefetch_related("teams", "photos")
    return queryset.filter(created_by=me) if me else queryset.none()


def _photos(request) -> list:
    uploads = request.FILES.getlist("photos")
    problems = []
    if len(uploads) > MAX_PHOTOS:
        raise ApiError(422, "validation_error", fields={"photos": [_("At most %(count)d photos.") % {"count": MAX_PHOTOS}]})
    checker = forms.ImageField()
    for upload in uploads:
        if upload.size > MAX_DOCUMENT_UPLOAD_BYTES:
            problems.append(_("“%(name)s” is larger than 10 MB.") % {"name": upload.name})
            continue
        try:
            checker.clean(upload)
        except forms.ValidationError as error:
            problems.append(f"“{upload.name}”: {error.messages[0]}")
        upload.seek(0)
    if problems:
        raise ApiError(422, "validation_error", fields={"photos": [str(problem) for problem in problems]})
    return uploads


@router.get("/teams/{team_id}/news", response={200: CoachNewsListOut, **ERRORS}, summary="Your own news submissions for the team")
def list_news(request, team_id: uuid.UUID, cursor: Annotated[str | None, Query()] = None, limit: int | None = None):
    """What the caller submitted for this team, newest first, with its `status` (waiting for
    review, published, or taken back to draft). Any staff of the team."""
    coach = coach_scope(request, team_id)
    page = paginate(_news(coach), sort_field="-created", cursor=cursor, limit=limit)
    return {"items": [news_out(request, item) for item in page.items], "next_cursor": page.next_cursor}


@router.post("/teams/{team_id}/news", response={201: CoachNewsOut, **ERRORS, 422: ErrorOut}, summary="Submit a news post for review", openapi_extra=SUBMIT_BODY)
def submit_news(request, team_id: uuid.UUID):
    """`multipart/form-data` with `title`, `body` (Markdown) and any number of `photos` files. The
    post is for this team, internal (the team's families, in the apps), and arrives as
    `pending_review`: an editor publishes it, so it is not live yet and the editors are told. Needs
    `permissions.can_add_news` (`403 forbidden` otherwise; a physio or assistant cannot post). Send an
    `Idempotency-Key`: a retry then does not post twice."""
    coach = coach_scope(request, team_id)
    if not can_add_news(request.user, coach.club):
        raise ApiError(403, "forbidden", _("You can't post news for this club."))

    def work():
        photos = _photos(request)
        form = NewsForm({"title": request.POST.get("title", ""), "body": request.POST.get("body", ""), "teams": [str(coach.team.pk)]}, club=coach.club, instance=News(club=coach.club, created_by=coach.club_scope.me))
        for name in ("title_en", "body_en", "visibility"):
            del form.fields[name]
        form.fields["teams"].queryset = Team.objects.filter(pk=coach.team.pk)
        form.fields["teams"].required = True
        if not form.is_valid():
            raise form_error(form)
        with transaction.atomic():
            item = form.save()
            for index, image in enumerate(photos):
                NewsPhoto.objects.create(news_item=item, image=image, is_main=index == 0)
            item.submit_for_review()
        dispatch_notify_editors_of_pending_review(item.pk)
        return 201, news_out(request, News.objects.prefetch_related("teams", "photos").get(pk=item.pk))

    return idempotent(request, work)
