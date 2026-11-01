from ninja import Schema


class ClubOut(Schema):
    slug: str
    name: str
    is_platform: bool


class MeOut(Schema):
    id: str
    email: str
    first_name: str
    last_name: str
    club: ClubOut
    roles: list[str]
    features: list[str]
