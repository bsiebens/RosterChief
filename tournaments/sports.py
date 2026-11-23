"""Sport specific lists for the game timeline. Only ice hockey has one so far: its penalty codes and the usual penalty lengths."""

import re

#: The IIHF abbreviations for offences, as printed on the game sheet: (abbreviation, offence). Taken from ANNEX 6 - Abbreviations for Offences
#: of the IIHF Off-Ice Officials' Handbook (October 2014, as published by the Hungarian league at
#: https://www.ersteliga.hu/_upload/doclib/25/2014-15_Off-Ice_Official_Handbook_-_V3.pdf). That is the newest edition of the annex that could be
#: reached: compare it with the current IIHF handbook / your federation's game sheet before relying on it, and edit this list if they differ.
ICE_HOCKEY_PENALTIES: list[tuple[str, str]] = [
    ("ABUSE", "Abuse of officials"),
    ("BENCH", "Bench minor"),
    ("BITE", "Biting"),
    ("BOARD", "Boarding"),
    ("BR-ST", "Broken stick"),
    ("BUT-E", "Butt-ending"),
    ("CHARG", "Charging"),
    ("CHE-B", "Checking from behind"),
    ("CHE-H", "Checking to the head or neck"),
    ("CLIPP", "Clipping"),
    ("CLOS", "Closing hand on puck"),
    ("CROSS", "Cross-checking"),
    ("DANG", "Dangerous equipment"),
    ("DELAY", "Delay of game"),
    ("EMBEL", "Diving or embellishment"),
    ("ELBOW", "Elbowing"),
    ("ENG-S", "Engaging with spectators"),
    ("FIGHT", "Fighting"),
    ("H-BUT", "Head-butting"),
    ("HI-ST", "High-sticking"),
    ("HOLD", "Holding"),
    ("HO-ST", "Holding the stick"),
    ("HOOK", "Hooking"),
    ("ILL-ST", "Illegal stick-stick measurement"),
    ("INCAC", "Incorrect access to or from penalty box"),
    ("INJUR", "Injured skater refusing to leave the ice"),
    ("INTRF", "Interference"),
    ("GK-INT", "Interference on a goaltender"),
    ("KICK", "Kicking"),
    ("KNEE", "Kneeing"),
    ("L-BCH", "Leaving the penalty box prematurely"),
    ("HELM", "Playing without a helmet"),
    ("PULL", "Pulling hair, helmet, cage"),
    ("REFUSE", "Refusing to start play"),
    ("ROUGH", "Roughing"),
    ("SLASH", "Slashing"),
    ("SLEW", "Slew-footing"),
    ("SPEAR", "Spearing"),
    ("SPIT", "Spitting"),
    ("TAUNT", "Taunting"),
    ("T-BCH", "Team official entering the playing area"),
    ("THR-ST", "Throwing a stick or object"),
    ("TOO-M", "Too many men"),
    ("TRIP", "Tripping"),
    ("UN-SP", "Unsportsmanlike conduct"),
    ("ILL-H", "Illegal hit (women)"),
]

#: The usual lengths of a penalty, as shown: 2 (minor), 2+2 (double minor), 5 (major), 10 (misconduct) and 20 (game misconduct).
PENALTY_LENGTHS = ["2", "2+2", "5", "10", "20"]


def penalty_minutes(length: str) -> int:
    """The minutes a penalty length adds to a team's penalty total: "2+2" is 4."""
    return sum(int(part) for part in length.split("+"))


def is_ice_hockey(sport) -> bool:
    """Whether a ``club.Sport`` (or ``None``) is ice hockey: its name or slug mentions hockey."""
    return bool(sport and re.search(r"hockey", f"{sport.name} {sport.slug}", re.IGNORECASE))


#: The fixed positions of an ice hockey player. Free text (a coach typing "centre" or "keeper") is mapped onto one of them.
HOCKEY_POSITIONS = ("Forward", "Defense", "Goalie")
_POSITION_ALIASES = {
    "Forward": {"forward", "forwards", "fw", "f", "wing", "winger", "left wing", "right wing", "lw", "rw", "center", "centre", "c", "attacker", "striker"},
    "Defense": {"defense", "defence", "defenseman", "defenceman", "defender", "d", "dm"},
    "Goalie": {"goalie", "goalies", "goalkeeper", "goaltender", "keeper", "g", "gk"},
}


def normalise_position(label: str | None) -> str:
    """The ice hockey position a free text label stands for ("Forward", "Defense" or "Goalie"), or "" when it is empty or unknown."""
    key = (label or "").strip().lower()
    return next((position for position, aliases in _POSITION_ALIASES.items() if key in aliases or key == position.lower()), "")
