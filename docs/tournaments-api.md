# Tournaments API contract

Base `/api/v1`. JSON everywhere; ids are UUIDs; dates `YYYY-MM-DD`; times `HH:MM`; datetimes ISO 8601 with offset; money strings with 2 decimals.
Errors: 401 anonymous, 403 `{detail, code:"forbidden"}` (missing permission, also when the feature is off for a non-owner), 404 (feature off for owner/anonymous, unknown id), 409 `{detail, code}` (state conflicts), 422 `{detail, code, errors:{field:[msg]}}` (validation; `errors` keys may be dotted like `players.2.last_name`), 429 on public submits.
Request bodies that are "optional" (`generate-matches`, `schedule/generate`, `next-phase`) must still send `{}`.

## Access

- `can_manage_tournaments` / `can_operate_tournaments` on `/me` (false when the feature is off). Manage implies operate. Owner and platform owner pass both.
- **view**: either permission. **operate**: `tournaments.operate_tournaments` (or manage). **manage**: `tournaments.manage_tournaments`.
- Gate order: permission 403 first, then feature 404.

## Management `/tournaments`

Tournament doc (list item): `id, slug, name, season{id,name}, start_date, end_date, status(draft|published|in_progress|completed|cancelled), registration_open, max_teams, accepts_registrations, pending_registration_count, teams_in_count`.

| Method + path | Tier | Body -> response |
|---|---|---|
| GET `` ?season=&status= | view | `{results:[item], seasons:[{id,name}], can_manage}` |
| POST `` | manage | `{name, status?, days:[{date, starts_at:"HH:MM", ends_at:"HH:MM"}] (1-60, each date once, ends after it starts, need not be consecutive), registration_open?, max_teams?, earliest_birth_year?, latest_birth_year?, contact_email?, description? (markdown), home_team_id?, deposit_amount?, entry_fee_amount?, lunch_mode(none|optional|mandatory)?, lunch_price_per_day?, lunch_billed_with_fee?, default_match_duration_minutes?}` -> 201 detail. `start_date`/`end_date` are derived (first and last day); a season must cover them, else 422 on `days`. The calendar event runs from the first day's start to the last day's end and follows later changes to the days. Every club team entered (the home team, one added by hand, or an approved club-team registration) gets its own calendar event (`event_id` on the team doc; the home team's is the tournament's `event_id`), its staff is copied once and its players follow that event's line-up; a withdrawn team's event is cancelled and a deleted team's is removed. Home team gets entered + calendar event; its staff (that season's `StaffAssignment`s) is copied onto the roster once as `staff`, and its players follow the event's line-up picks (minus anyone absent/excused), live; publishing notifies shifts. |
| GET `/{id}` | view | item + `contact_email, description, earliest_birth_year, latest_birth_year, home_team, event_id, deposit_amount, entry_fee_amount, lunch_mode, lunch_price_per_day, lunch_billed_with_fee, default_match_duration_minutes, schedule_needs_regeneration, dates[] (the days played), days[{date,starts_at,ends_at}], location, can_manage, can_operate` |
| PATCH `/{id}` | manage | any subset of the POST body (explicit null clears; `days`, when sent, replaces all days) -> detail |
| DELETE `/{id}` | manage | 204; 409 `not_draft` unless the tournament is a draft. Also deletes the calendar event created for it |
| GET `/{id}/registrations` | view | `{results:[{id,status(pending|approved|rejected),name,club_team,is_foreign,contact_*,primary_color,secondary_color,logo_url,rejection_reason,submitted_at,reviewed_at,tournament_team_id,is_oldest_pending,players:[person]}]}` ordered approved, pending (oldest first), rejected |
| POST `/registrations/{rid}/approve` | manage | -> registration doc (`409 already_decided`) |
| POST `/registrations/{rid}/reject` | manage | `{reason?}` -> registration doc |
| GET `/{id}/teams` | view | `{results:[team], payment_statuses}` |
| POST `/{id}/teams` | operate | `{kind:"club", team_id}` or `{kind:"foreign", foreign_name, foreign_contact_name, foreign_contact_email, foreign_contact_phone?, foreign_primary_color?, foreign_secondary_color?}` -> 201 team |
| POST `/teams/{team}/logo` | operate | multipart `logo` (image) -> team |
| PATCH `/teams/{team}/payment` | manage | `{payment_status: unpaid|deposit_received|fully_paid}` -> team |
| POST `/teams/{team}/withdraw` | manage | `{reason?}` -> team (409 `cannot_withdraw`, e.g. home team) |
| DELETE `/teams/{team}` | manage | 204 (manual teams only; 409 `cannot_delete`) |

Team doc: `id, name, slug, logo_url, primary_color, secondary_color, club_team{id,name}|null, is_foreign, is_home_team, contact_name, contact_email, contact_phone, seed, payment_status, withdrawn_at, withdrawal_reason, registration_id, player_count, staff_count, has_age_flag`.
Person doc (roster entry / registration player): `id, member_id, name, first_name, last_name, jersey_number, role(player|staff), position_label, date_of_birth, license_number, dietary_restrictions, lunch_days[ISO dates], age_flagged`.

### Roster, lunch, PDFs (`/tournaments/{id}/...`)
- GET `/teams/{team}/roster` (view) -> `{team, entries:[person], lunch_mode, lunch_price_per_day, dates, earliest_birth_year, latest_birth_year, lunch:[{date,player_count,staff_count,dietary_notes}]}`
- POST `/teams/{team}/roster` (operate) `{first_name, last_name (both required), jersey_number?, role?, position_label?, date_of_birth?, license_number?, dietary_restrictions?, lunch_days?}` -> 201 person. Staff rows drop the date of birth; lunch days follow the lunch mode (none: empty, mandatory: all days, optional: given days inside the tournament); duplicate jersey 422; date of birth must be 1900..today.
- PATCH `/teams/{team}/roster/{entry}` (operate) subset; names only editable on foreign entries. PUT `.../{entry}/lunch` `{lunch_days}` (only when lunch is optional, 422 `lunch_not_optional`). DELETE `.../{entry}` 204.
- GET `/teams/{team}/roster/pdf`, `/teams/{team}/lunch/pdf`, `/lunch-summary/pdf` (view): `application/pdf` attachment; 503 `{code:"pdf_unavailable"}` when WeasyPrint is not loadable. GET `/lunch-summary` (view) -> `{lunch_mode, days:[{date,player_count,staff_count,dietary_notes,teams:[{team,count,dietary_people:[{name,restriction}]}]}]}`.

### Competition (`/tournaments/...`)
Match doc: `id, stage_id, group{id,name}|null, round_number, round_label, sequence, field{id,name}|null, scheduled_start, auto_scheduled, status, score_home, score_away, went_to_overtime, penalty_home, penalty_away, home_team|null, away_team|null (team refs), home_display, away_display` (display = team name or "Winner of ...").
- GET `/{id}/standings` (view) -> `{tournament, standings:[{stage, tables:[{group|null, rows:[{rank, team, played, won, drawn, lost, points, goals_for, goals_against, goal_diff}]}]}], brackets:[{stage, rounds}]}`
- GET `/{id}/schedule` (view) -> `{tournament:{id,name,default_match_duration_minutes,schedule_needs_regeneration}, stages:[{stage, rounds:[{round_number,label,all_complete,matches}]}], fields:[{id,name}], blocks:[block]}`
- GET `/{id}/schedule/board` (view) -> `{px_per_minute, fields:[{field, rules:[{id,summary}], days:[{date,start,end,height_px,hour_marks,slots:[{type:"match"|"gap",start,end,minutes,height_px,top_px,match?|rule?}]}]}]}`
- POST `/{id}/schedule/generate` (operate) `{match_duration_minutes?}` -> `{scheduled_count}` (422 `scheduling`, e.g. no fields). POST `/{id}/schedule/clear` -> `{cleared_count}`.
- GET `/{id}/scores` (view) -> like schedule `stages` plus per stage `ready_for_next_phase`, `swiss_ready_for_next_round`.
- POST `/{id}/stages/{stage}/swiss/next-round` (operate) -> `{generated_count}`.
- POST `/matches/{match}/reschedule` (operate) `{field_id, scheduled_start}` -> match (422 lists placement violations). POST `/matches/{match}/result` `{score_home, score_away, went_to_overtime?, penalty_home?, penalty_away?}` -> `{match, updated_match_ids}` (422 `teams_unknown`).
- Schedule blocks (operate): POST `/{id}/schedule-blocks` `{title, starts_at, ends_at, field_id?, team_ids?}` -> 201 `{id,title,starts_at,ends_at,field,teams}`; PATCH/DELETE `/{id}/schedule-blocks/{block}`.

### Setup (`/tournaments/{id}/...`)
- GET `/setup` (view) -> `{fields:[{id,name,availability:[{date,start,end}]}], stages:[stage], break_rules:[rule], dates}`. Stage: `id,name,sequence,format(single_elim|double_elim|round_robin|groups|swiss|custom),status,rounds,num_groups,points_win,points_draw,points_loss,tiebreaker_1,tiebreaker_2(goal_diff|goals_for|goals_against|won|head_to_head),match_count,groups,needs_seed_choice,supports_placement`.
- Fields (operate): POST `/fields` `{name, availability:[{date,start,end}]}`, PATCH/DELETE `/fields/{field}`. Windows: end after start, no overlap per date.
- Stages (operate): POST `/stages` `{name, format, sequence?, rounds? (required for swiss), num_groups? (required for groups), points_*, tiebreaker_1, tiebreaker_2}`; PATCH/DELETE `/stages/{stage}`.
- POST `/stages/{stage}/generate-matches` `{with_placement?}` -> `{generated_count}`.
- GET `/stages/{stage}/seed` (view; knockout stages only) -> `{already_generated, slot_count, slots:[{type:"team",team_id}], teams, groups:[{id,name,stage_id}]}`; POST `/stages/{stage}/seed` `{slots:[{type:"team",team_id}|{type:"group",group_id,rank}|{type:"skip"}], with_placement?}` -> `{generated_count}`.
- GET `/stages/{stage}/next-phase` (view; 422 `not_ready` until every match is complete) -> `{stage, groups:[{group, standings:[{rank, team, ..., feeds:[{match_id,side,home_display,away_display,field,scheduled_start}]}]}]}`; POST same path `{order?: {group_id:[tournament_team_id,...]}}` -> `{updated_count, conflicts:[str]}` (marks the stage complete).
- Break rules (operate): POST `/break-rules` `{field_id?, trigger_type(game_count|elapsed_time|round_boundary|clock_window), trigger_every_n_games?, trigger_every_n_minutes?, trigger_after_round_number?, trigger_window_start?, trigger_window_end?, gap_minutes?, replaces_default_gap?, priority?}` -> 201 rule (+`summary`); PATCH/DELETE `/break-rules/{rule}`.

### Tasks (`/tournaments/{id}/tasks`)
Task doc: `id,title,description,starts_at,ends_at,location_label,needed_quantity,claimed_count,open_slots,is_full,claims:[{id,member{id,name},label,claimed_at}]`.
- GET (view) `{results}`; GET `/print` (view) `{timeline_start,timeline_end,locations:[{location_label,tasks:[task+timeline_left_pct,timeline_width_pct]}]}`
- POST `{title,starts_at,ends_at,description?,location_label?,needed_quantity?}` (operate) 201; POST `/generate` `{title,location_label?,starts_at,ends_at,shift_minutes,needed_quantity}` -> 201 `{created_count,last_shift_extended,results}`; PATCH/DELETE `/{task}`; POST `/{task}/assign` `{member_id}` (409 `cannot_assign`); DELETE `/{task}/claims/{claim}`.

### Dressing rooms
- Club-wide `/dressing-rooms`: GET (view) `{results:[{id,name,capacity,notes}]}`; POST/PATCH `/{id}`/DELETE `/{id}` (manage).
- GET `/tournaments/{id}/dressing-rooms` (view) `{teams:[{team,assignments:[{id,room,team,assigned_from,assigned_until,note,conflicts:[{assignment_id,label}]}]}], rooms}`; POST `/tournaments/{id}/dressing-rooms/assignments` `{dressing_room_id,tournament_team_id,assigned_from?,assigned_until?,note?}` (operate, 409 `already_assigned`); DELETE `.../assignments/{id}` 204.

## Public `/public/tournaments` (anonymous; 404 without the feature)
- GET `` -> `{upcoming:[item], past:[item]}` (published/in progress; completed). Item: `slug,name,start_date,end_date,status,location,accepts_registrations`.
- GET `/{slug}` (any status) -> item + `description_html, contact_email, earliest_birth_year, latest_birth_year, entry_fee_amount, deposit_amount, lunch_mode, lunch_price_per_day, dates, registration_open, stages, upcoming_matches, standings, brackets`.
- GET `/{slug}/schedule`, `/{slug}/standings` (same shapes as management), GET `/{slug}/teams` -> `{teams:[team ref + dressing_rooms[names]]}`, GET `/{slug}/teams/{team_slug}` -> `{team, roster:[{name,role,position_label,jersey_number}], dressing_rooms, matches}`.
- POST `/{slug}/registrations` `{foreign_name, foreign_contact_name, foreign_contact_email, foreign_contact_phone?, foreign_primary_color? (#rrggbb), foreign_secondary_color?, players:[{first_name,last_name (both required for a non-blank row), jersey_number?, role?, position_label?, date_of_birth?, license_number?, dietary_restrictions?, lunch_days?}], website:""(honeypot, keep empty)}` -> 201 `{status_token, status, age_flagged_count}`. 404 unless the tournament is draft/published; 422 `closed` when closed or full; 429 rate limited. Birth year outside earliest/latest is never an error (flagged for staff). The confirmation email links to `/tournaments/<slug>/registration/<token>` in the frontend.
- GET `/{slug}/registrations/{token}` -> `{tournament, registration:{status,name,contact_*,colors,logo_url,rejection_reason,submitted_at}, team:{slug,withdrawn_at,withdrawal_reason,payment_status}|null, players, players_editable, roster_closed, lunch_is_mandatory, mandatory_total, lunch_total, amount_owed}`.
- PUT `/{slug}/registrations/{token}/players` `{players:[...]}` replaces the list while pending, or the live roster of an approved foreign team before the start date; else 422 `locked`.
- POST `/{slug}/registrations/{token}/logo` multipart `logo` (max 2 MB image).

## Portal `/portal/tournaments` (signed-in member; 404 without the feature)
- GET `` -> `{results:[{slug,name,start_date,end_date,shift_count}]}` (published/in progress with shifts).
- GET `/{slug}/tasks` -> `{tournament, results:[{id,title,description,starts_at,ends_at,location_label,needed,claimed,open_slots,is_full,claimed_by_me,claimed_by:[family labels]}]}`.
- POST `/tasks/{task}/claim` (409 `cannot_claim` when full or already claimed) and DELETE `/tasks/{task}/claim` -> task doc.
