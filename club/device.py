"""Best-effort device-class sniff, used to steer the tenant root (see
``club.views.root``) between the mobile PWA and the desktop management UI.
"""

import re

#: Tokens seen on phone and tablet User-Agents. Deliberately over-inclusive on
#: tablets (Kindle/Silk/PlayBook/Nexus/SM-T/GT-P) -- management isn't responsive
#: yet (see CLAUDE.md), so a tablet belongs on the same touch-first PWA a phone
#: gets, not squinting at the desktop layout.
_MOBILE_OR_TABLET_UA = re.compile(
    r"Mobi|Android|iPhone|iPod|iPad|IEMobile|BlackBerry|BB10|Opera Mini|Windows Phone|webOS|Kindle|Silk|PlayBook|Nexus (?:7|9|10)|SM-T|GT-P|Tablet",
    re.IGNORECASE,
)


def is_mobile_or_tablet(request) -> bool:
    """Whether ``request`` looks like it came from a phone or tablet.

    User-Agent sniffing only -- there's no client-side signal (viewport,
    touch points) available yet at this point, this fires on the very first
    response before any of our own JS has run. Known gap: iPadOS 13+ ships a
    desktop-class Safari User-Agent indistinguishable from a real Mac unless
    the visitor has switched their browser to "Request Mobile Website" --
    not worth chasing here, since root() already falls back to the mobile
    app for anyone without management access anyway.
    """
    return bool(_MOBILE_OR_TABLET_UA.search(request.META.get("HTTP_USER_AGENT", "")))
