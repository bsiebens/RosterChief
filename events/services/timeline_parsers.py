"""Turning each competition's own per-game payload into the one normalized
timeline document (GAME_TIMELINE.md §2.2). Pure functions -- no network, no
database -- so they're tested directly against saved real payloads
(events/test_fixtures/game_timeline/).

Every parser returns a document with ``source``, ``events`` (chronological) and
whatever else the source knows (``score``, ``periods``, ``decided_in``,
``team_stats``, ``status``); ``finalize_document`` fills the gaps the same way
for all of them (score and period scores from the goals, ``version``).

``side`` is always "home"/"away" exactly as the source has it -- "ours" is only
worked out when the timeline is shown (Event.is_home_game), never stored.
"""

import json
import re

from bs4 import BeautifulSoup

VERSION = 1

#: Regular periods before anything counts as overtime.
REGULAR_PERIODS = 3
#: Period length when a source doesn't tell us (RBIHF/CEHL clocks are cumulative).
DEFAULT_PERIOD_SECONDS = 20 * 60

#: IIHF penalty codes, as RBIHF's own game page spells them out -- shared so a
#: source that only sends the code (hockeydata) still gets a description.
PENALTY_DESCRIPTIONS = {
    "ABUS": "Abuse Of Officials",
    "BECH": "Bench Minor",
    "BOAR": "Boarding",
    "BRST": "Broken Stick",
    "BUTE": "Butt Ending",
    "CHAR": "Charging",
    "CHFB": "Checking From Behind",
    "CHHE": "Checking To The Head",
    "CLIP": "Clipping",
    "CROS": "Cross Checking",
    "DEGA": "Delaying The Game",
    "DIVE": "Diving Embellishment",
    "ELBO": "Elbowing",
    "FAOP": "Falling On The Puck",
    "FIGH": "Fighting",
    "HEAD": "Headbutting",
    "HIST": "High Sticking",
    "HOLD": "Holding",
    "HOOK": "Hooking",
    "HOPU": "Holding The Puck",
    "HOST": "Holding Opponents Stick",
    "ILEQ": "Illegal Equipment",
    "ILLH": "Illegal Hit",
    "INTE": "Interference",
    "INTG": "Interference On The Goalie",
    "KICK": "Kicking",
    "KNEE": "Kneeing",
    "PENS": "Penalty Shot",
    "PLBE": "Players Left The Bench",
    "PYAB": "Physical Abuse Of Official",
    "ROUG": "Roughing",
    "SLAS": "Slashing",
    "SLEW": "Slew Footing",
    "SPEA": "Spearing",
    "TOMO": "Too Many Players",
    "TRIP": "Tripping",
    "TROW": "Throwing The Stick",
    "UNSP": "Unsportsmanlike Conduct",
}

#: The canonical penalty kinds (``penalty.kind`` in the document) and the minutes
#: each is recorded with -- for sources that name the kind but not the length.
PENALTY_MINUTES = {"minor": 2, "bench_minor": 2, "double_minor": 4, "major": 5, "misconduct": 10, "game_misconduct": 20, "match": 25}
#: ...and the other way round, for sources that only give the length (IJNL, hockeydata).
_KIND_FOR_MINUTES = {2: "minor", 4: "double_minor", 5: "major", 10: "misconduct", 20: "game_misconduct", 25: "match"}
#: Kinds that mean more than a couple of minutes in the box -- highlighted on the
#: event page, and worth calling out to an API consumer.
SERIOUS_PENALTIES = {"major", "misconduct", "game_misconduct", "match"}

CLOCK_RE = re.compile(r"^(\d{1,3}):(\d{2})$")
PLAYER_RE = re.compile(r"^(?P<name>.*?)\s*\(#(?P<number>\d*)\)\s*$")
SCORE_RE = re.compile(r"(\d+)\s*[-:\u2013]\s*(\d+)")  # \u2013 = en dash: IJNL writes period scores with one
# "\s*": the suffix is a <sup> ("2<sup>nd</sup> period"), which text extraction splits off with a space.
ORDINAL_RE = re.compile(r"(\d+)\s*(?:st|nd|rd|th)\s+period", re.IGNORECASE)


class TimelineParseError(Exception):
    """A payload didn't look like that source's timeline."""


# --- shared helpers ---------------------------------------------------------


def normalize_penalty_kind(text: str) -> str:
    """A source's own wording ("minor", "Game Misconduct", RBIHF's own typo "game
    miscodunct") -> one of PENALTY_MINUTES' keys, or "" when it can't tell."""
    words = re.sub(r"[^a-z]+", " ", (text or "").lower()).strip()
    if not words:
        return ""
    if "match" in words:
        return "match"
    if "game" in words and "mis" in words:
        return "game_misconduct"
    if "mis" in words:
        return "misconduct"
    if "double" in words:
        return "double_minor"
    if "bench" in words:
        return "bench_minor"
    if "major" in words:
        return "major"
    if "minor" in words:
        return "minor"
    return ""


def finish_penalty(penalty: dict, kind_text: str = "") -> dict:
    """Fills ``kind`` and ``minutes`` from whichever of the two the source gave."""
    kind = normalize_penalty_kind(kind_text) or penalty.get("kind") or ""
    if not kind and penalty.get("minutes") in _KIND_FOR_MINUTES:
        kind = _KIND_FOR_MINUTES[penalty["minutes"]]
    if kind:
        penalty["kind"] = kind
        penalty.setdefault("minutes", PENALTY_MINUTES[kind])
    else:
        penalty.pop("kind", None)
    return penalty


def penalty_description(code: str) -> str:
    """English description for an IIHF penalty code; tolerant of "HI-ST"-style spellings."""
    return PENALTY_DESCRIPTIONS.get(re.sub(r"[^A-Z]", "", (code or "").upper()), "")


def clock_seconds(text: str) -> int | None:
    match = CLOCK_RE.match((text or "").strip())
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def clock_text(seconds: int | None) -> str | None:
    return None if seconds is None else f"{seconds // 60:02d}:{seconds % 60:02d}"


def period_label(number: int) -> str:
    return str(number) if number <= REGULAR_PERIODS else "OT" if number == REGULAR_PERIODS + 1 else f"OT{number - REGULAR_PERIODS}"


def period_for(seconds: int | None, period_seconds: int = DEFAULT_PERIOD_SECONDS) -> str | None:
    """The period a cumulative game-clock time falls in. A time exactly on a
    boundary (40:00) belongs to the period that just ended."""
    if seconds is None:
        return None
    return period_label(max(1, -(-seconds // period_seconds)))


def parse_score(text: str) -> dict | None:
    match = SCORE_RE.search(text or "")
    return {"home": int(match.group(1)), "away": int(match.group(2))} if match else None


def player(name: str, number=None) -> dict:
    result = {"name": re.sub(r"\s+", " ", (name or "")).strip()}
    if number not in (None, ""):
        try:
            result["number"] = int(number)
        except TypeError, ValueError:
            pass
    return result


def player_from_text(text: str) -> dict:
    """ "Name (#19)" -> {"name": "Name", "number": 19}; "(#)" (no roster sent) -> name only."""
    match = PLAYER_RE.match((text or "").strip())
    if not match:
        return player(text)
    return player(match.group("name"), match.group("number"))


def with_ids(events: list[dict], source: str) -> list[dict]:
    """Synthesizes stable ids for sources that have none (RBIHF, CEHL):
    type/side/time/player, plus a counter for identical tuples in source order.
    An id a parser already set (hockeydata guid, IJNL key) is kept."""
    seen = {}
    for event in events:
        if event.get("id"):
            continue
        who = ""
        detail = event.get(event["type"]) or {}
        person = detail.get("scorer") or detail.get("player")
        if person:
            who = str(person.get("number") or re.sub(r"[^a-z0-9]+", "-", person.get("name", "").lower()).strip("-"))
        base = f"{source.lower()}:{event['type']}:{event.get('side') or '-'}:{event.get('seconds') if event.get('seconds') is not None else '-'}:{who}"
        seen[base] = seen.get(base, -1) + 1
        event["id"] = f"{base}:{seen[base]}"
    return events


#: Order of events that share one clock time (40:00): the period that just
#: ended, then the next one starting, then whatever happened at that moment
#: (e.g. a goalie change for the new period).
_SAME_SECOND_ORDER = {"period_end": 0, "period_start": 1}


def chronological(events: list[dict], *, newest_first: bool = False) -> list[dict]:
    """Oldest first. ``newest_first`` says the source lists events newest first
    (RBIHF, CEHL), so its order is reversed before sorting -- the stable sort
    then keeps same-second events in their real order; period markers at the
    same second go first (_SAME_SECOND_ORDER)."""
    ordered = list(reversed(events)) if newest_first else list(events)
    return sorted(ordered, key=lambda event: (event.get("seconds") if event.get("seconds") is not None else -1, _SAME_SECOND_ORDER.get(event["type"], 2)))


def finalize_document(document: dict) -> dict:
    """Fills what every source leaves to be derived: the score and per-period
    scores from the goals when the source didn't give them, and ``version``."""
    goals = [event for event in document.get("events", []) if event["type"] == "goal"]
    if "score" not in document:
        last = next((goal["goal"].get("score_after") for goal in reversed(goals) if goal.get("goal", {}).get("score_after")), None)
        document["score"] = last or {"home": sum(goal["side"] == "home" for goal in goals), "away": sum(goal["side"] == "away" for goal in goals)}
    if "periods" not in document:
        # Regular periods first (all three once anything has happened, so a
        # scoreless period still shows 0-0), then overtime as it occurs.
        periods = {}
        if document.get("events"):
            for number in range(1, REGULAR_PERIODS + 1):
                periods[period_label(number)] = {"label": period_label(number), "home": 0, "away": 0}
        for goal in goals:
            if goal.get("side") not in ("home", "away") or not goal.get("period"):
                continue
            periods.setdefault(goal["period"], {"label": goal["period"], "home": 0, "away": 0})[goal["side"]] += 1
        document["periods"] = list(periods.values())
    document["version"] = VERSION
    return document


# --- RBIHF ------------------------------------------------------------------

RBIHF_GD_RE = re.compile(r"updateTimeline\((\{.*?\})\);</script>", re.DOTALL)


def rbihf_extract_gd(game_page_html: str) -> dict:
    """The object RBIHF's game page hands to updateTimeline(...) -- game number,
    season, both rosters, penalty codes. timeline.php needs it posted back to
    fill in player names."""
    match = RBIHF_GD_RE.search(game_page_html or "")
    if not match:
        raise TimelineParseError("No timeline data on the RBIHF game page.")
    try:
        return json.loads(match.group(1))
    except ValueError as error:
        raise TimelineParseError("The RBIHF game page's timeline data isn't valid JSON.") from error


def rbihf_form_fields(gd: dict) -> list[tuple[str, str]]:
    """``gd`` flattened into jQuery-style nested form fields (gd[home][team][18438][nr]=35)."""
    fields = []

    def walk(prefix, value):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(f"{prefix}[{key}]", item)
        else:
            fields.append((prefix, "" if value is None else str(value)))

    walk("gd", gd)
    return fields


def parse_rbihf(timeline_json: str, gd: dict) -> dict:
    try:
        payload = json.loads(timeline_json)
    except ValueError as error:
        raise TimelineParseError("The RBIHF timeline wasn't valid JSON.") from error
    if not isinstance(payload, dict) or not payload.get("success"):
        raise TimelineParseError("RBIHF returned no timeline for this game.")

    sides = {code: side for side, code in (("home", (gd.get("home") or {}).get("code")), ("away", (gd.get("visit") or {}).get("code"))) if code}
    team_names = {name for name in ((gd.get("home") or {}).get("name"), (gd.get("visit") or {}).get("name")) if name}
    penalty_codes = {**PENALTY_DESCRIPTIONS, **(gd.get("penCode") or {})}
    soup = BeautifulSoup(payload.get("data") or "", "html.parser")
    period_seconds = DEFAULT_PERIOD_SECONDS
    events = []

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        kind = (row.get("class") or [""])[0]
        seconds = clock_seconds(cells[0].get_text(strip=True))
        label_lines = [line.strip() for line in cells[1].get_text("\n", strip=True).split("\n") if line.strip()]
        side = sides.get(cells[2].get_text(strip=True))
        note = cells[3]
        spans = note.find_all("span")
        note_text = note.get_text(" ", strip=True)
        base = {"seconds": seconds, "time": clock_text(seconds), "side": side}

        if kind == "goal":
            score_span = note.find("span", class_="highlight")
            people = [span for span in spans if "highlight" not in (span.get("class") or [])]
            goal = {"scorer": player_from_text(people[0].get_text(strip=True)) if people else player(""), "assists": [player_from_text(span.get_text(strip=True)) for span in people[1:]]}
            if len(label_lines) > 1:
                goal["strength"] = label_lines[1]
                goal["empty_net"] = label_lines[1] == "EN"
                goal["penalty_shot"] = label_lines[1] == "PS"
            score = parse_score(score_span.get_text(strip=True)) if score_span else None
            if score:
                goal["score_after"] = score
            events.append({**base, "type": "goal", "goal": goal})
        elif kind == "penalty" and label_lines and label_lines[0].lower().startswith("penalty shot"):
            # "<team> was awarded a penalty shot" -- the side it went to is the team cell.
            events.append({**base, "type": "penalty_shot"})
        elif kind == "penalty":
            kind_span = note.find("span", class_="highlight")
            code = label_lines[1] if len(label_lines) > 1 else ""
            who = spans[0].get_text(strip=True) if spans else ""
            # A team-level call ("<team> was given a penalty") names the team, not a player.
            offender = None if who in team_names else player_from_text(who)
            penalty = {"player": offender, "code": code, "description": penalty_codes.get(code) or penalty_description(code)}
            finish_penalty(penalty, kind_span.get_text(strip=True) if kind_span else "")
            events.append({**base, "type": "penalty", "penalty": penalty})
        elif kind == "goalie":
            action = "in" if "in" in label_lines[0].lower().split() else "out"
            events.append({**base, "type": "goalie_change", "goalie_change": {"player": player_from_text(spans[0].get_text(strip=True)) if spans else player(""), "action": action}})
        elif kind == "time-out":
            events.append({**base, "type": "timeout"})
        elif kind == "periods":
            ordinal = ORDINAL_RE.search(note_text)
            number = int(ordinal.group(1)) if ordinal else None
            ended = "ended" in note_text.lower()
            if ended and number == 1 and seconds:
                period_seconds = seconds
            events.append({**base, "side": None, "type": "period_end" if ended else "period_start", "period": period_label(number) if number else None})

    for event in events:
        event.setdefault("period", None)
        if event["period"] is None:
            event["period"] = period_for(event["seconds"], period_seconds)
    events = with_ids(chronological(events, newest_first=True), "rbihf")
    return {"source": "RBIHF", "events": events}


# --- CEHL -------------------------------------------------------------------


def parse_cehl(timeline_json: str) -> dict:
    try:
        payload = json.loads(timeline_json)
    except ValueError as error:
        raise TimelineParseError("The CEHL timeline wasn't valid JSON.") from error
    if not isinstance(payload, dict) or "html" not in payload:
        raise TimelineParseError("CEHL returned no timeline for this game.")

    soup = BeautifulSoup(payload.get("html") or "", "html.parser")
    period_seconds = DEFAULT_PERIOD_SECONDS
    events = []
    for node in soup.select("div.timeline-event"):
        classes = set(node.get("class") or []) - {"timeline-event"}
        side = "home" if "team-a" in classes else "away" if "team-b" in classes else None
        content = node.select_one(".timeline-event-content")
        if content is None:
            continue
        stamp = content.find("span")
        stamp_lines = [line.strip() for line in stamp.get_text("\n", strip=True).split("\n")] if stamp else []
        seconds = clock_seconds(stamp_lines[0]) if stamp_lines else None
        names = [b.get_text(strip=True) for b in content.find_all("b", recursive=False) if b.get_text(strip=True)]
        text = content.get_text(" ", strip=True).replace("\xa0", " ")
        base = {"seconds": seconds, "time": clock_text(seconds), "side": side}

        if "goal" in classes:
            goal = {"scorer": player(names[0] if names else "")}
            assists = re.search(r"assist of (.+)$", text)
            goal["assists"] = [player(name) for name in re.split(r"\s+and\s+|,\s*", assists.group(1))] if assists else []
            score_node = node.select_one(".timeline-event-score")
            score = parse_score(score_node.get_text(strip=True) if score_node else text)
            if score:
                goal["score_after"] = score
            events.append({**base, "type": "goal", "goal": goal})
        elif "penalty" in classes and "awarded a penalty shot" in text.lower():
            events.append({**base, "type": "penalty_shot"})
        elif "penalty" in classes:
            code = stamp_lines[1] if len(stamp_lines) > 1 else ""
            kind_node = content.find("i")
            description = re.search(r"penalty for (.+)$", text)
            penalty = {"player": player(names[0] if names else ""), "code": code, "description": description.group(1).strip() if description else penalty_description(code)}
            finish_penalty(penalty, kind_node.get_text(strip=True) if kind_node else "")
            events.append({**base, "type": "penalty", "penalty": penalty})
        elif "enter" in classes or "leave" in classes or "exit" in classes:
            action = "in" if "enter" in classes else "out"
            events.append({**base, "type": "goalie_change", "goalie_change": {"player": player(names[0] if names else ""), "action": action}})
        elif "clock" in classes:
            events.append({**base, "type": "timeout"})
        elif "period" in text.lower():
            ordinal = ORDINAL_RE.search(text)
            number = int(ordinal.group(1)) if ordinal else None
            ended = "ended" in text.lower()
            if ended and number == 1 and seconds:
                period_seconds = seconds
            events.append({**base, "side": None, "type": "period_end" if ended else "period_start", "period": period_label(number) if number else None})

    for event in events:
        event.setdefault("period", None)
        if event["period"] is None:
            event["period"] = period_for(event["seconds"], period_seconds)
    events = with_ids(chronological(events, newest_first=True), "cehl")
    return {"source": "CEHL", "events": events}


# --- DFEL (hockeydata GetGameReport) ----------------------------------------


def _hd_player(data) -> dict:
    if not isinstance(data, dict):
        return player("")
    name = " ".join(part for part in (data.get("playerFirstname"), data.get("playerMiddlename"), data.get("playerLastname")) if part)
    return player(name, data.get("playerJerseyNr"))


def _hd_period(value) -> str | None:
    text = str(value or "").strip()
    if text.isdigit():
        return period_label(int(text))
    return text.upper() or None


def _hd_base(item: dict, side: str, *, id_key: str | None = None) -> dict:
    """The fields every hockeydata event shares; ``gametime`` is spelled two ways."""
    seconds = item.get("gameTime", item.get("gametime"))
    base = {"side": side, "seconds": seconds, "time": item.get("gameTimeFormatted") or clock_text(seconds), "period": _hd_period(item.get("gameTimePeriod"))}
    if id_key and item.get(id_key):
        base["id"] = f"hd:{item[id_key]}"
    return base


def parse_hockeydata(report_json: str) -> dict:
    try:
        payload = json.loads(report_json)
    except ValueError as error:
        raise TimelineParseError("The hockeydata game report wasn't valid JSON.") from error
    if not isinstance(payload, dict) or payload.get("statusId") != 1 or not isinstance(payload.get("data"), dict):
        message = payload.get("statusMsg", "") if isinstance(payload, dict) else ""
        raise TimelineParseError(f"hockeydata returned no game report: {message or 'unknown error'}")

    data = payload["data"]
    events = []
    for side in ("home", "away"):
        for goal in data.get(f"{side}Goals") or []:
            detail = {
                "scorer": _hd_player(goal.get("scoredBy")),
                "assists": [_hd_player(goal[key]) for key in ("assistBy", "assist2By") if goal.get(key)],
                "strength": goal.get("gameStrength") or None,
                "empty_net": bool(goal.get("isEmptyNet")),
                "penalty_shot": bool(goal.get("isPenaltyShot")),
                "game_winning": bool(goal.get("isGameWinningGoal")),
            }
            score = parse_score(goal.get("newScore") or "")
            if score:
                detail["score_after"] = score
            events.append({**_hd_base(goal, side, id_key="guid"), "type": "goal", "goal": detail})
        for penalty in data.get(f"{side}Penalties") or []:
            length = penalty.get("penaltyLength")
            code = penalty.get("offence") or ""
            detail = {"player": _hd_player(penalty.get("offender")), "code": code, "description": penalty.get("offenceReason") or penalty_description(code)}
            if isinstance(length, int | float):
                detail["minutes"] = int(length // 60)
            finish_penalty(detail)
            if penalty.get("servedBy"):
                detail["served_by"] = _hd_player(penalty["servedBy"])
            events.append({**_hd_base(penalty, side, id_key="guid"), "type": "penalty", "penalty": detail})
        for change in data.get(f"{side}GoalKeeperChanges") or []:
            action = "in" if str(change.get("action", "")).lower() in ("on", "in") else "out"
            events.append({**_hd_base(change, side), "type": "goalie_change", "goalie_change": {"player": _hd_player(change.get("player")), "action": action}})

    events = with_ids(chronological(events), "hd")

    game = data.get("gameData") or {}
    document = {"source": "DFEL", "events": events}
    if isinstance(game.get("homeTeamScore"), int) and isinstance(game.get("awayTeamScore"), int):
        document["score"] = {"home": game["homeTeamScore"], "away": game["awayTeamScore"]}
    if game.get("isShootOut"):
        document["decided_in"] = "shootout"
    elif game.get("isOvertime"):
        document["decided_in"] = "overtime"

    stats = {}
    for side in ("home", "away"):
        team = data.get(f"{side}TeamStats") or {}
        if team:
            stats[side] = {
                "shots_on_goal": team.get("shotsOnGoal"),
                "penalty_minutes": team.get("penaltyMinutes"),
                "pp_goals": team.get("powerplayGoals"),
                "pp_opportunities": team.get("powerplayOpportunities"),
                "faceoffs_won": team.get("faceoffsWon"),
            }
    if stats:
        document["team_stats"] = stats
    return document


# --- IJNL -------------------------------------------------------------------

IJNL_STATES = {"gepland": "scheduled", "live": "live", "definitief": "final"}
IJNL_PERIODS = {"verlenging": "OT", "shootout": "SO", "strafschoten": "SO", "penalty's": "SO"}


def _ijnl_period(label: str) -> str | None:
    text = (label or "").strip().lower()
    number = re.search(r"\d+", text)
    if number and "periode" in text:
        return period_label(int(number.group(0)))
    return next((value for key, value in IJNL_PERIODS.items() if key in text), text.upper() or None)


def _ijnl_player(info) -> dict:
    if info is None:
        return player("")
    name = info.find("strong")
    number = info.find("small")
    return player(name.get_text(strip=True) if name else "", (number.get_text(strip=True).lstrip("#") if number else None))


def _ijnl_numbers(lineups_html: str) -> dict:
    """{"home"|"away": {player page link or lower-cased name: jersey number}} from the lineups tab. The
    timeline names the assists as bare links without a number, which the roster tables do give."""
    numbers = {"home": {}, "away": {}}
    cards = BeautifulSoup(lineups_html or "", "html.parser").select(".mp-lineup-grid > .mp-card")
    for side, card in zip(("home", "away"), cards, strict=False):
        for row in card.select("tbody tr"):
            link, cells = row.select_one("a.mp-roster-player"), row.find_all("td")
            number = cells[0].get_text(strip=True) if cells else ""
            if link is None or not number.isdigit():
                continue
            numbers[side][link.get("href", "")] = int(number)
            name = link.find_all("span", recursive=False)[-1].get_text(" ", strip=True) if link.find("span", recursive=False) else link.get_text(" ", strip=True)
            numbers[side][_name_key(name)] = int(number)
    return numbers


def _name_key(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().lower()


def _ijnl_team_stats(scoreboard) -> dict:
    """The scoreboard's "Teamstatistieken" row ("0/3 Powerplay 0/3", "6 Strafminuten 6") as the
    document's ``team_stats``: goals / opportunities and penalty minutes per side."""
    stats = {"home": {}, "away": {}}
    for span in scoreboard.select(".mp-score-stats > span"):
        values, label = span.find_all("b"), span.find("small")
        if len(values) != 2 or label is None:
            continue
        name = label.get_text(strip=True).lower()
        for side, value in zip(("home", "away"), values, strict=True):
            text = value.get_text(strip=True)
            if name == "powerplay" and (match := re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)):
                stats[side]["pp_goals"], stats[side]["pp_opportunities"] = int(match.group(1)), int(match.group(2))
            elif name == "strafminuten" and text.isdigit():
                stats[side]["penalty_minutes"] = int(text)
    return {side: values for side, values in stats.items() if values}


def parse_ijnl(game_json: str) -> dict:
    """From an IJNL game ``/data`` response (``part=live`` or core): the
    timeline section, plus the scoreboard's per-period scores, team statistics and
    the state, and the lineups' jersey numbers for the assists."""
    try:
        payload = json.loads(game_json)
    except ValueError as error:
        raise TimelineParseError("The IJNL game data wasn't valid JSON.") from error
    if not isinstance(payload, dict):
        raise TimelineParseError("The IJNL game data had an unexpected shape.")

    sections = payload.get("sections") or {}
    if "timeline" in sections:
        timeline_html, scoreboard_html, lineups_html = sections.get("timeline") or "", sections.get("scoreboard") or "", sections.get("lineups") or ""
    else:
        timeline_html = scoreboard_html = lineups_html = payload.get("html") or ""
    soup = BeautifulSoup(timeline_html, "html.parser")
    numbers = _ijnl_numbers(lineups_html)

    events = []
    for item in soup.select("li.mp-event"):
        classes = item.get("class") or []
        side = "home" if "mp-home" in classes else "away" if "mp-away" in classes else None
        kind = item.get("data-event")
        time_node = item.select_one(".mp-time")
        seconds = clock_seconds(time_node.get_text(strip=True) if time_node else "")
        group = item.find_parent("ol")
        period = _ijnl_period(group.get("data-period-events")) if group is not None and group.get("data-period-events") else period_for(seconds)
        info = item.select_one(".mp-event-info")
        line = info.find("p") if info else None
        base = {"id": f"ijnl:{item['data-event-key']}" if item.get("data-event-key") else None, "seconds": seconds, "time": clock_text(seconds), "period": period, "side": side}

        if kind == "goal":
            badges = {badge.get_text(strip=True).upper() for badge in item.select("abbr.mp-badge")}
            known = numbers.get(side or "", {})
            assists = [player(link.get_text(strip=True), known.get(link.get("href", ""), known.get(_name_key(link.get_text(strip=True))))) for link in (line.find_all("a") if line else [])]
            goal = {"scorer": _ijnl_player(info), "assists": assists}
            strength = next((badge for badge in ("PP2", "PP1", "PP", "SH2", "SH1", "SH", "PS") if badge in badges), None)
            goal["strength"] = {"PP": "PP1", "SH": "SH1"}.get(strength, strength) if strength else "EQ"
            goal["empty_net"] = "EN" in badges
            goal["penalty_shot"] = "PS" in badges
            goal["game_winning"] = "GWG" in badges
            score_node = item.select_one(".mp-event-score")
            score = parse_score(score_node.get_text(strip=True) if score_node else "")
            if score:
                goal["score_after"] = score
            events.append({**base, "type": "goal", "goal": goal})
        elif kind == "penalty":
            text = line.get_text(" ", strip=True) if line else ""
            minutes = re.search(r"(\d+)\s*min", text)
            description = re.split(r",\s*\d+\s*min", text)[0].strip()
            penalty = {"player": _ijnl_player(info), "description": description}
            if minutes:
                penalty["minutes"] = int(minutes.group(1))
            finish_penalty(penalty, description)
            events.append({**base, "type": "penalty", "penalty": penalty})
        elif kind == "goalie":
            text = line.get_text(" ", strip=True).lower() if line else ""
            events.append({**base, "type": "goalie_change", "goalie_change": {"player": _ijnl_player(info), "action": "out" if "uit" in text or "out" in text else "in"}})
        elif kind in ("timeout", "time-out"):
            events.append({**base, "type": "timeout"})

    events = [{key: value for key, value in event.items() if not (key == "id" and value is None)} for event in events]
    events = with_ids(chronological(events), "ijnl")
    document = {"source": "IJNL", "events": events}

    scoreboard = BeautifulSoup(scoreboard_html, "html.parser")
    periods = []
    for span in scoreboard.select(".mp-periods > span"):
        label = span.find("small")
        score = parse_score(span.get_text(" ", strip=True).replace(label.get_text(strip=True), "", 1) if label else span.get_text(" ", strip=True))
        if label and score:
            name = label.get_text(strip=True).upper()
            periods.append({"label": name[1:] if name.startswith("P") and name[1:].isdigit() else name, **score})
    if periods:
        document["periods"] = periods
    score_spans = scoreboard.select(".mp-score > span")
    if len(score_spans) == 2 and all(span.get_text(strip=True).isdigit() for span in score_spans):
        document["score"] = {"home": int(score_spans[0].get_text(strip=True)), "away": int(score_spans[1].get_text(strip=True))}
    if team_stats := _ijnl_team_stats(scoreboard):
        document["team_stats"] = team_stats
    if payload.get("state") in IJNL_STATES:
        document["status"] = IJNL_STATES[payload["state"]]
    return document
