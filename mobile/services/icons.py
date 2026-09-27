"""Server-rendered fallback PWA icon: a club's initials on its own
secondary_color -- confirmed with the user as the fallback for a club that
hasn't uploaded a logo, rather than a shared RosterChief mark (Club.initials'
own docstring: "Never the RosterChief mark -- that would pass our branding
off as the club's own", same reasoning applied to the home-screen icon).

Rendered on request by mobile.views.AppIconView, not stored in the database.
Only the sizes the manifest and templates reference are served, and the
rendered bytes are cached under a key built from every input that affects the
image (club, colours, initials, size), so a colour or name change simply
produces a new key rather than needing explicit invalidation.
"""

import hashlib
import io

from django.core.cache import cache
from PIL import Image, ImageDraw, ImageFont

_DEFAULT_BACKGROUND = "#e4002b"
_DEFAULT_FOREGROUND = "#ffffff"

#: The icon sizes referenced by the manifest (mobile.views.ManifestView) and
#: the mobile base templates. Anything else is a 404.
ICON_SIZES = frozenset({192, 512})

_CACHE_TIMEOUT = 60 * 60 * 24


def fallback_icon_cache_key(club, size: int) -> str:
    fingerprint = "|".join([str(club.pk), club.secondary_color or "", club.secondary_content_color or "", club.initials or "", str(size)])
    return f"mobile:fallback-icon:{hashlib.sha256(fingerprint.encode()).hexdigest()}"


def get_fallback_icon(club, size: int) -> bytes:
    """Cached wrapper around render_fallback_icon. Callers must restrict
    ``size`` to ICON_SIZES first."""
    if size not in ICON_SIZES:
        raise ValueError(f"Unsupported icon size: {size}")
    key = fallback_icon_cache_key(club, size)
    data = cache.get(key)
    if data is None:
        data = render_fallback_icon(club, size=size)
        cache.set(key, data, _CACHE_TIMEOUT)
    return data


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
