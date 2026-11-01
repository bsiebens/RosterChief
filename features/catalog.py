"""Features a club can have, defined in code.

Add a feature here together with the code that uses it, then run ``manage.py sync_features`` (also runs after
migrations). ``kind="flag"`` features are on/off; ``kind="limit"`` features carry a number (``None`` = unlimited).

Which clubs get which feature is data, managed by the platform: plans (``PlanFeature``) and per-club overrides.
"""

from dataclasses import dataclass

FLAG = "flag"
LIMIT = "limit"


@dataclass(frozen=True)
class FeatureDef:
    code: str
    name: str
    description: str = ""
    kind: str = FLAG
    # Limit features that cap the same thing share a scope; a plan may contain at most one feature per scope.
    limit_scope: str | None = None


CATALOG: dict[str, FeatureDef] = {
    f.code: f
    for f in [
        FeatureDef("sponsoring", "Sponsor management", "Track sponsors and sponsorship deals."),
        FeatureDef("max_members", "Member limit", "Maximum number of members on the roster.", kind=LIMIT, limit_scope="members"),
        # One feature per competition a club can import fixtures from (see club.Competition.feature).
        FeatureDef("competition_rbihf", "RBIHF fixtures", "Import fixtures from the RBIHF competition."),
        FeatureDef("competition_dfel", "DFEL fixtures", "Import fixtures from the DFEL competition."),
        FeatureDef("competition_ijnl", "IJNL fixtures", "Import fixtures from the IJNL competition."),
    ]
}

# Plans seeded when missing. After seeding they are edited in the platform admin (this is never re-applied).
# plan code: (name, {feature code: limit}); a flag uses None, a limit of None means unlimited.
DEFAULT_PLAN = "free"
DEFAULT_PLANS: dict[str, tuple[str, dict[str, int | None]]] = {
    "free": ("Free", {"max_members": 50}),
    "pro": ("Pro", {"sponsoring": None, "max_members": None}),
}


def duplicate_limit_scopes(codes) -> set[str]:
    """Scopes (e.g. "members") that more than one of the given feature codes would limit."""
    seen, dupes = set(), set()
    for code in codes:
        scope = CATALOG[code].limit_scope if code in CATALOG else None
        if scope is None:
            continue
        (dupes if scope in seen else seen).add(scope)
    return dupes
