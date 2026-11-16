"""Browser icons (favicon, PNG sizes, apple-touch-icon) generated from a club's logo.

Generated when the logo changes and stored next to it in the media storage, under ``clubs/<slug>/icons/<hash>/``. The
hash is of the logo's file name, so a new logo means new URLs (no stale browser caches) and the old set is removed.
The public branding endpoint hands the URLs out; clients never have to resize anything.
"""

import hashlib
import io
import logging

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

#: key -> (file name, edge in pixels, opaque background). The .ico bundles 16/32/48 px.
PNG_ICONS = {"png_32": ("icon-32.png", 32, False), "png_192": ("icon-192.png", 192, False), "png_512": ("icon-512.png", 512, False), "apple_touch": ("apple-touch-icon.png", 180, True)}
ICO_SIZES = [(16, 16), (32, 32), (48, 48)]
ICO_NAME = "favicon.ico"


def _base(club) -> str:
    return f"clubs/{club.slug}/icons"


def _folder(club) -> str:
    return f"{_base(club)}/{hashlib.sha1(club.logo.name.encode()).hexdigest()[:10]}"


def _square(logo: Image.Image, edge: int, opaque: bool) -> Image.Image:
    """The logo scaled to fit inside an ``edge`` square (aspect ratio kept), centred on a transparent or white canvas."""
    canvas = Image.new("RGBA", (edge, edge), (255, 255, 255, 255) if opaque else (0, 0, 0, 0))
    inner = max(1, round(edge * 0.9)) if opaque else edge
    fitted = ImageOps.contain(logo, (inner, inner), Image.Resampling.LANCZOS)
    canvas.alpha_composite(fitted, ((edge - fitted.width) // 2, (edge - fitted.height) // 2))
    return canvas


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def generate_icons(club) -> dict[str, str]:
    """(Re)build the icon set from ``club.logo``; returns ``icon_urls``. Removes the previous set. No logo: just cleans up."""
    base = _base(club)
    if default_storage.exists(base):
        _remove_tree(base)
    if not club.logo:
        return {}
    with club.logo.open("rb") as handle:
        logo = ImageOps.exif_transpose(Image.open(handle)).convert("RGBA")
    folder = _folder(club)
    for _, (name, edge, opaque) in PNG_ICONS.items():
        default_storage.save(f"{folder}/{name}", ContentFile(_png(_square(logo, edge, opaque))))
    ico = io.BytesIO()
    _square(logo, 48, False).save(ico, "ICO", sizes=ICO_SIZES)
    default_storage.save(f"{folder}/{ICO_NAME}", ContentFile(ico.getvalue()))
    return icon_urls(club)


def _remove_tree(path: str) -> None:
    dirs, files = default_storage.listdir(path)
    for name in files:
        default_storage.delete(f"{path}/{name}")
    for name in dirs:
        _remove_tree(f"{path}/{name}")


def icon_urls(club) -> dict[str, str]:
    """Site-relative URLs of the generated icons ({} when the club has no logo or nothing was generated yet)."""
    if not club.logo:
        return {}
    folder = _folder(club)
    if not default_storage.exists(f"{folder}/{ICO_NAME}") or not default_storage.exists(f"{folder}/{PNG_ICONS['png_512'][0]}"):
        return {}
    urls = {"ico": default_storage.url(f"{folder}/{ICO_NAME}")}
    urls.update({key: default_storage.url(f"{folder}/{name}") for key, (name, _, _) in PNG_ICONS.items()})
    return urls


def ensure_icons(club) -> dict[str, str]:
    """``icon_urls``, generating the set first when a logo exists that has none yet (clubs from before this existed).
    Never raises: an unreadable logo just means no icons (clients fall back to their default)."""
    if club.logo and club.logo.name.lower().endswith(".svg"):
        return {}  # A restored legacy SVG remains usable as the club crest.
    try:
        return icon_urls(club) or generate_icons(club)
    except Exception:
        logger.exception("Could not generate icons for club %s", club.slug)
        return {}
