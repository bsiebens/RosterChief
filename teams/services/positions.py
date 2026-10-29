"""Positions and number pools: the club-wide lookups the Teams screens and the management API edit."""

from django.db.models import ProtectedError


def delete_position(position) -> bool:
    """Delete ``position``; False (and nothing deleted) while a roster or staff assignment still uses it."""
    try:
        position.delete()
    except ProtectedError:
        return False
    return True


def name_taken(model, club, name: str, *, exclude=None) -> bool:
    """Whether ``club`` already has a ``model`` row with exactly this name.

    The (club, name) unique constraint is skipped by the web forms (club isn't a form field), so
    callers that want a validation error instead of an IntegrityError check it here first."""
    queryset = model.objects.filter(club=club, name=name)
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude.pk)
    return queryset.exists()
