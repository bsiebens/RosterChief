# Redesign handover: what still needs backend work

The member app, coach app and manage app were redesigned on branch `redesign/apps` (phases 1–4). Almost everything is wired to
real data. This document lists what is **not**: blocks drawn from sample content, mockup features the API cannot supply yet, and
smaller gaps found while building. Use it as a prompt for the backend work.

Every block still drawn from sample content carries `<SampleBadge what="…" />` (`frontend/management/src/lib/components/SampleBadge.svelte`).
`grep -rn "<SampleBadge" frontend/management/src` lists them; remove the badge and its fixture when the block reads live data.

## 1. Blocks still on sample data

None left: the calendar link (`events.CalendarFeed`, `GET/PUT/DELETE /portal/me/calendar-feed`, public `/public/calendar/<token>.ics`) and the
Finance totals (`GET /finance/summary`) read live data. `SampleBadge.svelte` stays for future placeholders.

## 2. Mockup features the API cannot supply yet

### 2.3 Line-up lines in notifications and the member app
Line-up lines (sport-level feature, `Sport.lineup_slots_enabled` + `lineup_template`, `LineupSlotAssignment`) are informational only.
A slot holds one player but a player can be in several slots (regular lines plus power play, penalty kill, extra attacker); un-picking
removes all their slots. `GET /events/{id}/lineup.pdf` renders a PDF overview of the lines (WeasyPrint, English, 503 without it).
Not done: the player's line in the "You're in the line-up" notification (the body is one shared text; it would need a per-player body in
`events.services.lineup._publish`), and showing "Line 2 · LW" to families in the member event drawer.
Decision taken: lines are **not** used in statistics, because lines change during games. A per-team opt-in with a warning was
considered and deliberately left out.

### 2.4 Phone attendance for coaches without manage rights
A coach on a phone who may see but not manage attendance gets the desktop attendance table (the read-only cards were removed). A
read-only phone list (answer pills, counts) would be nicer; no backend work needed.

## 3. Behaviour gaps and decisions to review

- **Early renewal keeps one file.** A member's early renewal keeps the old completion valid until approval, but the stored document is
  replaced when the new file is handed in (no per-version file history); `RequirementEvent` records who did what, not the files.
- **Invoice link in the profile overview.** The download itself (`/portal/invoices/{id}.pdf`) follows `members.services.invoice_access`
  (registration contact, or a parent/guardian of the family of the invoiced people). `membership` rows in `api/routers/portal_profile.py`
  (`_membership_rows`) still show `invoice_id` only when the contact is among the viewer's people: make it use
  `invoice_access.can_download(viewer, batch)` so a non-contact parent also gets the link.
- **Team staff permissions.** Roster and staff writes are allowed for anyone with a management position on that team (e.g. head coach);
  assistants are read-only in the coach Squad page. Letting every staffed coach edit would also let them edit team settings and the team
  photo, because those share one permission check (`teams.services.teams`). Split the check if that is wanted.
- **News review.** Reviewer note on send-back exists (`News.review_note`, `sent_back_at`, query parameter `note`). Open: reviewers who
  open the notification link in a plain phone browser (outside the coach app) land on the desktop news page; there is no
  "unsaved changes" warning when leaving the coach news editor.
- **Evaluations.** Release is per evaluation (`PlayerEvaluation.release`: "" / "notes" / "full"), per criterion visibility
  (`Field.visible_to_family`) and bulk release at the end of a meeting. Open: families are told once per release, not on later level
  changes; nothing in the manage overview counts "evaluations waiting to be shared".
- **Line-up unpublish** restores each player's previous answer from `Attendance.status_before_lineup`. Line-ups published before that
  column existed restore to "present".
- **Push.** Web push needs `DJANGO_VAPID_PUBLIC_KEY`, `DJANGO_VAPID_PRIVATE_KEY`, `DJANGO_VAPID_ADMIN_EMAIL` and Web push switched on in
  Control panel → Push. Generate a pair with `uv run python manage.py generate_vapid_keys --email you@example.com`.

## 4. Migrations added during the redesign (run `migrate_schemas` on every environment before deploying)

| App | Migration | What |
|---|---|---|
| members | `0016_requirement_pending_review` | member hand-ins wait for staff review (`is_pending_review`, `submitted_at`) |
| members | `0017_requirement_review_history` | `review_state` / `review_note`, `RequirementEvent` history, a file sent by the club (`sent_file`, `sent_at`, `sent_note`), `submitted_valid_until` for early renewals |
| evaluations | `0003_evaluation_release` | public note, release level, released at/by |
| formbuilder | `0004_field_visible_to_family` | per-criterion visibility to the family (data step keeps scores visible) |
| news | `0002_review_note` | reviewer note and sent-back time |
| events | `0014_attendance_status_before_lineup` | answer before a line-up was published |
| events | `0015_lineup_selection_slot` | line-up slot per selected player |
| club | `0012_sport_lineup_slots` | sport-level switch and template for line-up lines |
| events | `0017_event_notes` | `Event.notes` and `EventSeries.notes` (staff notes for the invitees) |
| events | `0018_calendar_feed` | `CalendarFeed`: one secret calendar link per account |

A missing migration hides pages silently: server `load` functions drop any part whose API call fails (the manage overview vanished
once because `members.0016` was not applied).

## 5. Frontend items that are intentionally not backend work

- Tablet/desktop restyle of the calendar pages, the line-up editor, tasks, files and the manage event form was not done (they already
  follow the shared tokens); the event detail header, tab bar and attendance KPIs were.
- The manage account security page uses shared cards (`PasskeysCard`, `TotpCard`, `RecoveryCard`) and was left as is.
- No page has been checked in a real browser by the people who built them: expect small visual fixes.
