# Game timelines — design

Storing what happens *during* a game — goals and who scored them, penalties, goalie changes,
time-outs, period results — next to the score we already keep, and using it for the member app
and (later) push notifications.

Today `poll_live_game_results` asks each game's competition for `is_live` + score and nothing
else (`../events/competition/hockey.py`). Every source we poll can give us much more than that, most
of it from the same request or one extra. This document records what each source offers, proposes
one normalized shape to store it in, and splits the work into phases.

Status: **all four phases implemented.** §7a–§7d cover what shipped and where it deviates from
this design. The §9 decisions are recorded there.

---

## 1. What each source actually exposes

Researched on 2026-10-04 against real, played games (RBIHF D1 #137/#131/#203, CEHL 2526/7101,
DFEL `c441c652-…`, IJNL `920c9dc5-…`).

|                         | RBIHF                                                                      | CEHL                                 | DFEL (hockeydata)                                                               | IJNL                                                                    |
|-------------------------|----------------------------------------------------------------------------|--------------------------------------|---------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| **Endpoint**            | `POST /modules/league/ajax/timeline.php`                                   | `GET /ajax/timeline.php?nr=&season=` | `GET api.hockeydata.net/data/ebel/GetGameReport?gameId=&apiKey=`                | `GET /competities/wedstrijd/<uuid>/data?part=live`                      |
| **Format**              | JSON wrapping an HTML `<table>`                                            | JSON wrapping HTML `<div>`s          | Real JSON                                                                       | JSON wrapping HTML sections                                             |
| **Requests per poll**   | 2: game page (embedded roster) + timeline POST. Roster cacheable per game. | 1 (`score.php` already called)       | 1, replacing today's team page + whole-division schedule                        | **0 extra**: the response we already fetch contains `sections.timeline` |
| **Goals**               | scorer, ≤2 assists, strength (`EQ`/`PP1`/`SH1`/`DP`…), score after         | scorer, assists, score after         | scorer, 2 assists, strength, empty-net, penalty shot, GWG/tying, players on ice | scorer, assists, GWG, score after                                       |
| **Penalties**           | player, code (`TRIP`…), type (`minor`/…), description                      | player, code, type, description      | offender, served by, code, reason, length, start/end time                       | player, description, minutes                                            |
| **Goalie changes**      | in/out                                                                     | —                                    | in/out                                                                          | in/out                                                                  |
| **Time-outs / periods** | yes / start + end markers                                                  | yes / start + end markers            | period results; game situations (PP/SH spans)                                   | period scores in scoreboard                                             |
| **Extras**              | penalty-code dictionary, full rosters                                      | —                                    | per-player and per-team stats (shots, faceoffs, PP/PK)                          | lineups section                                                         |
| **Stable event id**     | **none**: must synthesize                                                  | **none**: must synthesize            | **yes**: `guid` per goal/penalty                                                | **yes**: `data-event-key`                                               |
| **Home/away marker**    | team code → home/visit via the embedded roster                             | `team-a` / `team-b` class            | `homeGoals` / `awayGoals` lists                                                 | `mp-home` / `mp-away` class                                             |
| **Change marker**       | —                                                                          | `score.php` → `last` (unix ts)       | `lastChangeOfGameSetup` on schedule rows                                        | `updatedAt`                                                             |

Source quirks worth knowing before building parsers:

- **RBIHF needs the roster in the request.** `timeline.php` takes `gd` = the object the game page
  passes to `updateTimeline(...)` (game number, season, both rosters, penalty-code map), posted as
  jQuery-style nested form fields (`gd[home][team][18438][nr]=35`). With only
  `gd[gameNr]`/`gd[season]` the rows come back with empty names (`" (#)"`). So: scrape the game
  page once per game and cache that object (rosters don't change mid-game), then POST it each poll.
- **RBIHF and CEHL times are cumulative game-clock** (`44:31` = 4:31 into the 3rd), not
  per-period. Normalize to `period` + `seconds`.
- **CEHL rows carry no player numbers, and names come in inconsistent casing** (`MITCH MORGAN` vs.
  `Kevin Kallonen`). Store them as given; don't try to match people across sources.
- **hockeydata needs the API key that `dfel_import` already scrapes from the EHV-NRW team page.**
  Cache it (a day is plenty) instead of fetching the team page on every poll, as today's
  `DFEL.update_game_information` does.
- **All four list events newest first.** Normalize to chronological.

## 2. Storage

### 2.1 A dedicated model, not columns on `Event`

```
events.GameTimeline(UUIDModel)
  event          OneToOneField(Event, related_name="timeline", on_delete=CASCADE)
  source         CharField           # "RBIHF" | "CEHL" | "DFEL" | "IJNL" (= Event.competition at fetch time)
  data           JSONField           # the normalized document, §2.2
  data_hash      CharField(64)       # sha256 of `data` -- skip the write when nothing changed
  status         CharField           # scheduled | live | final  (mirrors data.status, indexed)
  fetched_at     DateTimeField       # last successful fetch
  changed_at     DateTimeField       # last time `data` actually changed
  finalized_at   DateTimeField(null) # set once a final game survived the settle window, §3.3
  last_error     TextField(blank)    # last fetch/parse error; the previous `data` is kept
```

The user's idea was a JSON field for this, and `data` is that field. It lives on its own one-to-one
model rather than on `Event` because `Event` is loaded everywhere: calendar, lists, attendance,
API lists. A 5–30 KB blob on every row would mean remembering `.defer("timeline")` in dozens of
places. A side table costs one join where it's actually wanted (`select_related("timeline")`).
It also gives the fetch bookkeeping (hash, error, timestamps) a home without adding five more
columns to `Event`.

Club scoping comes through `event.club`: a plain `UUIDModel`, not `ClubScopedModel`, the same as
the other per-event children (`Attendance`, `Lineup`, `EventReferee`). It is always reached
through an already club-scoped `Event`.

### 2.2 The normalized document (`data`, version 1)

One shape for every source, so the API, the management UI and notification rules never branch on
the source:

```jsonc
{
  "version": 1,
  "source": "RBIHF",
  "status": "live",                       // scheduled | live | final
  "period": "3", "clock": "04:31",        // best effort; null when the source doesn't say
  "score": {"home": 5, "away": 2},
  "periods": [{"label": "1", "home": 0, "away": 0}, {"label": "2", "home": 3, "away": 2}, {"label": "3", "home": 2, "away": 0}],
  "decided_in": "regulation",             // regulation | overtime | shootout | null while live
  "events": [                             // chronological
    {
      "id": "hd:c441c652-…-1:1",          // stable per source, §2.3
      "type": "goal",                     // goal | penalty | goalie_change | timeout | period_start | period_end | shootout_attempt
      "period": "1", "seconds": 473, "time": "07:53",
      "side": "home",                     // home | away | null (period markers)
      "goal": {
        "scorer": {"name": "Julia Malejka", "number": 88},
        "assists": [{"name": "Zoe Kallenbach", "number": 33}],
        "strength": "PP1",                // EQ | PP1 | PP2 | SH1 | SH2 | EN | PS | null
        "empty_net": false, "penalty_shot": false, "game_winning": false,
        "score_after": {"home": 1, "away": 1}
      }
    },
    {
      "id": "rbihf:penalty:away:3359:16:0", "type": "penalty", "period": "3", "seconds": 3593, "time": "59:53", "side": "away",
      "penalty": {"player": {"name": "Glassee Kevin", "number": 16}, "code": "ROUG", "description": "Roughing", "minutes": 2, "kind": "minor"}
    }
  ],
  "team_stats": {"home": {"shots_on_goal": 31, "pp_goals": 1, "pp_opportunities": 4, "penalty_minutes": 6}, "away": {}}  // optional, hockeydata only
}
```

Rules:

- **`side` stays home/away exactly as the source says it.** "Ours" vs. "theirs" is derived when
  the data is presented, with the same `event.is_home_game` convention the score already uses (`../events/competition/hockey.py`, `api/public/routers/events.py`). If someone later fixes a game's location, the
  stored timeline doesn't need rewriting.
- **Player names are stored as the source gives them.** The format is `{name, number}` everywhere.
  We don't link people to `Member` in v1: names aren't reliable keys across sources (CEHL casing,
  RBIHF "Last First" order). See §9 for privacy.
- **Unknown fields are left out, not set to `null`.** Consumers treat every per-type sub-object
  key as optional.
- **`version` is bumped on any breaking change.** Readers check it. Because the last raw payload
  is kept (§3.4), a re-parse migration can rebuild old rows.

### 2.3 Stable event ids

Notifications and API clients both need to recognise "the same goal" across polls.

- **hockeydata:** `hd:<guid>`, already unique and stable.
- **IJNL:** `ijnl:<data-event-key>`, e.g. `goal:home:13:39::/competities/speler/3speqpannveb`.
- **RBIHF / CEHL:** synthesized as `<src>:<type>:<side>:<seconds>:<player number or name slug>:<n>`,
  where `n` disambiguates identical tuples in source order. That covers the double-roughing case
  at 59:53 in RBIHF #137. Two penalties at the same second need different players *and* sides to
  avoid `n` doing the work. The known weakness is that a correction (scorer changed from #19 to
  #22) changes the id, so the diff sees a removal plus an addition. §4 handles that explicitly
  rather than pretending ids are perfect.

### 2.4 Goal strength checks and corrections

Sources get a goal's `strength` wrong now and then (IJNL reads a missing badge as `EQ`). When a timeline's stats are synced (`events.services.game_stats`), `events.services.strength_check` replays the penalties per side (IIHF: minor / bench minor /
double minor / major / match cost a skater, misconducts don't; two at a time; a goal against a team that is a skater down ends its
earliest minor, never a major) and flags goals whose strength class (PP / SH / EQ) disagrees: `GameStat.meta["strength_suspect"]`.
Overtime, shoot-outs, penalty shots, empty-net goals and goals on the very second a penalty starts or ends are not judged.
The team analytics page lists the flagged goals; staff either count the goal as the suggested strength or keep it as recorded.
Either choice is stored in `GameTimeline.strength_overrides` (`{timeline event id: strength}`), survives re-fetches, is applied when the
stats are built, and stops the goal being flagged.

IJNL's completed games also carry the lineups and team statistics: assists get their jersey number from the lineup tables (the
timeline only links their names), and the scoreboard's Powerplay / Strafminuten become `team_stats`.

## 3. Fetching

### 3.1 One snapshot per poll

Extend `../events/competition/base.py` from "update the score" to "describe the game":

```python
@dataclass
class GameSnapshot:
    is_live: bool
    home_score: int | None
    away_score: int | None
    timeline: dict | None  # the §2.2 document, or None if this source/game has none
    raw: str | None = None  # the payload it was parsed from, kept for re-parsing (§3.4)


class CompetitionBaseClass:
    def fetch_snapshot(self, event) -> GameSnapshot: ...
    
    def update_game_information(self, event) -> None:  # now a template method, not per source
        apply_snapshot(event, self.fetch_snapshot(event))
```

`apply_snapshot` (new, `../events/services/game_timeline.py`) does what each class does by hand
today: map home/away to `score_for`/`score_against` and set `is_live`. It also upserts the
`GameTimeline`, but writes only when `data_hash` changed, then runs the diff (§4). Four copies
of that score-mapping code collapse into one. `fetch_game_info`, `poll_live_game_results` and the
"Fetch game info" button stay unchanged.

Per source:

- **RBIHF:** game page → extract the `updateTimeline({...})` object (cache per game for 10 min),
  then POST `timeline.php`. Keep `time.php` for live/score: it's cheaper, and the timeline adds
  nothing to it. `overview.php` gives per-period goals; `rbihf_standings.parse_overview` already
  parses it, so reuse that for `periods`.
- **CEHL:** `timeline.php` is already fetched for the live flag; also parse its `html`.
- **DFEL:** switch from schedule + filter to `GetGameReport` (score, status, OT/SO, everything),
  and cache the API key. This is cheaper than today.
- **IJNL:** parse `sections.timeline` (and the scoreboard's `.mp-periods`) from the response we
  already fetch.

Each parser is a pure function `parse_<source>_timeline(raw, …) -> dict`, unit-tested against
saved real payloads in `events/tests/fixtures/`. The four games named in §1 are good first
fixtures.

### 3.2 Cadence and cost

Polling stays where it is: every minute, from 20 min before to 1 h after the game. Within that
window the extra cost is +1 request per game per minute for RBIHF and nothing for CEHL, DFEL (net cheaper) and IJNL. On a busy Saturday with, say, 15 polled games, that's under 1,000 extra
requests/day, all RBIHF. If that ever matters, fetch RBIHF's timeline only when `time.php`'s score
or state changed, or every 3rd minute. Today's per-game failure isolation (`CompetitionFetchError`
is logged and skipped) stays.

### 3.3 After the final whistle

Federations correct timelines after the game: assists added, scorers fixed, a goal disallowed.
`poll_live_game_results` stops polling a game once it's seen finishing (`live_score_polling_done_at`), which would freeze the first, often incomplete, version. So:

- When a game turns final, keep the timeline at `status = final` but leave `finalized_at` empty.
- A nightly sweep (folded into `refresh_standings` at 01:00, or a sibling job) re-fetches every
  game that finished in the last 48 h with `finalized_at IS NULL`. When the hash hasn't changed
  since the previous fetch, it sets `finalized_at`.
- Games imported after they were played (§9) get a single fetch the same way.

### 3.4 Raw payloads

Keep only the *last* raw payload per game (`GameTimeline.raw`, or a `raw` key outside `data`).
Its size is ~6 KB for RBIHF, ~5 KB for CEHL, ~27 KB for hockeydata and ~45 KB for IJNL. That's
enough to re-parse every stored game when a parser improves, without keeping a growing history.

## 4. Change detection: "moments"

`apply_snapshot` compares the new `events` list with the stored one by id and fires one Django
signal per poll that changed something:

```python
game_timeline_changed = Signal()  # sender=Event; kwargs: added, removed, changed, previous_status, status, first_fetch
```

A small, pure `derive_moments(event, diff) -> list[Moment]` turns that into the things a human
would call news, from the club's point of view (ours/theirs via `is_home_game`):

| Moment `kind`                     | Trigger                                                                                                                      |
|-----------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| `game_started`                    | status scheduled → live                                                                                                      |
| `goal_for` / `goal_against`       | added goal event; includes scorer, assists, strength, score after                                                            |
| `goal_cancelled`                  | a goal event removed while the score went *down*. A removal without a score change is treated as an id correction, not news. |
| `penalty_for` / `penalty_against` | added penalty event                                                                                                          |
| `period_started`                  | added `period_start`                                                                                                         |
| `period_ended`                    | added `period_end`, with the period score                                                                                    |
| `game_final`                      | status → final, with the result and `decided_in`                                                                             |

The stored payload keeps the assists for both, but the *sentence* (management event page, live pushes) names them for **our** goals only ("Goal for us by Sam (#9) (assist: Ann (#7)), 2-1."); an opponent's goal is just "Goal against by …", and an
unassisted goal reads as plain "Goal for us by …".

Guards that keep this from becoming spam:

- **`first_fetch` produces no moments.** The first snapshot of a game that's already under way (a
  deploy mid-game, a freshly imported fixture) would otherwise "detect" every past goal at once.
- **Only moments whose game time is recent produce notifications.** Within ~5 minutes of the
  latest event counts; a backfill after a source outage doesn't replay the whole period.
- **Post-game corrections (§3.3) produce no notifications,** only an updated timeline.

**Delivery bookkeeping** (phase 3): a tiny table
`events.GameMoment(event, key, kind, payload JSON, created_at, delivered_at)` with
`unique(event, key)`, where `key` is the moment kind plus the timeline event id. The unique
constraint makes delivery idempotent even if two pollers or a retry race. The table also backs an
API "what happened since X" query. Until push is decided, moments can be derived and logged
without being stored.

## 5. API

**Public API (`/api/v1/`, club website):** serves no timeline data. Games there carry only
score and status; the timeline is for the native apps.

**Member app API (`api.protected`):** the same game-detail shape when the app gets a game screen. Push
payloads stay pointers (`{target: game id}`), per `API.md` §9. The app fetches the timeline itself.

**Management UI:** a read-only timeline card on the event detail page, next to the existing
"Fetch game info" button. It's mostly a debugging aid in phase 1: it shows what was parsed, when,
and any `last_error`.

## 6. Push notifications (shape only, rules to be decided)

Everything above is built so this part is a subscriber, not a rewrite:

```python
@receiver(game_timeline_changed)
def notify_game_moments(sender, **diff): ...  # derive_moments → filter by rules → deliver
```

How it plugs into what exists:

- `notifications.services.notify_members(..., send_email=False)` creates `Notification` rows and
  fires `notifications_created`. That already fans out to Web Push (`mobile.services.push`) and
  native push (`api.protected.delivery`, which also de-duplicates per account and reaches guardians).
  So reaching a team's players and parents needs no new delivery code.
- But one inbox row per goal per member is noisy, and the inbox isn't a live ticker. The likely
  better fit is a push-only path. That means a sibling of `notify_members` that skips the
  `Notification` row and calls the push gateways directly, using `GameMoment` for idempotency.
  This is the main build decision in §9.

## 7. Phases

| Phase          | Scope                                                                                                                                                                                                                                       | Rough size            |
|----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------|
| **1. Store**   | `GameTimeline` model + migration; `GameSnapshot`/`apply_snapshot`; four parsers with real-payload fixtures; DFEL switched to `GetGameReport`; nightly finalization sweep; management read-only card. Behind a `game_timeline` feature flag. | largest: four parsers |
| **2. Expose**  | `GET /games/{id}/` with timeline; `periods`/`decided_in`/`timeline_updated_at` on lists; ARCHITECTURE.md + API docs.                                                                                                                        | small                 |
| **3. Moments** | `game_timeline_changed` signal, `derive_moments`, `GameMoment` table, logging only.                                                                                                                                                         | small                 |
| **4. Push**    | Subscriber + rules + opt-in + rate limiting, once §9 is decided.                                                                                                                                                                            | depends on decisions  |

Phases 1–3 are independent of the push decisions and can ship first.

## 7a. Phase 1 as built

- **Model:** `events.GameTimeline` (`events/migrations/0041`) as in §2.1, plus `raw` (last payload
  only). The `game_timeline` waffle flag is seeded by `0042`. Timelines are fetched only for clubs
  that have both this flag and the competition's own flag.
- **Parsers:** `../events/services/timeline_parsers.py`, pure functions, one per source, tested
  against real payloads in `events/test_fixtures/game_timeline/`. Players are pseudonymised there
  and birthdates removed; real names never go into the repo.
- **Storage service:** `../events/services/game_timeline.py` (`store`, `fetch_and_store`,
  `refresh_timeline`, `settle`, `display_periods`).
- **Hook:** `events.services.competitions.fetch_game_info` calls `refresh_timeline` after a
  successful score update, on the same competition instance. So the live poll and the "Fetch new
  game info" button both cover it, and CEHL/IJNL reuse the response the score fetch already
  downloaded (no extra request). A timeline failure is recorded in `last_error` and never fails
  the score update.
- **Commands:**
    - `settle_game_timelines` is a scheduled job, daily at 01:30. It handles games that started
      3h–7d ago and aren't finalized yet; a final timeline that comes back unchanged gets
      `finalized_at`.
    - `fetch_game_timelines` is the manual backfill (`--club`, `--since`, `--refetch`,
      `--dry-run`). By default it covers each club's current season. Games older than the settle
      window are finalized right away.
- **Management:** the event detail page shows a read-only "Game timeline" card under the game
  card, from the club's side.

Deviations from §3.1, both to keep the score path untouched:

- **No `GameSnapshot` refactor.** Each competition class keeps its proven
  `update_game_information` for score and live status, and gains a separate
  `fetch_timeline(event) -> TimelineFetch | None` (default `None`). The single shared score-mapping
  function is left for later.
- **DFEL still uses the division schedule for its score.** `GetGameReport` is used for the
  timeline only. Its `gameStatus` values for "live" vs. "intermission" haven't been verified yet,
  so it doesn't replace the schedule's proven live detection. The hockeydata API key is cached for
  a day for the report fetch.
- **RBIHF timelines cost two requests per poll:** the game page, cached 10 min per game, plus the
  `timeline.php` POST. `overview.php` isn't called; per-period scores are derived from the goals,
  which match it.

## 7b. Phase 2 as built

- **Player names (§9.1, decided):** the timeline is only served to logged-in members (member
  app), always with names. The public API serves no timeline, so the earlier per-team switch (`Team.show_player_names_publicly`) was removed.
- **Shared shape:** `../events/timeline_schemas.py` (`TimelineOut` and its parts) is built by
  `events.services.game_timeline.timeline_for_api`, so the app API serves this
  structure. `ours` is resolved per event and on `our_side`. When a source doesn't say how the
  game was decided, `decided_in` is derived from the period labels once the game is final.
- **Public API (`/api/v1/`):** no timeline fields (`periods`, `decided_in`, `timeline_updated_at`
  and `timeline` were dropped); `GET /games/{id}/` returns the plain game.
- **Member app API (`api.protected`):**
    - `GET /events/{id}` gains `game_timeline`, filled only for games, with the club's
      `game_timeline` feature, and with names.
    - The club's `features` now includes `game_timeline`.
    - `app-api/openapi-v1.json` is regenerated.

## 7c. Phase 3 as built

- **Signal:** `events.services.game_timeline.store` sends `game_timeline_changed` (from
  `../events/services/game_moments.py`) after saving a timeline whose document actually changed.
  It carries the previous document and `first_fetch`, which is true when the row had never held
  real data.
- **Derivation:** the pure `derive_moments(previous, current, our_side=, first_fetch=)` produces
  the kinds in §4 with the guards as designed:
    - a first fetch yields nothing;
    - a game that was already final yields nothing (post-game corrections);
    - a vanished goal is `goal_cancelled` only when the total score dropped, so a re-keyed goal is
      treated as a correction;
    - `notifiable` is false for anything more than `RECENT_SECONDS` (5 min of game clock) behind the
      newest event, i.e. a catch-up after an outage.
- **Storage:** `events.GameMoment` (`events/migrations/0043`), recorded by `record_moments`
  (connected in `../events/signals.py`). `unique(event, key)` makes a repeat a no-op. `key` is the
  kind plus the timeline event id; `period_started`, `period_ended` and the game-level kinds use the period or the
  kind itself. `delivered_at` is reserved for phase 4.
- **Visibility (decided):** a "Detected moments" list under the management event page's
  timeline card, with catch-ups flagged, plus a read-only Django admin. Moments are not exposed on
  the APIs and nothing is pushed.

## 7d. Phase 4 as built

Decisions (§9.2–9.6):

- **Who:** opt-in, anyone with an account in the club, per team they follow.
- **What:** every notifiable moment kind.
- **Channel:** push only, with no inbox row.
- **Batching:** the moments one poll detected are coalesced into one push per account, with no
  cap per game.
- **No backfill pushes:** a timeline first fetched after the game stays silent. The event page
  says so, and the full timeline is shown above it.

- **`events.GameFollow`** (`events/migrations/0044`): (club, user, team), unique per
  user and team. Set it from the member app (`GET/PUT /game-follows`) or the PWA's
  Me → "Live game updates" screen (`mobile:live_game_updates`). Both exist only while the club
  has the `game_timeline` flag.
- **Delivery:** `../events/services/game_push.py`. After `record_moments` stores a batch, it calls
  `schedule_delivery`, which runs `deliver_moments` after commit in a background thread (the
  same pattern as `api.protected.delivery`). `deliver_moments`:
    - marks the batch's notifiable moments delivered, whether or not anyone follows the team, so
      nothing is retried later;
    - finds the followers of any of the game's teams (active accounts only);
    - sends each one push, to native devices (`api.protected.push` gateway, `kind: "game_moment"`,
      target = the event) and to web push subscriptions (`mobile.services.push.send_push_to_user`).
    - The body is one `describe_moment` line per moment, in game-clock order.
- **Language:** push text is rendered in the server's default language (the background thread
  has no request locale). Per-recipient localisation would need the device or account locale and
  is not done yet.
- **The management event page** marks moments as "pushed" (handled) or "catch-up" (never
  pushed).

## 8. Testing

- **Parsers:** one fixture per source per interesting case: plain game, OT/shootout, a game with
  an empty-net goal, a live (mid-game) payload, and an RBIHF payload posted without the roster.
- **`apply_snapshot`:** no write when the hash is unchanged; score mapping for home/away games;
  `last_error` kept alongside the previous data on failure.
- **`derive_moments`:** ours/theirs, `first_fetch` silence, correction vs. cancellation, the
  recency guard.
- **Network:** mocked at `requests.get`/`post`, as in today's competition tests.

## 9. Open decisions

1. **Privacy of player names (decide before phase 2).** Federation sites publish them, but youth
   leagues (U14–U18) are minors, and we'd be redistributing names through the club's own API and
   pushes. Options:
    - show names as-is everywhere;
    - hide names for youth teams (`Team` would need an age-category signal);
    - per-club setting;
    - numbers only in the public API, names only for authenticated members in `api.protected`
      (resolved: the public API serves no timeline at all).
2. **Who gets pushes?** Team players and guardians by default with opt-out, opt-in only, or
   "followers" (anyone, including non-members via the PWA)? Which moments by default? The likely
   candidates are goals and the final result; penalties are probably opt-in.
3. **Inbox or not.** Push-only for live moments (recommended), or also `Notification` rows?
4. **Rate limiting.** For example, coalesce goals within 60 s into one push and cap pushes per
   game per account. A 18-3 game (RBIHF #203) would otherwise be 21 pushes.
5. **Opponent events.** Notify for `goal_against`, or only our own goals plus the final result?
6. **Backfill.** Fetch timelines once for already-played games of the current season (so the
   website shows them), or only from go-live onwards?
7. **Lineups and player stats** (hockeydata, IJNL, the RBIHF rosters). Out of scope for v1.
   Store them under `data.lineups` later if the website wants them.
