import datetime
import uuid

from ninja import Schema


class NewsPersonOut(Schema):
    id: uuid.UUID
    name: str


class NewsTeamOut(Schema):
    id: uuid.UUID
    name: str


class NewsPhotoOut(Schema):
    id: uuid.UUID
    url: str
    is_main: bool
    ordering: int
    focal_x: int
    focal_y: int


class NewsListItem(Schema):
    id: uuid.UUID
    title: str
    slug: str
    status: str
    visibility: str
    published_at: datetime.datetime | None = None
    is_scheduled: bool
    created: datetime.datetime
    created_by: NewsPersonOut | None = None
    team_ids: list[uuid.UUID]
    teams: list[NewsTeamOut]
    main_photo_url: str | None = None
    can_edit: bool


class NewsDetail(NewsListItem):
    body: str
    title_en: str
    body_en: str
    body_html: str
    notified_at: datetime.datetime | None = None
    #: The reviewer's note when the item was sent back to its author (empty otherwise).
    review_note: str = ""
    sent_back_at: datetime.datetime | None = None
    photos: list[NewsPhotoOut]
    can_publish: bool
    can_delete: bool


class NewsListOut(Schema):
    count: int
    results: list[NewsListItem]


class NewsIn(Schema):
    title: str
    body: str
    title_en: str = ""
    body_en: str = ""
    visibility: str = "internal"
    team_ids: list[uuid.UUID] = []


class NewsPatch(Schema):
    title: str | None = None
    body: str | None = None
    title_en: str | None = None
    body_en: str | None = None
    visibility: str | None = None
    team_ids: list[uuid.UUID] | None = None


class NewsPublishIn(Schema):
    published_at: datetime.datetime | None = None
    notify: bool = True


class NewsFocalIn(Schema):
    focal_x: int
    focal_y: int


class NewsTeamOptionOut(Schema):
    id: uuid.UUID
    name: str
    short_name: str


class PublicNewsPhotoOut(Schema):
    url: str
    is_main: bool
    ordering: int
    focal_x: int
    focal_y: int
    object_position: str


class PublicNewsTextOut(Schema):
    title: str
    excerpt: str
    body: str


class PublicNewsItemOut(Schema):
    """``translations`` is keyed by language code (``en``, ``nl``): the club's own text as authored under ``nl``, the English
    text (falling back to the authored one when untranslated) under ``en``. Never blank."""

    id: uuid.UUID
    slug: str
    translations: dict[str, PublicNewsTextOut]
    published_at: datetime.datetime
    teams: list[str]
    photos: list[PublicNewsPhotoOut]


class PublicNewsListOut(Schema):
    count: int
    limit: int
    offset: int
    results: list[PublicNewsItemOut]
