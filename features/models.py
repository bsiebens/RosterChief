"""Club-scoped feature flags.

waffle's Flag model is swappable (``WAFFLE_FLAG_MODEL``, like ``AUTH_USER_MODEL``),
so we subclass it to add the one dimension this platform actually needs: which
*clubs* a feature is on for. The tenant middleware already puts ``request.club``
on every request, so a flag resolves with a plain ``flag_is_active(request, "shop")``
— no call site has to know about clubs.

Everything waffle already offers (``everyone`` / ``percent`` / ``staff`` /
``superusers`` / per-user / per-group) keeps working untouched.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _
from waffle.models import CACHE_EMPTY, AbstractUserFlag
from waffle.utils import get_cache, keyfmt

#: Cache key template for a flag's club ids, mirroring waffle's own
#: FLAG_USERS_CACHE_KEY / FLAG_GROUPS_CACHE_KEY.
FLAG_CLUBS_CACHE_KEY = "flag:%s:clubs"


class Flag(AbstractUserFlag):
    clubs = models.ManyToManyField(
        "club.Club",
        blank=True,
        related_name="flags",
        help_text=_("Activate this flag for these clubs."),
        verbose_name=_("Clubs"),
    )

    def get_flush_keys(self, flush_keys=None):
        flush_keys = super().get_flush_keys(flush_keys)
        flush_keys.append(keyfmt(FLAG_CLUBS_CACHE_KEY, self.name))
        return flush_keys

    def _get_club_ids(self) -> set:
        """Club ids this flag is on for, cached the way waffle caches its own M2Ms."""
        cache = get_cache()
        cache_key = keyfmt(FLAG_CLUBS_CACHE_KEY, self.name)

        cached = cache.get(cache_key)
        if cached == CACHE_EMPTY:
            return set()
        if cached:
            return cached

        club_ids = set(self.clubs.values_list("pk", flat=True))
        if not club_ids:
            cache.add(cache_key, CACHE_EMPTY)
            return set()

        cache.add(cache_key, club_ids)
        return club_ids

    def is_active(self, request, read_only=False):
        # waffle's contract: `everyone` overrides *all* other settings. So a flag
        # explicitly switched off for everyone stays off even for a targeted club,
        # and club targeting only applies while `everyone` is left Unknown (None).
        if self.everyone is None:
            club = getattr(request, "club", None)
            if club is not None and club.pk in self._get_club_ids():
                return True

        return super().is_active(request, read_only=read_only)

    def is_active_for_club(self, club) -> bool:
        """Explicit check for code that holds a club but no request."""
        if self.everyone is not None:
            return self.everyone
        return club.pk in self._get_club_ids()
