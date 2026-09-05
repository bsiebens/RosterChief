"""Full, irreversible deletion of a club and every row it owns -- not the same
as Club.archive() (club/models.py), which just stops it resolving on its
subdomain while keeping everything. Superuser-only, from the control panel's
own "danger zone" -- see controlpanel/views.py's ClubDeleteView. There is no
undo once this runs.

A plain ``club.delete()`` cannot do this in one pass. Every club-owned model
carries ``club`` with ``on_delete=CASCADE`` (rosterchief.base.ClubScopedModel),
but several of those same models also PROTECT *another* club-scoped model --
e.g. club.ClubMembership.season PROTECTs club.Season, so an admin can't
accidentally delete a season that's still in use. That safeguard becomes an
obstacle here: Django's deletion collector checks every PROTECT relation
before anything is actually deleted, so it has no way to know the protecting
ClubMembership row is *also* about to be deleted in this same operation -- it
just sees a Season marked for deletion that something still protects, and
refuses. Resolved below by deleting whatever's currently reported as
protected first, then re-collecting -- repeated until nothing protects
anything anymore, since clearing one layer can reveal another PROTECT
relation one level deeper.

``Member``/``User`` are never touched -- neither is club-scoped (a person can
belong to more than one club), so deleting a club only removes its own
``ClubMembership``/``StaffAssignment``/etc. rows, never the person's account,
even one left with no club at all afterwards.
"""

from collections import Counter
from dataclasses import dataclass, field

from django.apps import apps
from django.contrib.admin.utils import NestedObjects
from django.db import router, transaction
from django.db.models import ProtectedError, RestrictedError

#: Deep enough for any real PROTECT chain in this schema (empirically 2-3
#: passes) with headroom to spare -- a genuine cycle (which would be a
#: modeling bug, not a data issue) hits this and stops rather than looping
#: forever.
_MAX_RESOLUTION_PASSES = 25


class ClubDeletionBlocked(Exception):
    """Something outside this club's own cascade -- or a genuine PROTECT
    cycle -- still refuses deletion after every resolution pass."""


@dataclass
class ClubDeletionImpact:
    #: (verbose_name_plural, count) pairs, alphabetical -- everything that
    #: would be deleted along with the club, one row per affected model.
    counts: list[tuple[str, int]] = field(default_factory=list)
    total: int = 0
    #: Objects still blocking deletion after every resolution pass. Empty in
    #: the overwhelmingly common case (see this module's own docstring) --
    #: checked for real rather than assumed, the same way Django admin's own
    #: bulk-delete confirmation does.
    protected: list = field(default_factory=list)

    @property
    def can_delete(self) -> bool:
        return not self.protected


def _label_to_display(label: str) -> str:
    app_label, model_name = label.split(".")
    return str(apps.get_model(app_label, model_name)._meta.verbose_name_plural)


def _resolve_protection(club, using) -> tuple[Counter, list]:
    """Deletes whatever's currently protecting one of club's own cascade
    targets, re-collecting and repeating until nothing's protected anymore
    (or a pass makes no progress at all). Returns a tally of
    {"app_label.model": count} for everything removed this way -- a later
    club.delete() call would no longer see these rows to count them itself,
    since they're already gone by then -- and whatever's still protected
    once resolution gives up."""
    tally = Counter()

    for _ in range(_MAX_RESOLUTION_PASSES):
        collector = NestedObjects(using=using)
        collector.collect([club])
        if not collector.protected:
            return tally, []

        progress = False
        for obj in list(collector.protected):
            try:
                _count, per_model = obj.delete()
            except (ProtectedError, RestrictedError):
                continue
            tally.update(per_model)
            progress = True

        if not progress:
            break

    collector = NestedObjects(using=using)
    collector.collect([club])
    return tally, list(collector.protected)


def deletion_impact(club) -> ClubDeletionImpact:
    """What deleting ``club`` would actually do, without deleting anything for
    real -- runs the real resolution-and-delete inside a savepoint and rolls
    it back, rather than trying to predict it separately, so the preview can
    never drift from what the "Permanently delete" button actually does."""
    using = router.db_for_write(type(club))
    club_label = f"{club._meta.app_label}.{club._meta.model_name}"

    with transaction.atomic(using=using):
        sid = transaction.savepoint(using=using)
        # A fresh copy, not the caller's own `club` -- Model.delete() clears an
        # instance's pk in memory the moment it runs, and a savepoint rollback
        # only undoes the database side of that, not the Python object. The
        # caller's `club` must still be perfectly usable after this preview
        # returns (its .pk, .name, ...), exactly as if nothing had touched it.
        preview_club = type(club).objects.get(pk=club.pk)
        tally, protected = _resolve_protection(preview_club, using)
        if not protected:
            _count, per_model = preview_club.delete()
            tally.update(per_model)
        transaction.savepoint_rollback(sid, using=using)

    tally.pop(club_label, None)
    counts = sorted((_label_to_display(label), count) for label, count in tally.items())

    return ClubDeletionImpact(counts=counts, total=sum(count for _label, count in counts), protected=protected)


def delete_club(club) -> None:
    using = router.db_for_write(type(club))

    with transaction.atomic(using=using):
        _tally, protected = _resolve_protection(club, using)
        if protected:
            raise ClubDeletionBlocked(f"{club} could not be fully deleted -- still blocked by {len(protected)} record(s) after every resolution pass.")
        club.delete()
