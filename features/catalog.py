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
        FeatureDef("referee_officials", "Referees and officials", "Use of referee and officials management"),
        FeatureDef("shop", "Shop", "Enables the club shop"),
        FeatureDef("analytics", "Analytics", "Enables analytics for games played based on official gamesheets"),
        # One feature per competition a club can import fixtures from (see club.Competition.feature).
        FeatureDef("competition_rbihf", "RBIHF fixtures", "Import fixtures from the RBIHF competition."),
        FeatureDef("competition_dfel", "DFEL fixtures", "Import fixtures from the DFEL competition."),
        FeatureDef("competition_ijnl", "IJNL fixtures", "Import fixtures from the IJNL competition."),
    ]
}


def all_access_features() -> dict[str, int | None]:
    """Every catalog feature with no limit: flags on, limit features unlimited (``None``).

    A plan may hold at most one limit feature per scope (see ``duplicate_limit_scopes``), so if two features ever cap the
    same thing the first one in the catalog wins.
    """
    seen_scopes: set[str] = set()
    features: dict[str, int | None] = {}
    for code, definition in CATALOG.items():
        if definition.limit_scope:
            if definition.limit_scope in seen_scopes:
                continue
            seen_scopes.add(definition.limit_scope)
        features[code] = None
    return features


# Plans seeded when missing. After seeding they are edited in the platform admin (this is never re-applied).
# plan code: (name, {feature code: limit}); a flag uses None, a limit of None means unlimited.
DEFAULT_PLAN = "free"
ALL_ACCESS_PLAN = "all_access"
DEFAULT_PLANS: dict[str, tuple[str, dict[str, int | None]]] = {
    "free": ("Free", {"max_members": 50}),
    ALL_ACCESS_PLAN: ("All access", all_access_features()),
}

# Plans that automatically pick up every new catalog feature (without limits) the next time the catalog is synced.
# Only missing features are added: an admin can still set a limit on one, or the plan's other rows, without it being undone.
AUTO_FEATURE_PLANS = frozenset({ALL_ACCESS_PLAN})


def duplicate_limit_scopes(codes) -> set[str]:
    """Scopes (e.g. "members") that more than one of the given feature codes would limit."""
    seen, dupes = set(), set()
    for code in codes:
        scope = CATALOG[code].limit_scope if code in CATALOG else None
        if scope is None:
            continue
        (dupes if scope in seen else seen).add(scope)
    return dupes
