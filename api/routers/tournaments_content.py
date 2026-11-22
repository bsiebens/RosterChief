"""``/tournaments/{id}/rules|sponsors``: the content of a tournament's public page that staff maintain -- the rules (Markdown and/or an
uploaded document) and the tournament's own sponsors (with the choice to also show the club's). Reading needs either tournament permission,
changing anything the manage tier."""

import uuid

from django.utils import timezone
from ninja import File, Router, Schema, UploadedFile
from pydantic import Field

from api.auth import AUTHENTICATORS
from api.routers import tournaments_common as c
from members.services.errors import Invalid
from sponsoring.models import Sponsor
from tournaments.models import TournamentSponsor

router = Router(auth=AUTHENTICATORS)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_RULES = (".pdf", ".doc", ".docx", ".odt")


class RulesIn(Schema):
    markdown: str = Field("", max_length=100_000)


class SponsorSettingsIn(Schema):
    use_club_sponsors: bool


class SponsorIn(Schema):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field("", max_length=200)


class SponsorPatch(Schema):
    name: str | None = Field(None, min_length=1, max_length=255)
    url: str | None = Field(None, max_length=200)
    position: int | None = Field(None, ge=0, le=32767)


def rules_out(t) -> dict:
    return {"markdown": t.rules_markdown, "file_url": c.url(t.rules_file) if t.rules_file else None, "file_name": t.rules_file.name.rsplit("/", 1)[-1] if t.rules_file else ""}


def sponsor_out(s: TournamentSponsor) -> dict:
    return {"id": s.pk, "name": s.name, "url": s.url, "logo_url": c.url(s.logo) if s.logo else None, "position": s.position}


def _sponsor(t, sponsor_id) -> TournamentSponsor:
    return c.get_or_404(TournamentSponsor, pk=sponsor_id, tournament=t)


def sponsors_out(t) -> dict:
    return {
        "use_club_sponsors": t.use_club_sponsors, "club_sponsor_count": Sponsor.active(timezone.localdate()).count(),
        "results": [sponsor_out(s) for s in t.sponsors.all()],
    }  # fmt: skip


@router.get("/{tournament_id}/rules", summary="The tournament rules", operation_id="tournaments_rules")
def get_rules(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    return rules_out(c.get_tournament(tournament_id))


@router.put("/{tournament_id}/rules", summary="Set the rules text (Markdown)", operation_id="tournaments_rules_update")
def update_rules(request, tournament_id: uuid.UUID, payload: RulesIn):
    c.gate_manage(request)
    t = c.get_tournament(tournament_id)
    t.rules_markdown = payload.markdown
    t.save(update_fields=["rules_markdown"])
    return rules_out(t)


@router.post("/{tournament_id}/rules/file", summary="Upload the rules as a document", operation_id="tournaments_rules_file")
def upload_rules(request, tournament_id: uuid.UUID, document: File[UploadedFile]):
    c.gate_manage(request)
    t = c.get_tournament(tournament_id)
    if not (document.name or "").lower().endswith(ALLOWED_RULES):
        raise Invalid("Upload a PDF, Word or OpenDocument file.", {"document": ["Upload a PDF, Word or OpenDocument file."]})
    if document.size > MAX_UPLOAD_BYTES:
        raise Invalid("The file is too large (10 MB at most).", {"document": ["The file is too large (10 MB at most)."]})
    if t.rules_file:
        t.rules_file.delete(save=False)
    t.rules_file.save(document.name, document, save=True)
    return rules_out(t)


@router.delete("/{tournament_id}/rules/file", summary="Remove the uploaded rules document", operation_id="tournaments_rules_file_delete")
def delete_rules_file(request, tournament_id: uuid.UUID):
    c.gate_manage(request)
    t = c.get_tournament(tournament_id)
    if t.rules_file:
        t.rules_file.delete(save=False)
        t.rules_file = ""
        t.save(update_fields=["rules_file"])
    return rules_out(t)


@router.get("/{tournament_id}/sponsors", summary="The tournament's own sponsors and the club sponsor choice", operation_id="tournaments_sponsors")
def get_sponsors(request, tournament_id: uuid.UUID):
    c.gate_view(request)
    return sponsors_out(c.get_tournament(tournament_id))


@router.put("/{tournament_id}/sponsors/settings", summary="Choose whether the club's sponsors are shown too", operation_id="tournaments_sponsors_settings")
def sponsor_settings(request, tournament_id: uuid.UUID, payload: SponsorSettingsIn):
    c.gate_manage(request)
    t = c.get_tournament(tournament_id)
    t.use_club_sponsors = payload.use_club_sponsors
    t.save(update_fields=["use_club_sponsors"])
    return sponsors_out(t)


@router.post("/{tournament_id}/sponsors", summary="Add a tournament sponsor", operation_id="tournaments_sponsor_create", response={201: dict})
def create_sponsor(request, tournament_id: uuid.UUID, payload: SponsorIn):
    c.gate_manage(request)
    t = c.get_tournament(tournament_id)
    last = t.sponsors.order_by("-position").values_list("position", flat=True).first()
    sponsor = TournamentSponsor(tournament=t, name=payload.name.strip(), url=payload.url.strip(), position=0 if last is None else last + 1)
    c.validate(sponsor)
    sponsor.save()
    return 201, sponsor_out(sponsor)


@router.patch("/{tournament_id}/sponsors/{sponsor_id}", summary="Change a tournament sponsor", operation_id="tournaments_sponsor_update")
def update_sponsor(request, tournament_id: uuid.UUID, sponsor_id: uuid.UUID, payload: SponsorPatch):
    c.gate_manage(request)
    sponsor = _sponsor(c.get_tournament(tournament_id), sponsor_id)
    data = {k: (v.strip() if isinstance(v, str) else v) for k, v in payload.dict(exclude_unset=True).items() if v is not None}
    c.apply(sponsor, data)
    c.validate(sponsor)
    sponsor.save()
    return sponsor_out(sponsor)


@router.post("/{tournament_id}/sponsors/{sponsor_id}/logo", summary="Upload a tournament sponsor's logo", operation_id="tournaments_sponsor_logo")
def sponsor_logo(request, tournament_id: uuid.UUID, sponsor_id: uuid.UUID, logo: File[UploadedFile]):
    c.gate_manage(request)
    sponsor = _sponsor(c.get_tournament(tournament_id), sponsor_id)
    if not (logo.content_type or "").startswith("image/"):
        raise Invalid("Upload an image.", {"logo": ["Upload an image."]})
    if logo.size > MAX_UPLOAD_BYTES:
        raise Invalid("The image is too large (10 MB at most).", {"logo": ["The image is too large (10 MB at most)."]})
    if sponsor.logo:
        sponsor.logo.delete(save=False)
    sponsor.logo.save(logo.name, logo, save=True)
    return sponsor_out(sponsor)


@router.delete("/{tournament_id}/sponsors/{sponsor_id}", summary="Remove a tournament sponsor", operation_id="tournaments_sponsor_delete", response={204: None})
def delete_sponsor(request, tournament_id: uuid.UUID, sponsor_id: uuid.UUID):
    c.gate_manage(request)
    sponsor = _sponsor(c.get_tournament(tournament_id), sponsor_id)
    if sponsor.logo:
        sponsor.logo.delete(save=False)
    sponsor.delete()
    return 204, None
