from ninja import Router, Schema

from .services import consume_for

router = Router(tags=["announcements"])


class AnnouncementsOut(Schema):
    announcements: list[AnnouncementSchema]


class AnnouncementSchema(Schema):
    title: str
    message: str


@router.get("/announcements/pending/", response={200: AnnouncementsOut}, summary="Platform announcements")
def pending_announcements(request):
    if not request.user.is_authenticated:
        return {"announcements": []}

    announcements = consume_for(request.user, getattr(request, "club", None))
    return {"announcements": [{"title": a.title, "message": a.message} for a in announcements]}
