"""Template helper shared by every HTML email that shows a club's logo.

An <img> in an email has no page to resolve a relative /media/... URL against
the way a browser tab would -- the inbox fetches it cold. Storage backends that
already return an absolute URL (e.g. S3 in production) are unaffected; this
only matters for the local FileSystemStorage used in dev, where FieldFile.url
is relative.
"""

from django import template

from club.models import Club

register = template.Library()


@register.filter
def contrast_color(hex_color):
    """Black or white, whichever reads on ``hex_color`` -- for a literal
    fallback background (e.g. ``club.secondary_color|default:"#ec4899"``)
    rather than a club's own colour, which already has a matching
    ``primary_content_color``/``secondary_content_color`` computed for it.

    Chaining this onto the *same* expression used for the background --
    ``club.secondary_color|default:"#ec4899"|contrast_color`` -- rather than
    hardcoding a second, independently-guessed text colour is what keeps the
    two from drifting apart: a pale fallback and a dark one both get the
    contrast Club._content_color_for would compute for them either way.
    """
    return Club._content_color_for(hex_color)


@register.filter
def primary_text_color(club, fallback):
    """Text colour for the club's primary colour, or for ``fallback`` when the
    club set none -- ``club|primary_text_color:"#4f46e5"`` pairs with a
    background of ``club.primary_color|default:"#4f46e5"``. Honours the club's own
    primary_text_color (Club.primary_content_color) when it has a colour."""
    # ``club`` is None on emails sent off a club subdomain (e.g. a password reset).
    return club.primary_content_color if club and club.primary_color else Club._content_color_for(fallback)


@register.filter
def secondary_text_color(club, fallback):
    """The secondary-colour counterpart to primary_text_color."""
    return club.secondary_content_color if club and club.secondary_color else Club._content_color_for(fallback)


@register.simple_tag(takes_context=True)
def absolute_media_url(context, file_field):
    """An absolute URL for ``file_field`` (e.g. ``club.logo``), for use in an
    email. Falls back to the field's own (possibly relative) ``.url`` when
    there's no request in the template context to build an absolute one from --
    better a relative URL than a hard error while rendering the email."""
    if not file_field:
        return ""

    request = context.get("request")
    return request.build_absolute_uri(file_field.url) if request is not None else file_field.url
