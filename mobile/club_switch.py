"""Switch club: the clubs an account may jump between, for Me's row and the picker.

Sessions are shared across club subdomains (settings.SESSION_COOKIE_DOMAIN), so switching
is a plain navigation to the other club's app. The list reuses api.protected's ``clubs_for`` -- the
exact membership definition the native ClubPicker uses -- so a club the account has no tie
to is never listed, and every URL is built from the Club's own slug (never from input).
"""

from django.urls import reverse

from api.protected.services import club_host, clubs_for, logo_url


def switchable_clubs(request) -> list:
    return clubs_for(request.user)


def has_multiple_clubs(request) -> bool:
    return len(switchable_clubs(request)) > 1


def club_picker_rows(request) -> list[dict]:
    current = getattr(request, "club", None)
    path = reverse("mobile:home")
    rows = []
    for club in switchable_clubs(request):
        is_current = current is not None and club.pk == current.pk
        rows.append(
            {
                "club": club,
                "logo_url": logo_url(request, club),
                "is_current": is_current,
                "url": path if is_current else f"{request.scheme}://{club_host(request, club)}{path}",
            }
        )
    return rows
