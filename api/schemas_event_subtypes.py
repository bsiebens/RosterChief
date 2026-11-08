import uuid

from ninja import Schema


class EventSubtypeOut(Schema):
    id: uuid.UUID
    kind: str
    kind_label: str
    name: str
    title_template: str
    ordering: int
    in_use_count: int


class EventSubtypeIn(Schema):
    kind: str
    name: str
    title_template: str = ""
    ordering: int = 0


class EventSubtypePatch(Schema):
    kind: str | None = None
    name: str | None = None
    title_template: str | None = None
    ordering: int | None = None


class EventSubtypeOrderIn(Schema):
    ids: list[uuid.UUID]
