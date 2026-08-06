"""Club-scoped feature flags.

waffle's Flag model is swappable (``WAFFLE_FLAG_MODEL``, like ``AUTH_USER_MODEL``),
so we subclass it to add the one dimension this platform actually needs: which
*clubs* a feature is on for. The tenant middleware already puts ``request.club``
on every request, so a flag resolves with a plain ``flag_is_active(request, "shop")``
— no call site has to know about clubs.

Everything waffle already offers (``everyone`` / ``percent`` / ``staff`` /
``superusers`` / per-user / per-group) keeps working untouched.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from waffle.models import CACHE_EMPTY, AbstractUserFlag
from waffle.utils import get_cache, keyfmt

from rosterchief.base import UUIDModel

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
        if self.pk is None:
            # waffle's own BaseModel.get() falls back to a transient, unsaved
            # Flag(name=...) instance for a name nothing in the DB matches yet
            # (see waffle/models.py) -- an M2M lookup can't run against that, and
            # there's genuinely nothing for it to be on for anyway.
            return set()

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


class Maintenance(UUIDModel):
    """Platform lock-down. One row, read on every request.

    Cached rather than queried per request, and the cache is the same shared Redis the flags
    use — so turning maintenance on in the control panel takes effect on every worker and
    every server at once. A per-process cache would leave some workers still serving clubs.
    """

    CACHE_KEY = "maintenance:current"

    #: Cached, but not for ever. Write-through makes the flip instant for the process that
    #: made it and — on the shared Redis of a real deployment — for every other one too. The
    #: TTL is the belt to that braces: on a per-process cache (a dev box with no Redis, or a
    #: misconfigured deploy) a lock-down that only reached one gunicorn worker would be worse
    #: than useless, so the others notice within ten seconds regardless.
    CACHE_SECONDS = 10

    is_active = models.BooleanField(_("active"), default=False)
    message = models.TextField(_("message"), blank=True, help_text=_("Shown to clubs while the platform is locked down."))
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="maintenance_windows", verbose_name=_("started by"))

    class Meta:
        verbose_name = _("maintenance")
        verbose_name_plural = _("maintenance")

    def __str__(self):
        return "Maintenance on" if self.is_active else "Maintenance off"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        get_cache().set(self.CACHE_KEY, self, self.CACHE_SECONDS)

    @classmethod
    def current(cls) -> Maintenance:
        """The one row, created on first read. Cached until it changes."""
        cached = get_cache().get(cls.CACHE_KEY)
        if cached is not None:
            return cached

        maintenance = cls.objects.first() or cls.objects.create()
        get_cache().set(cls.CACHE_KEY, maintenance, cls.CACHE_SECONDS)

        return maintenance

    @classmethod
    def is_on(cls) -> bool:
        return cls.current().is_active

    @classmethod
    def start(cls, *, message: str = "", user=None) -> Maintenance:
        maintenance = cls.current()
        maintenance.is_active = True
        maintenance.message = message
        maintenance.started_at = timezone.now()
        maintenance.started_by = user
        maintenance.save()

        return maintenance

    @classmethod
    def stop(cls) -> Maintenance:
        maintenance = cls.current()
        maintenance.is_active = False
        maintenance.started_at = None
        maintenance.started_by = None
        maintenance.save()

        return maintenance
