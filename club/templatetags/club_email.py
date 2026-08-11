"""Template helper shared by every HTML email that shows a club's logo.

An <img> in an email has no page to resolve a relative /media/... URL against
the way a browser tab would -- the inbox fetches it cold. Storage backends that
already return an absolute URL (e.g. S3 in production) are unaffected; this
only matters for the local FileSystemStorage used in dev, where FieldFile.url
is relative.
"""

from django import template

register = template.Library()


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
