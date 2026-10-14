"""Fixture helper shared by mobile/test_coach_ops.py."""

from rosterchief.test_support import make_club as _make_club


def make_club(**kwargs):
    return _make_club(secondary_color="#e4002b", **kwargs)
