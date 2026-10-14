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

    A missing/empty User-Agent defaults to mobile, not desktop -- every real
    browser (phone or desktop) always sends one, so an absent header means an
    unidentifiable client (a stripped-down proxy, a bot, Django's own test
    Client when a test doesn't set HTTP_USER_AGENT) rather than a genuine
    desktop signal. Mobile is the safer default: it's the one surface this
    app has served exclusively until desktop Member mode existed, so an
    unknown client -- and every pre-existing test that never had to think
    about device detection -- keeps landing there rather than silently
    flipping to desktop-only markup.
    """
    override = _layout_override(request)
    if override is not None:
        request.layout_reason = f"override:{override}"
        return override == "phone"
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    if not user_agent:
        request.layout_reason = "no-user-agent"
        return True
    matched = _MOBILE_OR_TABLET_UA.search(user_agent)
    request.layout_reason = f"user-agent:{matched.group(0)}" if matched else "user-agent:desktop"
    return bool(matched)


def _layout_override(request):
    """``"phone"`` / ``"desktop"`` when the visitor chose a layout, else None.

    ``?layout=phone|desktop`` picks one and is remembered for the session (``?layout=auto`` clears
    it), so a browser whose User-Agent says one thing while the window is the other -- device mode,
    "Request Mobile Website", an iPad -- can still be put on the layout it should have."""
    session = getattr(request, "session", None)
    requested = request.GET.get("layout")
    if session is not None:
        if requested in ("phone", "desktop"):
            session["layout"] = requested
        elif requested == "auto":
            session.pop("layout", None)
        value = session.get("layout")
        return value if value in ("phone", "desktop") else None
    return requested if requested in ("phone", "desktop") else None
