# Redesign handover: what still needs backend work

The member app, coach app and manage app were redesigned on branch `redesign/apps` (phases 1–4). Almost everything is wired to
real data. This document lists what is **not**: blocks drawn from sample content, mockup features the API cannot supply yet, and
smaller gaps found while building. Use it as a prompt for the backend work.

Every block still drawn from sample content carries `<SampleBadge what="…" />` (`frontend/management/src/lib/components/SampleBadge.svelte`).
`grep -rn "<SampleBadge" frontend/management/src` lists them; remove the badge and its fixture when the block reads live data.

## 1. Blocks still on sample data

### 1.1 `calendar-sync` — member calendar, "Sync to my calendar"
Where: `routes/(app)/parent/+page.svelte` (calendar header, disabled button + badge).
Need: a personal calendar feed so a family can subscribe in Google/Apple/Outlook.
- A per-person (or per-account, covering the family) secret feed URL: `GET /public/calendar/{token}.ics` (no login), with a token that
  can be created, shown, replaced and switched off (same pattern as `members.services.stats_sharing` for the personal stats link).
- Content: the person's (and managed children's) events for the current and next season: games, trainings, other; title,
  start/end, gathering time as an alarm or in the description, location, "Cancelled" status, the player's answer in the description.
- `GET/PUT/DELETE /portal/me/calendar-feed` to read, enable/rotate and disable it; the frontend then shows the URL with Copy and the
  "webcal://" link, like the statistics share card (`components/portal/StatsShare.svelte`).

### 1.2 `finance-totals` — manage Finance list, totals row
Where: `routes/(app)/finance/+page.svelte`, fixture `src/lib/finance-sample.ts`.
Need: `GET /finance/summary?season_id=…&from=…&to=…` returning, for confirmed invoices in the selection: `invoiced` (sum), `received`
(sum of payments), `open` (sum), `overdue` (sum and count of invoices past their due date). Amounts as strings (decimals), one query
with aggregates, respecting the same filters as the invoice list.

## 2. Mockup features the API cannot supply yet

### 2.1 Members list: Team and Docs columns
The mockup's members list shows the member's team(s) and an onboarding/documents state. `GET /members` returns neither. Add to each
row: `teams` (current season, short names), and `requirements` summary: counts of `missing`, `pending` (handed in, waiting for review),
`expiring_soon`, `expired` (see `members.services.onboarding.item_for` / `ChecklistItem.display`). Keep the list fast (prefetch, no N+1).

### 2.2 Sign-ups: per-requirement decision column
The mockup's sign-ups page has a checks column (Approve / Decline / Request / Compare). The live flow has Place, Link, Approve,
Cancel and a requirements dialog; that stays. If the product wants per-requirement decisions on a registration (approve a document,
decline it with a reason, request a new one), the registration model needs per-requirement review state and a reason; reuse
`MemberRequirementStatus.is_pending_review` and the approve/reopen services in `members/services/onboarding.py`.

### 2.3 Event notes
Events have no notes/description field, so the phone event form (coach view) cannot edit one. Add `Event.notes` (text, staff-written,
shown to invitees in the member event drawer), an `EventIn/EventOut` field and the form field.

### 2.4 Line-up lines in notifications and the member app
Line-up lines (sport-level feature, `Sport.lineup_slots_enabled` + `lineup_template`, `LineupSlotAssignment`) are informational only.
A slot holds one player but a player can be in several slots (regular lines plus power play, penalty kill, extra attacker); un-picking
removes all their slots. `GET /events/{id}/lineup.pdf` renders a PDF overview of the lines (WeasyPrint, English, 503 without it).
Not done: the player's line in the "You're in the line-up" notification (the body is one shared text; it would need a per-player body in
`events.services.lineup._publish`), and showing "Line 2 · LW" to families in the member event drawer.
Decision taken: lines are **not** used in statistics, because lines change during games. A per-team opt-in with a warning was
considered and deliberately left out.

### 2.5 Phone attendance for coaches without manage rights
A coach on a phone who may see but not manage attendance gets the desktop attendance table (the read-only cards were removed). A
read-only phone list (answer pills, counts) would be nicer; no backend work needed.

## 3. Behaviour gaps and decisions to review

- **Early renewal of expiring requirements.** A requirement that is `expiring_soon` is locked for the member (only expired/missing ones
  can be handed in again). Decide whether members may hand in a renewal early; `submit_as` in `members/services/onboarding.py` is the
  place.
- **Invoice download for memberships** in the member profile only works when the signed-in person was the registration contact.
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
| evaluations | `0003_evaluation_release` | public note, release level, released at/by |
| formbuilder | `0004_field_visible_to_family` | per-criterion visibility to the family (data step keeps scores visible) |
| news | `0002_review_note` | reviewer note and sent-back time |
| events | `0014_attendance_status_before_lineup` | answer before a line-up was published |
| events | `0015_lineup_selection_slot` | line-up slot per selected player |
| club | `0012_sport_lineup_slots` | sport-level switch and template for line-up lines |

A missing migration hides pages silently: server `load` functions drop any part whose API call fails (the manage overview vanished
once because `members.0016` was not applied).

## 5. Frontend items that are intentionally not backend work

- Tablet/desktop restyle of the calendar pages, the line-up editor, tasks, files and the manage event form was not done (they already
  follow the shared tokens); the event detail header, tab bar and attendance KPIs were.
- The manage account security page uses shared cards (`PasskeysCard`, `TotpCard`, `RecoveryCard`) and was left as is.
- No page has been checked in a real browser by the people who built them: expect small visual fixes.
