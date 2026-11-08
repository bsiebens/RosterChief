"""The payment form of a game's referees or officials: a club-branded sheet (logo, name, address, colours) with the game, the
fee / kilometres per person and a signature box each. Rendered as HTML (shown in the app) and turned into a PDF with WeasyPrint
(``club.previews.render_pdf``). The same template serves the sample in the club settings' PDF tab (``sample_context``)."""

import base64
import datetime
import mimetypes
from decimal import Decimal
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.utils import timezone, translation

from events.models import Location
from events.services import referees as svc

LANGUAGES = ("en", "nl", "fr")
DEFAULT_ACCENT, DEFAULT_SECONDARY = "#3730a3", "#be185d"

LABELS = {
    "en": {
        "title": {"referee": "Referee payment form", "official": "Official payment form"},
        "people": {"referee": "Referee names", "official": "Official names"},
        "section": {"referee": "Referees and fee, plus any kilometers", "official": "Officials and fee, plus any kilometers"},
        "person": {"referee": "Referee", "official": "Official"},
        "none": {"referee": "No referees assigned yet.", "official": "No officials assigned yet."},
        "sign": {"referee": "Referee signature", "official": "Official signature"},
        "pays": "{club} pays the fee to the {who} for officiating the game mentioned above.",
        "who": {"referee": "referee(s)", "official": "official(s)"},
        "game": "Game",
        "date": "Date",
        "time": "Time",
        "id": "ID",
        "vs": "vs",
        "fee": "Fee",
        "km": "Kilometers",
        "rate": "Rate",
        "total": "Total",
        "total_pay": "Total to pay",
        "no_fee": "No fee",
        "collected": "Collected by team manager",
        "footer": "payment form",
    },
    "nl": {
        "title": {"referee": "Betaalformulier scheidsrechter", "official": "Betaalformulier official"},
        "people": {"referee": "Namen scheidsrechters", "official": "Namen officials"},
        "section": {"referee": "Scheidsrechters en vergoeding, plus eventuele kilometers", "official": "Officials en vergoeding, plus eventuele kilometers"},
        "person": {"referee": "Scheidsrechter", "official": "Official"},
        "none": {"referee": "Nog geen scheidsrechters toegewezen.", "official": "Nog geen officials toegewezen."},
        "sign": {"referee": "Handtekening scheidsrechter", "official": "Handtekening official"},
        "pays": "{club} betaalt de vergoeding aan de {who} voor het leiden van de hierboven vermelde wedstrijd.",
        "who": {"referee": "scheidsrechter(s)", "official": "official(s)"},
        "game": "Wedstrijd",
        "date": "Datum",
        "time": "Uur",
        "id": "ID",
        "vs": "tegen",
        "fee": "Vergoeding",
        "km": "Kilometers",
        "rate": "Tarief",
        "total": "Totaal",
        "total_pay": "Totaal te betalen",
        "no_fee": "Geen vergoeding",
        "collected": "Opgehaald door ploegverantwoordelijke",
        "footer": "betaalformulier",
    },
    "fr": {
        "title": {"referee": "Formulaire de paiement de l'arbitre", "official": "Formulaire de paiement de l'officiel"},
        "people": {"referee": "Noms des arbitres", "official": "Noms des officiels"},
        "section": {"referee": "Arbitres et indemnité, plus kilomètres éventuels", "official": "Officiels et indemnité, plus kilomètres éventuels"},
        "person": {"referee": "Arbitre", "official": "Officiel"},
        "none": {"referee": "Aucun arbitre désigné pour le moment.", "official": "Aucun officiel désigné pour le moment."},
        "sign": {"referee": "Signature de l'arbitre", "official": "Signature de l'officiel"},
        "pays": "{club} verse l'indemnité à l'(aux) {who} pour avoir officié le match mentionné ci-dessus.",
        "who": {"referee": "arbitre(s)", "official": "officiel(s)"},
        "game": "Match",
        "date": "Date",
        "time": "Heure",
        "id": "ID",
        "vs": "contre",
        "fee": "Indemnité",
        "km": "Kilomètres",
        "rate": "Tarif",
        "total": "Total",
        "total_pay": "Total à payer",
        "no_fee": "Pas d'indemnité",
        "collected": "Perçu par le responsable d'équipe",
        "footer": "formulaire de paiement",
    },
}


def _channels(hex_color: str):
    return [int(hex_color[i : i + 2], 16) for i in (1, 3, 5)]


def _near_black_or_white(hex_color: str, threshold: float = 0.12) -> bool:
    lightness = sum(_channels(hex_color)) / (3 * 255)
    return lightness < threshold or lightness > 1 - threshold


def tint(hex_color: str, strength: float = 0.14) -> str:
    """A pale tint of ``hex_color`` (WeasyPrint has no ``color-mix``)."""
    return "#{:02x}{:02x}{:02x}".format(*(round(c * strength + 255 * (1 - strength)) for c in _channels(hex_color)))


def colors(club) -> dict:
    """The club's primary colour as accent and a pale card tint, taken from the secondary colour when the primary is black or white."""
    accent = club.primary_color or DEFAULT_ACCENT
    secondary = club.secondary_color or DEFAULT_SECONDARY
    return {"accent_color": accent, "info_card_color": tint(secondary if _near_black_or_white(accent) else accent)}


def logo_data_uri(club) -> str:
    if not club.logo:
        return ""
    try:
        with club.logo.open("rb") as f:
            raw = f.read()
    except Exception:
        return ""
    return f"data:{mimetypes.guess_type(club.logo.name)[0] or 'image/png'};base64,{base64.b64encode(raw).decode()}"


def document_address(club):
    """The club's legal address, else its home ground, else nothing."""
    if club.legal_address:
        return SimpleNamespace(address=club.legal_address, zip_code=club.legal_zip_code, city=club.legal_city)
    return Location.objects.filter(is_home=True).first()


def _row(name, type_name, fee, km, km_rate, no_fee, total):
    return SimpleNamespace(name=name, type_name=type_name, fee=fee, km=km, km_rate=km_rate, no_fee=no_fee, total=total)


def build_context(club, game, rows, kind: str, lang: str = "en") -> dict:
    """``game``: ``{date, time, teams, opponent, external_id}``; ``rows``: ``[_row(...)]``."""
    lang = lang if lang in LANGUAGES else "en"
    labels = {k: (v[kind] if isinstance(v, dict) and kind in v else v) for k, v in LABELS[lang].items()}
    return {
        "club": club,
        "official_name": club.legal_name or club.name,
        "logo": logo_data_uri(club),
        "address": document_address(club),
        "game": game,
        "rows": rows,
        "grand_total": sum((r.total for r in rows), Decimal("0")),
        "L": labels,
        "pays": LABELS[lang]["pays"].format(club=club.name, who=labels["who"]),
        "lang": lang,
        "footer": f"{club.name} - {labels['footer']}",
    } | colors(club)


def context_for_event(club, event, kind_code: str, lang: str = "en") -> dict:
    kind = svc.kind_of(kind_code)
    local = timezone.localtime(event.start)
    game = SimpleNamespace(
        date=local.date(),
        time=local.strftime("%H:%M"),
        teams=", ".join(t.short_name for t in event.teams.all()),
        opponent=event.opponent.name if event.opponent_id else "",
        external_id=event.external_game_id,
    )
    rows = [_row(r.display_name, r.referee_type.name if r.referee_type_id else "", r.fee, r.km, r.km_rate, r.no_fee, r.total_payable) for r in svc.order_assignments(getattr(event, kind.related).all())]
    return build_context(club, game, rows, kind_code, lang)


def sample_context(club, kind_code: str = "referee", lang: str = "en") -> dict:
    """Made-up game and people, with the club's real branding -- the PDF tab in the club settings."""
    game = SimpleNamespace(date=timezone.localdate() + datetime.timedelta(days=7), time="14:30", teams="U16", opponent="Leuven", external_id="BE-2026-00417")
    rows = [
        _row("Jamie Doe", "Referee" if kind_code == "referee" else "Table official", Decimal("35.00"), Decimal("12.0"), Decimal("0.3500"), False, Decimal("39.20")),
        _row("Alex Sample", "Linesman" if kind_code == "referee" else "Timekeeper", Decimal("35.00"), None, None, False, Decimal("35.00")),
    ]
    return build_context(club, game, rows, kind_code, lang)


def render_html(context: dict) -> str:
    with translation.override(context["lang"]):
        return render_to_string("events/payment_form.html", context)
