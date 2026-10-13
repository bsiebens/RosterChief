# Prompt: update the iOS app for the new push, pictures and preferences API

Paste everything inside the fence into Claude Code, run from the **iOS app repository**. The server
side (repo `RosterChief`) is finished; the prompt is self-contained, and points at the server's own
contract (`openapi-app-v1.json`, `ios.md`) as the source of truth when something is unclear.

````text
You are updating the RosterChief iOS app (SwiftUI) to match changes made to the RosterChief server's
app API (`/api/app/v1/`). The server work is finished and tested; your job is the client. Work in
small, reviewable steps, one commit per numbered section, and keep the app building and its tests
green after each. Start by reading the codebase: find how the app talks to the API (networking layer,
models, auth/token refresh, club switching, the Notifications inbox, profile screens, the
push-registration code and the notification handling in the AppDelegate / UNUserNotificationCenter
delegate) and match its conventions (naming, error handling, localisation, DI, test style). Do not
rewrite things that already work.

SOURCE OF TRUTH: the server's OpenAPI document (`docs/app-api/openapi-app-v1.json` in the server repo,
also served at `https://<base domain>/api/app/v1/openapi.json`) and its guide (`docs/app-api/ios.md`).
If anything below conflicts with them, they win: tell me about the conflict instead of guessing.
Before coding, diff the OpenAPI document against the app's existing models and list for me which
fields/endpoints are new or changed, then do the work below.

All user-facing text must be localisable (the app is Dutch + English: `nl`, `en`), use the app's
existing String Catalog / localisation mechanism. Never hard-code strings.

────────────────────────────────────────────────────────────────────────────────────────────
1. PROFILE PICTURES: two pictures per person

A person now has two pictures:
- `player_photo_url`: the OFFICIAL player picture. Only club managers can change it (in the web
  management portal). Read-only in the app: never offer to change it.
- `profile_photo_url`: the SELF-SERVICE picture, set from the app. Visible to everyone in the club,
  never public.
- `photo_url`: the effective one = profile picture, else player picture. USE THIS FOR EVERY AVATAR
  (rosters, lists, headers). It is `null` when the person has neither.

Endpoints (all need the Bearer token, so load images with your own authenticated `URLRequest`, NOT
`AsyncImage`; cache by URL; the URL changes whenever the picture does, via a `?v=` hash):
- `GET /members/{id}` returns `photo_url`, `profile_photo_url`, `player_photo_url`,
  `photo_public_consent`.
- `GET /members/{id}/photo` serves the effective picture; `?kind=profile` or `?kind=player` fetches
  one directly (the player one has stricter visibility rules and may 404 for non-staff).
- `POST /members/{id}/photo` (multipart/form-data, part `photo`; JPEG/PNG/WebP, at most 10 MB;
  optional `photo_public_consent=true|false`) now sets the PROFILE picture. Convert HEIC from the
  camera/picker to JPEG and downscale to ~1600 px longest side before upload.
- `DELETE /members/{id}/photo` removes the profile picture only (the player picture stays and becomes
  the effective one again).
- `photo_public_consent` now means: "the official player picture may be shown on the club's public
  website/API". Update its label/explanation text accordingly (it is NOT about the profile picture).

Do: update models; show `photo_url` in all avatars; on the profile screen show the effective picture,
let the user set/replace/remove their PROFILE picture (PhotosPicker + camera), and show the official
player picture as a read-only secondary image with a short explanation ("set by your club") if
present; keep the consent toggle with the new wording. Handle 404 on a picture gracefully (initials).

────────────────────────────────────────────────────────────────────────────────────────────
2. PUSH PAYLOAD AND NOTIFICATION HANDLING

The push payload (in `userInfo`) now has these custom keys next to `aps`:
`kind` (`notification` or `game_moment`), `notification_id` (absent for `game_moment`), `club_slug`,
`category`, `target` (`null` or an object `{type, id?, slug?}` with `type` in `news`, `event`,
`form_send`, `order`, `calendar`; ignore unknown types). `category` is one of: `news`, `event`,
`reminder`, `lineup`, `crew`, `form`, `shop`, `coach`, `admin`, `announcement`, `game`, `general`
(treat unknown values as `general`). `aps.badge` is the account's total unread count across all clubs.
`aps.thread-id` is the club slug (inbox pushes) or the event id (live-game pushes); the system groups by it.

Server-side behaviour you do NOT implement but should know: pushes expire (news 7 days, live games 15
minutes), a newer live-game push for the same event replaces the earlier one, pushes can be held
until the user's quiet hours end, and `critical` ones (a cancelled or same-day changed event) ignore
quiet hours.

Do:
- Add `category` (String, default `general` when missing/unknown) and `priority` (`normal`/`critical`)
  to the notification model; `GET /notifications` items now include both. Use `category` for a small
  icon per category in the inbox and (optionally) a filter. Show critical items with a distinct
  (non-colour-only) marker.
- Make sure tap handling still works: switch to the club in `club_slug`, mark the notification read
  (`POST /notifications/{notification_id}/read`, skip for `game_moment`), open `target`.
- Platform announcements now arrive as normal inbox notifications with `category: announcement` and
  `source: null` (no target): tapping just opens the notification detail/inbox; make sure a
  notification without a target does not break navigation.
- Keep the app icon badge in sync with `GET /notifications/unread-count` as today.

────────────────────────────────────────────────────────────────────────────────────────────
3. NOTIFICATION SETTINGS SCREEN (new): which notifications do I get?

People can switch PUSH and EMAIL on/off per category, for all clubs or per club, and per person (a
parent can set different switches for each child). The in-app inbox itself is never switchable. No
stored switch means ON. Rules: a person-in-a-club switch beats the club switch, which beats the
all-clubs default.

Endpoints (account plane = on the BASE domain, like `/me` and `/devices`; Bearer token):
- `GET /me/notification-preferences` returns:
  - `categories[]`: `{key, label, description, android_channel_id, android_importance}` (label and
    description are already in the request language; ignore the `android_*` fields on iOS);
  - `defaults`: `{category: {push: Bool, email: Bool}}` (the all-clubs switches);
  - `clubs[]`: `{club_slug, club_name, overrides: {category: {push?, email?}}, effective:
    {category: {push, email}}, people: [{member_id, name, is_self, overrides, effective}]}`.
    `people` are the people the account manages in that club (itself, plus children for a parent).
    `effective` always shows what actually applies, so render from `effective` and use `overrides`
    only to know whether to show a "reset to default" affordance.
- `PATCH /me/notification-preferences` with
  `{"club_slug": null | "<slug>", "member_id": null | "<uuid>", "changes": [{"category": "news",
  "push": false, "email": true}]}`. `club_slug: null` writes the all-clubs defaults; a slug writes an
  override for that club; `member_id` (requires `club_slug`) writes a per-person switch. Omitted
  `push`/`email` keys are left unchanged; repeating a call is harmless. Invalid category, a club the
  account is not in, or a member the account does not manage there gives `422 validation_error`
  (nothing saved). Answers with the same body as GET: just re-render from it.
- `DELETE /me/notification-preferences?club_slug=<slug>&member_id=<uuid>&category=<key>` drops
  overrides (back to inherited). Leave `category` off to drop the whole scope; leave `club_slug` off
  for the defaults.

UI to build (Settings > Notifications):
1. Top level: a section "All my clubs" with the category x (Push, Email) table from `defaults`
   (Toggle rows with the category label + description), then one row per club leading to a per-club
   screen with the same table from `clubs[i].effective`; rows that have an override show "Set for
   this club only" and a "Reset to default" action (DELETE for that club + category).
2. On a per-club screen, if `people.count > 1`, a person picker ("Me", "Lou", ...). Switching it
   shows that person's `effective` and writes with `member_id`. Explain: "Applies to notifications
   about this person".
3. Optimistic toggles with rollback on error; debounce rapid changes; handle offline with a retry
   hint. Accessible: VoiceOver labels "<category>, push, on", Dynamic Type, no colour-only state.
4. Keep the existing email-preference toggle (`PATCH /me/settings` `email_notifications`
   `always`/`unless_app`) on the same screen; explain it applies to email next to the app.

QUIET HOURS (same screen, account-wide):
- `GET /me/quiet-hours` -> `{enabled, start: "22:00", end: "07:00", timezone, active_now, until}`.
  Defaults when nothing is stored: disabled, 22:00-07:00, server zone.
- `PUT /me/quiet-hours` with `{enabled, start, end, timezone}` (`HH:MM` 24h; IANA zone). 422 per field
  on bad input. `DELETE /me/quiet-hours` removes it.
- UI: a toggle, two time pickers (from/until; may cross midnight), time zone defaulting to
  `TimeZone.current.identifier`, and a caption: "While quiet hours are on, push notifications are held
  and arrive when they end. Live game updates are skipped. Cancelled or same-day changed events and
  emails still come through." Show `active_now`/`until` as a status line when active.

────────────────────────────────────────────────────────────────────────────────────────────
4. PUSH REGISTRATION: verify, don't change unless needed

`POST /devices` is unchanged: `{installation_id, platform: "ios", environment: "production" |
"sandbox", push_token (hex), app_version, locale, push_to_start_token?}`, called on every launch and when
the token or the in-app language changes (title/body are rendered in the registered `locale`: `nl` or
`en`, anything else gets English). `push_enabled: false` in the response is not an error (the server may
have no APNs key yet). Check that the app already does all of this; fix gaps only.
Live Activities (`GameAttributes`) are unchanged.

────────────────────────────────────────────────────────────────────────────────────────────
5. TESTS AND DONE CRITERIA

- Unit-test the Codable models against JSON samples taken from the OpenAPI examples (including
  missing/unknown `category`, a notification with `source: null`, `photo_url: null`).
- Test the settings view model: render from `effective`, PATCH body construction (global / club /
  person scope), rollback on 422, reset via DELETE.
- Test quiet-hours validation and the PUT body.
- UI-test or preview the screens in Dutch and English, and at the largest Dynamic Type size.
- When done, give me: the list of changed files per section, anything in the API you found ambiguous,
  and a manual test plan for a real device (receive each category, mute one, quiet hours across
  midnight, per-child mute, avatar fallback from profile to player picture).

Do not change the server. If you think the API needs a change, write it down and ask.
````
