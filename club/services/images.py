"""Dimensions for uploads that aren't Django ImageFields.

Logos (Club.logo, Sponsor.logo) are plain FileFields, not ImageFields --
Pillow can't validate SVGs, and crests/sponsor logos are commonly vector
files -- so there's no automatic width_field/height_field the way there
would be on an ImageField. This fills that gap: Pillow for raster formats,
a bounded regex read of the root <svg> tag for vector ones (not a full XML
parse -- this reads untrusted uploads, and a parser is exposed to entity
expansion attacks a plain attribute read never is).
"""

import re

from PIL import Image, UnidentifiedImageError

_SVG_TAG_RE = re.compile(rb"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_WIDTH_RE = re.compile(rb"""\bwidth\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_HEIGHT_RE = re.compile(rb"""\bheight\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_VIEWBOX_RE = re.compile(rb"""\bviewBox\s*=\s*["']\s*([\d.+-]+)[ ,]+([\d.+-]+)[ ,]+([\d.+-]+)[ ,]+([\d.+-]+)""", re.IGNORECASE)
_LEADING_NUMBER_RE = re.compile(r"[\d.]+")

#: The root <svg> tag is always near the top of the file -- no need to read
#: (or regex-scan) anything past a small header.
_SVG_HEAD_BYTES = 8192


def _svg_length(raw: bytes) -> int | None:
    """Parse an SVG length attribute (``"200"``, ``"200px"``) to a rounded
    int, or None if it's relative (``"100%"``) and so not a real pixel size."""
    text = raw.decode("utf-8", errors="ignore").strip()
    if text.endswith("%"):
        return None
    match = _LEADING_NUMBER_RE.match(text)
    return round(float(match.group(0))) if match else None


def _svg_dimensions(file) -> tuple[int | None, int | None]:
    try:
        file.seek(0)
        head = file.read(_SVG_HEAD_BYTES)
    except OSError:
        return None, None
    # Reset for whatever reads the file next (e.g. FileField writing it to storage).
    file.seek(0)

    tag_match = _SVG_TAG_RE.search(head)
    svg_tag = tag_match.group(0) if tag_match else head

    width_match, height_match = _WIDTH_RE.search(svg_tag), _HEIGHT_RE.search(svg_tag)
    if width_match and height_match:
        width, height = _svg_length(width_match.group(1)), _svg_length(height_match.group(1))
        if width and height:
            return width, height

    viewbox_match = _VIEWBOX_RE.search(svg_tag)
    if viewbox_match:
        _, _, width, height = viewbox_match.groups()
        return round(float(width)), round(float(height))

    return None, None


def get_image_dimensions(file) -> tuple[int | None, int | None]:
    """Best-effort (width, height) for an uploaded logo -- (None, None) if the
    file can't be read as an image (corrupt upload, unrecognised format)."""
    if not file:
        return None, None

    name = getattr(file, "name", "") or ""
    if name.lower().endswith(".svg"):
        return _svg_dimensions(file)

    try:
        file.seek(0)
        with Image.open(file) as image:
            size = image.size
        # Reset for whatever reads the file next (e.g. FileField writing it to storage) --
        # only on the success path, since a failed open/read leaves nothing to rewind.
        file.seek(0)
        return size
    except (OSError, UnidentifiedImageError):
        return None, None
