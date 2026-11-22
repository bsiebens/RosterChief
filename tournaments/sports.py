"""Sport specific lists for the game timeline. Only ice hockey has one so far: its penalty codes and the usual penalty lengths."""

import re

#: Penalty codes as they appear on an ice hockey game sheet: (code, name). The code is what staff type or pick, the name explains it.
ICE_HOCKEY_PENALTIES: list[tuple[str, str]] = [
    ("ABU", "Abuse of officials"),
    ("BEN", "Bench minor"),
    ("BOA", "Boarding"),
    ("BUT", "Butt-ending"),
    ("CBH", "Checking from behind"),
    ("CHA", "Charging"),
    ("CHN", "Check to the head or neck"),
    ("CRO", "Cross-checking"),
    ("DEL", "Delaying the game"),
    ("DIV", "Diving / embellishment"),
    ("ELB", "Elbowing"),
    ("EQU", "Illegal equipment"),
    ("FIG", "Fighting"),
    ("GMC", "Game misconduct"),
    ("HBT", "Head-butting"),
    ("HIG", "High-sticking"),
    ("HOK", "Hooking"),
    ("HOL", "Holding"),
    ("HST", "Holding the stick"),
    ("INT", "Interference"),
    ("KIC", "Kicking"),
    ("KNE", "Kneeing"),
    ("MAT", "Match penalty"),
    ("MIS", "Misconduct"),
    ("PHM", "Playing without a helmet"),
    ("ROU", "Roughing"),
    ("SLA", "Slashing"),
    ("SPE", "Spearing"),
    ("TMP", "Too many players on the ice"),
    ("TRI", "Tripping"),
    ("UNS", "Unsportsmanlike conduct"),
]

#: The usual lengths of a penalty, as shown: 2 (minor), 2+2 (double minor), 5 (major), 10 (misconduct) and 20 (game misconduct).
PENALTY_LENGTHS = ["2", "2+2", "5", "10", "20"]


def penalty_minutes(length: str) -> int:
    """The minutes a penalty length adds to a team's penalty total: "2+2" is 4."""
    return sum(int(part) for part in length.split("+"))


def is_ice_hockey(sport) -> bool:
    """Whether a ``club.Sport`` (or ``None``) is ice hockey: its name or slug mentions hockey."""
    return bool(sport and re.search(r"hockey", f"{sport.name} {sport.slug}", re.IGNORECASE))
