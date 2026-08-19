repo: bsiebens/RosterChief
branch: main

## Last sync
date: 2026-08-16T00:00:00Z

### Updated in this project
- Clean-sheet restructure into four surfaces: control panel, club management, and one mobile app with Coach / Member modes (role switcher in the header, remembered per device).
- "Parents app" renamed and rethought as the Member app — adult members and parents use the same screens, with a person switcher on anything per-member.
- New design language: Barlow Condensed display, scoreboard numerals, navy/red core, ice-blue coach accent; club theming limited to primary + secondary colour, logo and wordmark.
- Added member detail drawer, news list + editor, club branding settings and the member notification inbox.
- Control panel moved to an industrial light workspace under a dark command bar, monospaced figures throughout.

## Sync history
date: 2026-08-08T16:59:19Z — control panel rebuilt around the real statistics service; club detail stat groups; dark hero band.
date: 2026-08-08T16:18:27Z — first clean-sheet design for three layers; domain model from the repo's Django apps.

## Screen map
| Screen | Repo files |
| --- | --- |
| Foundations (palette, type, status, role switcher, surface map) | club/models.py (Club.primary_color, secondary_color, logo) |
| M1–M6 Member app: home, event RSVP for several, calendar, news article, me &amp; my people, edit personal info | news/models.py, events/models.py (Attendance), members/models.py (Family), club/models.py (ClubMembership) |
| C1–C6 Coach mode: today, bench attendance, line-up / game selection, create event, post news, add members to team | events/models.py, teams/models.py (Position, StaffAssignment), news/models.py |
| D1–D6 Management: club home, members list &amp; bulk actions, sign-up intake &amp; approval, team &amp; staff assignment, season calendar planning, dues &amp; billing | management/urls.py, members/models.py, teams/models.py, events/models.py, billing/models.py |
| D7–D9 Management: member detail drawer, news list &amp; editor, club identity &amp; branding | members/models.py, news/models.py, club/models.py (primary_color, secondary_color, logo, custom stylesheet) |
| M7 Member app: notification inbox | events/models.py (Attendance), news/models.py, billing/models.py |
| P1–P2 Control panel: platform health, club provisioning &amp; feature flags | controlpanel/services/statistics.py, controlpanel/views.py, features/models.py, billing/models.py |
