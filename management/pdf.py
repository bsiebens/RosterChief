"""HTML-to-PDF rendering for exports (currently just the memberships list).

Same lazy-import shape as billing.services.invoices.render_pdf -- WeasyPrint binds
to native pango/cairo libraries, and a machine without them must still be able to
run the app, the tests and every other page, so the import happens here, not at
module scope, and only fails when someone actually asks for a PDF. Kept separate
from billing's version rather than shared: the two exports have nothing else in
common, and sharing would make one app depend on the other's error type for no
real benefit.
"""

from django.template.loader import render_to_string

#: Referee form defaults, used whenever a club hasn't set its own colours (see
#: club.models.Club.primary_color/secondary_color) -- picked to match the
#: app's own daisyUI theme accent/secondary so an unbranded club's form still
#: looks intentional rather than grey.
DEFAULT_ACCENT_COLOR = "#3730a3"
DEFAULT_SECONDARY_COLOR = "#be185d"


class PDFExportError(Exception):
    """Raised when WeasyPrint's native libraries aren't available."""


def _is_near_black_or_white(hex_color: str, threshold: float = 0.12) -> bool:
    """Whether a #rrggbb colour reads as (near-)black or (near-)white -- a rough
    perceived-lightness check (plain channel average, not WCAG luminance: this
    doesn't need to be contrast-accurate, just good enough to say "too close to
    black or white to make a nice pale card background")."""
    red, green, blue = (int(hex_color[index : index + 2], 16) for index in (1, 3, 5))
    lightness = (red + green + blue) / (3 * 255)
    return lightness < threshold or lightness > 1 - threshold


def _tint_with_white(hex_color: str, strength: float = 0.14) -> str:
    """A pale, card-background-friendly tint of `hex_color` -- mixed `strength`
    of the colour into white. Computed here rather than left to CSS
    `color-mix()`: WeasyPrint doesn't support that function, so the rule was
    silently dropped and the card rendered with no background at all."""
    channels = (int(hex_color[index : index + 2], 16) for index in (1, 3, 5))
    mixed = (round(channel * strength + 255 * (1 - strength)) for channel in channels)
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def referee_form_colors(club) -> dict:
    """Accent colour (the club's own primary_color, or the app default) and a
    pale info-card background tint for the referee payment form.

    The card is tinted off the *secondary* colour instead when the primary
    colour is itself (near) black or white -- using it straight would make an
    all-but-invisible near-white-on-white or a harsh near-black card, so the
    secondary colour (meant for exactly this kind of highlight -- see
    Club.secondary_color's help text) stands in instead.
    """
    accent = club.primary_color or DEFAULT_ACCENT_COLOR
    secondary = club.secondary_color or DEFAULT_SECONDARY_COLOR
    tint_source = secondary if _is_near_black_or_white(accent) else accent
    return {"accent_color": accent, "info_card_color": _tint_with_white(tint_source)}


def render_pdf(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as error:
        raise PDFExportError("PDF rendering needs the native pango/cairo libraries (on macOS: brew install pango).") from error

    return HTML(string=html).write_pdf()


def membership_list_pdf(context: dict) -> bytes:
    html = render_to_string("management/membership_list_pdf.html", context)
    return render_pdf(html)


def event_referee_form_pdf(context: dict) -> bytes:
    html = render_to_string("management/event_referee_form_pdf.html", context)
    return render_pdf(html)
