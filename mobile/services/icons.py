"""Server-rendered fallback PWA icon: a club's initials on its own
secondary_color -- confirmed with the user as the fallback for a club that
hasn't uploaded a logo, rather than a shared RosterChief mark (Club.initials'
own docstring: "Never the RosterChief mark -- that would pass our branding
off as the club's own", same reasoning applied to the home-screen icon).

Rendered on request by mobile.views.AppIconView, not stored -- a club's
colours/initials change rarely enough that regenerating a small PNG per
request is cheaper than adding cache invalidation for it.
"""

import io

from PIL import Image, ImageDraw, ImageFont

_DEFAULT_BACKGROUND = "#e4002b"
_DEFAULT_FOREGROUND = "#ffffff"


def render_fallback_icon(club, size: int = 512) -> bytes:
    background = club.secondary_color or _DEFAULT_BACKGROUND
    foreground = club.secondary_content_color or _DEFAULT_FOREGROUND

    image = Image.new("RGB", (size, size), background)
    draw = ImageDraw.Draw(image)

    initials = club.initials or "RC"
    font = ImageFont.load_default(size=int(size * 0.42))

    left, top, right, bottom = draw.textbbox((0, 0), initials, font=font)
    text_width, text_height = right - left, bottom - top
    draw.text(((size - text_width) / 2 - left, (size - text_height) / 2 - top), initials, font=font, fill=foreground)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
