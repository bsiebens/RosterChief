# iOS developer guide

How to build the RosterChief iOS app on this API. Read [the overview](../) first for the two
APIs and the two planes.

## 1. Setup

* **Base domain:** production is `rosterchief.app`. Point debug builds at the test server's
  base domain instead. Keep it in one build setting: every URL in this guide starts from it.
* **Client code from the specs:** both APIs publish OpenAPI 3 documents. Generate a Swift client
  from each with [Swift OpenAPI Generator](https://github.com/apple/swift-openapi-generator):
  * sign-in: `https://<base domain>/api/auth/openapi.json`
  * app API: `https://<base domain>/api/app/v1/openapi.json`

  A snapshot of the app API document is in the repository at
  [`docs/app-api/openapi-app-v1.json`](https://github.com/bsiebens/RosterChief/blob/development/docs/app-api/openapi-app-v1.json). CI fails when the API changes without updating it, so the
  file in `git log` doubles as the API changelog.
* **Headers on every request:** `Accept: application/json`, `Accept-Language: <app language>`,
  `User-Agent: RosterChief-iOS/<version>`.
* **No cookies.** Use an ephemeral `URLSessionConfiguration` (or turn off cookie storage); the
  APIs are token-only.

## 2. Launch check

`GET https://<base domain>/api/app/v1/meta` (no token). If the app's version is below
`min_supported_app_version.ios`, show a blocking "update required" screen with an App Store link.

## 3. Signing in

All sign-in calls go to `https://<base domain>/api/auth/app/v1`.

### Email and password

```http
POST /auth/login
Content-Type: application/json

{"email": "parent@example.com", "password": "…"}
```

**200** means signed in. Keep `meta.access_token` and `meta.refresh_token`:

```json
{"status": 200,
 "data": {"user": {"id": "…", "email": "parent@example.com", …}, "methods": [...]},
 "meta": {"is_authenticated": true, "access_token": "eyJ…", "refresh_token": "eyJ…", "session_token": "…"}}
```

**401 is a step, not a failure**, whenever `data.flows` lists a pending flow:

| Pending flow `id` | Meaning | Next call |
|---|---|---|
| `mfa_authenticate` | The account has two-factor on | `POST /auth/2fa/authenticate` `{"code": "123456"}` |
| `verify_email` | The email address isn't confirmed yet | Tell the user to open the confirmation email (it links to the website), then sign in again |

For the next step, send the `meta.session_token` from the 401 response in the
`X-Session-Token` header. A TOTP code and a recovery code both go to the same endpoint.
Its 200 response carries the tokens.

Wrong password: **400** with `errors[].code`. Too many attempts: **429**.

> **Rule for every sign-in response:** when it carries `meta.session_token`, store it and send it
> as `X-Session-Token` on the next sign-in call, replacing the old one.

### Passkeys

Not in the first release (password + two-factor code first). Planned for iOS next, with the
`webcredentials:rosterchief.app` associated domain.

### Signing up

Closed. Accounts are created by the club or by the family-registration form on the website.
`GET /config` reports `account.is_open_for_signup: false`; don't show a sign-up button.

### Password reset

`POST /auth/password/request` `{"email": "…"}` sends an email whose link opens the **website**'s
reset page. Show "check your email". The app doesn't handle the link itself yet.

## 4. Tokens

* **Access token:** a JWT, valid for **10 minutes**. Send it as `Authorization: Bearer <token>`
  on every app-API call.
* **Refresh token:** valid for **90 days from last use**. Every refresh returns a *new* refresh
  token and invalidates the old one.

```http
POST https://<base domain>/api/auth/app/v1/tokens/refresh
Content-Type: application/json

{"refresh_token": "eyJ…"}
```

**200** returns `data.access_token` and `data.refresh_token`: store both, replacing the old ones.
**400** means the refresh token is dead (used before, the session was ended, or 90 days idle):
clear the tokens and show sign-in.

**Store both tokens in the Keychain** (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`).
Never in `UserDefaults`, never in logs or crash reports.

**Refresh from one place, one at a time.** Because refresh tokens rotate, two refreshes running
at once look like a stolen token, and the second fails. Put refreshing behind a Swift `actor`
that runs at most one refresh and lets every other caller await its result:

```swift
actor TokenStore {
    private var refreshTask: Task<Tokens, Error>?

    func validAccessToken() async throws -> String { … }   // refreshes shortly before expiry

    func refresh() async throws -> Tokens {
        if let task = refreshTask { return try await task.value }
        let task = Task { try await callRefreshEndpoint() }
        refreshTask = task
        defer { refreshTask = nil }
        return try await task.value
    }
}
```

On an app-API **401 `token_expired`**: refresh, then retry the request **once**. On **401
`unauthenticated`**: sign-in screen.

### Signing out

1. `DELETE https://<base domain>/api/app/v1/devices/<installation_id>` (stop pushes to this phone).
2. `DELETE https://<base domain>/api/auth/app/v1/auth/session` with the access token. It answers
   **401 with `is_authenticated: false`**, which here means *done*.
3. Delete the tokens from the Keychain.

Signing out ends that server session, so its access and refresh tokens stop working at once.

## 5. Clubs

After sign-in, `GET https://<base domain>/api/app/v1/clubs`:

```json
{"items": [{
  "id": "…", "slug": "hc-example", "name": "HC Example", "sport_type": "ice_hockey",
  "api_base_url": "https://hc-example.rosterchief.app/api/app/v1",
  "public_api_base_url": "https://hc-example.rosterchief.app/api/v1",
  "logo_url": "https://…", "event_background_url": "https://…",
  "primary_color": "#1e40af", "primary_content_color": "#ffffff",
  "secondary_color": "#e4002b", "secondary_content_color": "#ffffff",
  "relationship": ["guardian", "coach"],
  "features": {"shop": true, "shop_open": false, "forms": true, "officials": false, "evaluations": false, "game_timeline": true}
}]}
```

* `relationship`: `member` (plays or is signed up), `guardian` (acts for children), `coach`
  (staffs a team this season), `staff` (club admin, editor or member admin). Ignore values you
  don't know; more may come.
* `sport_type` (also on `GET /context` → `club`): the sport the club plays, `ice_hockey` or `other`.
  More sports will come: treat any value you don't know as `other`. Use it for sport-specific
  wording or icons, not to decide which features exist (that is `features`).
* Exactly one club: open it. More: show a picker and remember the choice.
* Use `api_base_url` for every club call. For public content (games and scores, league
  standings, sponsors), `public_api_base_url` is the club's read-only public API: no token needed.
* Theme the club like the web app does: `primary_color` for the header and navigation bar,
  `secondary_color` for buttons, the active tab and highlights, and the matching
  `*_content_color` for text on each (the club's own text colour, else black or white). The colours are never null: a club
  without its own gets the app defaults. Show `logo_url`, or the club's initials without one.
* Event screens (the next-event card on Home, an event's page) use `event_background_url` as a
  full-bleed photo in **black & white** under a dark gradient, with white text on top: in
  SwiftUI, `.grayscale(1)` on the image plus a `LinearGradient` of `#0b1220` from ~45 %
  opacity at the top to ~97 % at the bottom. Without one, use the plain dark background.
* Hide tabs whose feature is off. The server 404s them anyway.
* Push notifications carry `club_slug`: switch to that club before opening the target.

### Opening a club

`GET <api_base_url>/context` answers who the account is *in this club*:

* `managed_people`: the account's own person (`is_self: true`) and their children. Show a
  person switcher when there's more than one. Endpoints that act for someone take one of these
  ids as `member_id`.
* `season`: the current season, or `null` between seasons.
* `staffed_teams`: teams the account coaches this season. Show coach mode when not empty.
* `permissions`, `features`, and `unread_notifications` (use it for the app icon badge).

**403 `not_a_member`**: the account lost access to this club. Refresh `/clubs`.

## 6. Two-factor enrolment

Club staff with authority (admins, editors, member admins, shop/evaluation managers, and anyone
with a **management position** on a team, such as a head coach or team manager) must have
two-factor. A coach with another staff position (assistant, physio, ...) does not. Until the
required account enrols, **every** app-API call answers **403 `mfa_enrollment_required`**,
including `GET /context`: there is no way to see the coach screens first. Show an enrolment
screen that uses the sign-in API:

1. `GET /api/auth/app/v1/account/authenticators/totp`: returns `meta.secret` and
   `meta.totp_url`. Show `totp_url` as a QR code, plus the secret for copy-paste.
2. `POST /api/auth/app/v1/account/authenticators/totp` `{"code": "123456"}` activates it.
3. `GET /api/auth/app/v1/account/authenticators/recovery-codes`: show the codes once, with
   "save these somewhere safe".

Then retry the call that failed.

## 7. Push notifications (APNs)

**Before you start:** in Xcode, add the **Push Notifications** capability to the app target.
The bundle ID must be the one the server's push setup was made for. The server side (an APNs
key, sent straight to Apple, no intermediary) is set up by whoever runs the server, following [DEPLOYMENT.md "Native app
push"](https://github.com/bsiebens/RosterChief/blob/development/DEPLOYMENT.md#native-app-push-apns).
Hand them the bundle ID; they need the team's APNs `.p8` key from the Apple Developer account.
Until that's done, `POST /devices` answers `push_enabled: false` and no pushes arrive.

1. Ask permission (`UNUserNotificationCenter.requestAuthorization`) at a sensible moment, not
   on first launch.
2. `UIApplication.shared.registerForRemoteNotifications()`. In
   `didRegisterForRemoteNotificationsWithDeviceToken`, turn the token into a hex string:
   `token.map { String(format: "%02x", $0) }.joined()`.
3. Register it:

   ```http
   POST https://<base domain>/api/app/v1/devices
   Authorization: Bearer …

   {"installation_id": "<UUID generated once, kept in the Keychain>",
    "platform": "ios",
    "environment": "production",
    "push_token": "<hex token>",
    "app_version": "1.0.0",
    "locale": "nl"}
   ```

   Use `"environment": "sandbox"` for debug builds run from Xcode; TestFlight and App Store builds
   are `production`. Call it again after every launch where the token may have changed. It's
   safe to repeat.
4. The response's `push_enabled` is `false` until the server has its APNs key set up. Don't show
   an error for that.

**Payload** (arrives in `userInfo`): an `aps.alert` with the title and body, plus:

```json
{"kind": "notification", "notification_id": "…", "club_slug": "hc-example",
 "target": {"type": "news", "id": "…", "slug": "welcome"}}
```

`target` is `null` or has a `type` of `news`, `event`, `form_send`, `order` or `calendar`
(ignore unknown types; `calendar` has no `id`). On tap: switch to the club named by `club_slug`,
mark the notification read (`POST /notifications/{notification_id}/read`), then open the target
(`GET /news/{slug}`, `GET /events/{id}`, `GET /shop/orders/{id}`, the Calendar tab for `calendar`;
forms arrive in a later release). The inbox's `source` uses the same shape. The payload is only a pointer and never
contains personal data. One account gets **one** push per notification even when it concerns
several of its children. The payload also carries `aps.badge`: the account's total unread count across
all its clubs (set it as the icon badge; the app keeps it current itself as notifications are read).

`aps.thread-id` groups pushes on the lock screen: the club's slug for inbox pushes, the event id for
live-game pushes (so one game's goals stack together).

Title and body are rendered in the language of the `locale` the device last registered with
(`nl` or `en`; anything else gets English), so call `POST /devices` again when the in-app language
changes. Live-game pushes (`kind: "game_moment"`) carry `club_slug` and `target` but no
`notification_id`: they are not stored in the inbox.

**Live Activities** (iOS 17.2+, for games the account follows, directly or through a team).
Define an `ActivityAttributes` type named `GameAttributes` (or tell the server operator the name) with fixed
data `{event_id, club_slug}` and a `ContentState` carrying `home`, `away`, `home_score`, `away_score`
(numbers or null), `period`, `clock` (strings, empty when unknown) and `status` (`live` or `final`).
Keys are snake_case: map them with `CodingKeys`.

1. Send the push-to-start token as `push_to_start_token` on `POST /devices` (omit it to keep the stored one,
   `""` clears it).
2. When a followed game goes live, the server sends a `start` push to that token (with an alert in the
   device's language). When the activity exists, register its own token: `POST /events/{id}/live-activity`
   with `{installation_id, activity_token}` (repeat whenever that token changes). Until you do, the server
   treats the activity as started and sends no second start.
3. While the game is on, every change of the game feed (about once a minute) sends an `update` to the registered
   activities. The clock is the federation's as of the last poll, so interpolate it yourself; an activity not
   updated for 15 minutes is marked stale.
4. When the game ends the server sends `end` (the activity stays on the lock screen for an hour) and forgets
   the token. If your app ends the activity itself, call `DELETE /events/{id}/live-activity/{installation_id}`.

A dead token is dropped server-side: a rejected push-to-start token is cleared (send it again with the next
`POST /devices`), a rejected activity token is forgotten.

**Email next to the push:** `GET /me` returns `settings.email_notifications` and `PATCH /me/settings`
changes it: `always` (default) or `unless_app` (no notification email while this account has the app
installed; mails with an attachment such as an invoice are always sent). Offer it as a toggle in the app's
settings.

A person who also has the web app installed with notifications on gets both for now.

## 8. Member screens

All on the club host (`<api_base_url>`), all read with the Bearer token. The web app's screens
map one to one; the server decides every rule (deadlines, who may answer, caps), so the app
only renders what it gets.

### Who you act for

`/context` → `managed_people` is the account's own person (`is_self: true`) and its children.
Reads take `member_id` (one person) or nothing / `all` (everyone, the default). Every write names
one `member_id`. Show a person switcher when there's more than one; each row in a response
carries `member_id`, so label rows with that person's first name.

### Home

`GET /home?member_id=…`:

* `followed_games`: a card for each game the account **follows** (see "Following games" below) that
  is live, starts within the next day, or began in the last few hours; live first, then soonest,
  then just finished; at most 5, empty when nothing is followed. Each has both teams with the
  score (`home`/`away`: `{name, score}`, score null before there is one), the place
  (`event.location`), `status` (`scheduled` / `live` / `finished`) and, while live, the `period`
  and `clock` the federation reports (`decided_in` once finished). Show the live ones prominently;
  refresh while one is live.
* `member_cards`: the cards to show first. Every person who has something gets at least one:
  their next unanswered invitation (`kind: "rsvp_needed"`: show **In / Maybe / Out** buttons for
  each person in `members`, answered with `POST /events/{id}/rsvp`) and their next event they are
  going to (`kind: "next_going"`). People whose next event is the same one share a card, so label
  each button row with that person's first name. All `rsvp_needed` cards come first, then
  `next_going`, each soonest first. Invitations whose reply deadline has passed are not shown as
  a card (they are still in the calendar).
* Every card, hero and `needs_answer` row has two task counts for its event: `open_tasks`
  (tasks that still have a free slot) and `total_tasks` (all its tasks), the same numbers the
  calendar shows. Show "2 of 5 tasks open" straight from them (hide it when `total_tasks` is 0)
  instead of fetching each event; the tasks themselves and claiming are on
  `GET /events/{id}` → `tasks`.
* The same cards, heroes and rows also have `my_tasks`: the tasks of that event the household has
  **already taken**, so Home reminds them ("You're bringing fruit"). Household = the account's own
  person plus anyone sharing a family with them: `claimed_by_me` is true when the account's own
  person took it, false when someone else in the household did. Each entry is
  `{id, title, description, claimed_by_me}`; empty when they took nothing. To give a task back,
  use `DELETE /events/{id}/tasks/{task_id}/claim` (only works for `claimed_by_me`).
* `rsvp_needed` and `next_going`: the single-card form of the above (the family's soonest only).
  Kept for older app versions; new code should use `member_cards`.
* `needs_answer` (+ `needs_answer_more`): other open asks, not repeating anything on a `rsvp_needed` card. `type` `rsvp` → answer the event;
  `referee` / `official` → accept or decline `signup_id`.
* `open_tasks`, `dues` (money), `news` (cards), `forms_to_complete`.

### Calendar

`GET /calendar?from=…&to=…&kind=game|training&member_id=…` returns everything in the window,
soonest first. The window defaults to the next 92 days and may not be longer; for the next page,
pass the previous `to` as `from`.

**One game can appear several times.** A row is one *person in one role* for one event, so a
family where one child plays, the parent can referee and another child can officiate gets three
rows for the same game (`rsvp`, `referee`, `official`), each with its own `member_id`. They share
`event.id`, so **key the list on the row's `id`** (`{type}:{member_id}:{event_id}`, unique and
stable), never on `event.id` -- keying on the event collapses them and hides the duties. Render
every row; group by event only as a presentation choice, and then still list each role. The
`official` rows only exist while the club has the officials feature (`features.officials` on
`GET /context`). The same applies to `GET /events/{id}`: its `answers`, `referee_signups` and
`official_signups` are the three roles for that one game.

Every row also carries the event's task counts, so the list can show them without a call per
event: `open_tasks` (how many of that event's tasks, such as bring fruit or drive, still have a
free slot) and `total_tasks` (how many tasks it has in all), identical for each row of one event.
"2 of 5 tasks open" is `open_tasks` of `total_tasks`; hide it when `total_tasks` is 0. Only
fetch `GET /events/{id}` → `tasks` when the user opens the event to see or claim them.

Row types:

| `type` | Show | Action |
|---|---|---|
| `rsvp` | the event with the person's `status` | open the event |
| `referee`, `official` | "Referee?" / "Official?" (`status` `invited`), a confirmed duty (`accepted`) or a "can't make it" (`declined`, kept until the game starts) | respond to `signup_id` (a declined one can still be accepted) |
| `blocked` | the event greyed out with `blocked_requirements` ("Medical certificate") | none: the club must clear it |

### Event

`GET /events/{id}`: the event, `answers` (one per invited managed person, with their `note`),
a published `lineup`, `referee_signups` / `official_signups` (invited, accepted, or declined until
the game starts -- the same rule as the calendar), `tasks`, `blocked` people and the squad's
`squad_summary` (counts only).

**Answering** (`POST /events/{id}/rsvp`):

```json
{"member_id": "…", "status": "present" | "absent" | "maybe" | "dropout", "note": "Sick"}
```

* `absent` needs a reason (`note`); `maybe` may have one; `present` clears it.
* Once a line-up is published, answers are read-only. A person with `can_drop_out: true` may
  still send `dropout` with a reason: the coach is told.
* Refusals: `409 rsvp_closed`, `409 event_started`, `409 not_in_lineup`, `422 note_required`.
* It returns the stored answer, and repeating it is harmless.

**Referee / official invites:** `POST /events/{id}/referee-signups/{signup_id}/respond` (or
`official-signups`) with `{"response": "accept" | "decline"}`. `409 signup_unavailable` means it
couldn't be accepted (e.g. full).

**Tasks** ("bring fruit"): `POST` or `DELETE /events/{id}/tasks/{task_id}/claim`. A slot is
taken by the account itself, not per child. Others see it as "<Family> family".


### Following games

An account can follow **one game** or **all games of a team**; either gets the live updates (the
same goal / penalty / result pushes, `kind: "game_moment"` in the payload) and a card on Home while
the game is on. Both need the club's `features.game_timeline`.

| Action | Call |
|---|---|
| follow / unfollow one game | `PUT` / `DELETE /events/{id}/follow` (idempotent; answers `{directly, via_team}`; `422 not_a_game` for anything but a game) |
| follow every game of a team | `PUT /game-follows` with the full set of `team_ids` (replaces the current set) |
| what is followed | `GET /game-follows` → `teams` (each with `following`) and `games` (single games followed directly, to offer an unfollow) |
| is *this* game followed? | `GET /events/{id}` → `follow: {directly, via_team}` |

Show a Follow toggle on a game's page from `follow.directly`; when `follow.via_team` is true the
game is followed regardless, and `DELETE` only removes the direct follow (the answer still says
`via_team: true`), so offer unfollowing the team in the team list instead. A game followed both ways
is one card on Home (`via: ["game", "team"]`).

### News

`GET /news?cursor=…` (newest first, paged) and `GET /news/{slug}`. Titles and texts follow
`Accept-Language`: English when the club wrote an English version, the club's own language
otherwise (`language` says which). `body_html` is sanitised HTML: render it in a text view that
supports basic HTML, or a non-scripting `WKWebView`. Crop photos around `focal_x` / `focal_y`.
`author` has the writer's first and last name (null when unknown).

### Notifications

`GET /notifications?cursor=…&member_id=…` (newest first), `GET /notifications/unread-count`
(set the app icon badge from it), `POST /notifications/{id}/read`,
`POST /notifications/read-all`, and `DELETE /notifications` (clears the inbox for good).

### Profiles and photos

`GET /members/{id}` for a managed person: contact details, guardians, current teams and open
club requirements. `PATCH /members/{id}` with only the fields that change (`first_name`,
`last_name`, `date_of_birth`, `email`, `phone`, `emergency_phone`, `photo_public_consent`);
`422` lists invalid fields. Phone numbers come back as E.164 (`+32470123456`).

`photo_url` (and `GET /members/{id}/photo`) needs the Bearer token, so load it with your own
`URLRequest` (not `AsyncImage`). The URL changes when the photo does, so cache it by URL.

**New photo:** `POST /members/{id}/photo` as `multipart/form-data` with a `photo` part (JPEG,
PNG or WebP, at most 10 MB; convert HEIC from the camera to JPEG and downscale to ~1600 px
first) and optionally `photo_public_consent=true|false`. `DELETE /members/{id}/photo` removes
it. Both return the updated profile.

### Payments & dues

`GET /payments`: what the family still owes and the club's `payment_instructions` (plain text:
bank account, reference). There's **no in-app payment**; show the instructions and the amount.
Each item is either a `registration` (one invoice for several people, with the PDF at
`GET /payments/registrations/{registration_id}/invoice.pdf`) or one person's `membership`
dues. `early_payment`, when present, is the smaller `total` to pay by its `deadline`. Open the
PDF with Quick Look or share it; it needs the Bearer token, so download it yourself.

### Forms

`GET /forms` lists every form the family was asked to fill in (one row per person; done when
`submitted_at` is set). `GET /forms/{send_id}` gives the fields to render:

| `type` | Control | Value to send |
|---|---|---|
| `text`, `email` | text field | string |
| `textarea` | multi-line text | string |
| `number` | decimal keypad | string or number |
| `date` | date picker | `"YYYY-MM-DD"` |
| `choice` | single choice from `options` | the option's `value` |
| `multichoice` | several from `options` | list of `value`s |
| `checkbox` | toggle | `true` / `false` |
| `file` | document or photo picker (`file_accept`, ≤ `max_file_bytes`) | a file part (multipart only) |

Group fields under their `section`. If a form has a `type` the app doesn't know, open the form
on the website instead.

**Submit** with `POST /forms/{send_id}/submit` and an `Idempotency-Key` header, for one of the
form's `addressed` people:

* no file fields: JSON `{"member_id": "…", "answers": {"allergies": "None", "size": "M"}}`;
* with a file field: `multipart/form-data` with a `member_id` part, one part per field `key`
  (repeat the key for each `multichoice` value), and the files.

`201` returns the stored response. `422` has messages per field `key` in `fields`;
`409 form_unavailable` means the form is closed, not open yet, or already answered as many times
as allowed. `GET /forms/submissions/{id}` shows a submitted response.

### Shop

Only when `/clubs` → `features.shop` is on; hide the tab while `shop_open` is off (the
catalogue, cart and checkout then answer `409 shop_closed`, but orders stay readable).

* `GET /shop/products`: products and categories. A product with `variants` needs one picked
  (`M`, `L`, …); `personalization_enabled` offers a number and name to print.
* `POST /shop/cart/items` `{"product_id", "variant_id", "quantity", "beneficiary_id",
  "personalization_number", "personalization_name"}` adds to the cart and returns the whole
  cart. `beneficiary_id` is the managed person it's for; show a person picker when there's
  more than one. Send an `Idempotency-Key`.
* `GET /shop/cart?code=SUMMER10`: the cart re-priced to what checkout will charge, with a
  preview of a discount code (`discount` or `discount_error`). Lines with `unavailable: true`
  must be removed first; when `prices_changed` is true, tell the user.
* `PATCH /shop/cart/items/{id}` `{"quantity": 3}` (0 removes), `DELETE /shop/cart/items/{id}`.
* `POST /shop/orders` `{"discount_code": "…"}` with an `Idempotency-Key` places the order. It's
  **paid at the club on pickup**, never in the app. `409 checkout_failed` carries the reason
  in `message`.
* `GET /shop/orders` (paged), `GET /shop/orders/{id}` and `GET /shop/orders/{id}/invoice.pdf`.

### Registering for a season

Show "Register" when `GET /registration/options` returns seasons (empty: registration is
closed). With several seasons, `season` is null: let the user pick, then call again with
`?season_id=`.

1. Per person, collect entries: a managed person (`member_id`) or someone new to the family
   (`first_name`, `last_name`, `date_of_birth`); `entry_kind` (`player` / `volunteer`); a
   `variant_id` from `products` (only products whose `registration_kind` is empty or matches);
   optionally a `team_id`. A person may have several entries (two teams, or player and
   volunteer).
2. `POST /registration/quote` `{"season_id", "entries": [...]}` prices it without saving. Show
   the lines, `total` and `early_payment_total`. For a player whose team has `jersey_numbers`,
   offer those (plus the person's own number from `current_numbers`, as "keep #N") and send
   the choice as `jersey_number`.
3. `POST /registration` with the same body and an `Idempotency-Key` submits it. The club
   reviews it and sends the invoice, which then appears under Payments; the account gets a
   confirmation email now.

`422` names the entry and field, e.g. `entries.1.variant_id` or `entries.0.jersey_number` (a
number taken in the meantime).

### Calendar subscription

`POST /calendar-feed` returns `webcal_url`: open it with `UIApplication.shared.open` and iOS
offers to subscribe. It covers the family's events in every club. The link is shown **only in
that response**; `GET /calendar-feed` just says whether one exists. Calling `POST` again makes a
new link and the old one stops working (use it as "reset link" when one leaked).

### Bug reports

`POST /bug-reports` `{"title": "…", "description": "…"}` files a report as the signed-in person;
`GET /bug-reports` lists them with the RosterChief team's replies (`notes`) and `status`. Add the
app version and iOS version to the description yourself.

## 9. Coach screens

The Manager switch of the app. Coach endpoints live on the club host under
`/teams/{team_id}/…`. The team comes from `GET /context` → `staffed_teams`; the API keeps no
"active team", so send the id on every call. **A team the account doesn't staff this season is
`404 not_found`**, the same answer as a team that doesn't exist.

### Who may do what

Every `staffed_teams` item says what the account may do there. Hide a button when its flag is
false; calling it anyway answers **`403 manager_required`**.

| Field | Meaning |
|---|---|
| `role` | `manager`: a management position on the team (head coach, team manager, club admin who staffs it). `coach`: any other staff position (assistant, physio). |
| `position` | The account's own position name, for display. |
| `can_manage_attendance` | Check players in, remind silent players. |
| `can_edit_events` | Create, edit, delete and detach events and series, quick-create locations and opponents. |
| `can_publish_lineup` | Change and publish the line-up. |
| `can_edit_roster` | Add, edit and remove players and staff. |

Today all four equal `role == "manager"`. Read the flags, not the role, so a later split needs
no app update. **Reading is open to every coach**: today, the attendance sheet, events, line-up
and roster. Financial data is never returned to a coach.

### Today

`GET /teams/{team_id}/today`: `squad_count`; `event` (today's session if one is still current,
else the next one, as `EventOut`) with `is_today`, `closes_at` (its reply deadline),
`in_count` (present, selected or maybe), `out_count` (absent, excused, not selected) and
`silent_count`; and `needs_you`, a list of `{type, event_id, title, start, count}`:

* `silent_players`: `count` players haven't answered for `event_id`. Open its attendance sheet.
* `unpublished_lineup`: the game or tournament `event_id` has no published line-up (`count` is
  null). Checked for the team's next five games, not only `event`.

Ignore a `type` you don't know.

### Attendance

`GET /teams/{team_id}/events/{event_id}/attendance?filter=responded|silent|declined|all` returns
`{event, filter, counts, items}`. `counts` always cover everybody; `items` follow `filter`
(default `responded`: in or maybe, the web default, since nobody expects a silent or declined
player to show up). One row per person: `attendance_id`, `member_id`, `first_name`,
`last_name`, `jersey_number`, `position`, `status`, `note` (the reason the player gave),
`showed_up` (null until checked in) and `photo_url` (null when there is no photo or the account
may not see it; fetch it with the bearer token like the member photos).

`POST …/check-ins` `{"items": [{"attendance_id", "showed_up": true|false|null}]}` records the
check-in (`null` clears it). Send only the rows that changed; repeating a call changes nothing.
An `attendance_id` that isn't a row of this event fails the whole call with
**`422 unknown_attendance`** (`fields.items` lists the ids) and saves nothing. The response is
the sheet again; pass the same `filter` as a query parameter to get the list you are showing.
**Managers only.**

`POST …/remind-silent` sends the reminder to everyone still silent and answers
`{"notified": n}` (0 when everybody answered). It notifies, so send an `Idempotency-Key`: a
retry with the same key doesn't remind twice. **Managers only.**

**Push to players for a reminder:** an ordinary inbox notification (`kind: "notification"`,
`target: {"type": "event", "id": <event id>}`, `thread-id` = the club slug), titled with the
event and worded "Reminder: Training on Tue 14 Oct, 18:30 still needs your answer.", in the
device's language.

### Events and series

All under `/teams/{team_id}`. Reading is open to every coach; **writes are managers only**
(`can_edit_events`, else `403 manager_required`). Events use the member app's `EventOut` plus
what editing needs (`CoachEventOut`): `series` (`{id, rrule, frequency, interval, weekdays,
detached}`, null for a one-off), `location_id`, `opponent_id`, `is_friendly`, `max_referees`,
`max_officials` (null when the club doesn't use officials), `invited_member_ids` and
`excluded_member_ids`.

| Call | |
|---|---|
| `GET …/events?from&to&kind&cursor&limit` | The team's events, soonest first from `from` (default now; pass a past time to look back), cancelled ones included. `{items, next_cursor}`. |
| `GET …/events/{id}` | One event. |
| `POST …/events` | Create. `kind` is `training`, `game`, `tournament` or `meeting`. The audience is the team plus `invited_member_ids` minus `excluded_member_ids` (people from `GET …/roster` once that ships). `notify` (default true, as on the web) tells everyone invited. Send an `Idempotency-Key`. |
| `PATCH …/events/{id}` | Send only what changes; `null` clears `end`, `gathering`, `deadline`, `location_id`, `opponent_id`. Also takes `score_for`, `score_against`, `is_live`. Answers `{event, occurrences_updated}`. |
| `DELETE …/events/{id}?keep_record&notify` | A one-off event is deleted. An occurrence of a series is removed and not regenerated, or with `keep_record=true` kept and marked `cancelled`. `notify=true` tells everyone invited. `204`. |
| `POST …/events/{id}/detach` | The occurrence stops following its series. |
| `GET/POST …/series`, `GET/PATCH/DELETE …/series/{id}` | The recurrence itself: `frequency` (`weekly` or `monthly`), `interval`, `weekdays` (`MO`…`SU`), `dtstart`, `until`, `duration_minutes`, `gathering_minutes_before`, `deadline_minutes_before`, plus title, kind, location, opponent and the invited / excluded people. Creating generates the next 90 days; a nightly job extends it. Deleting removes every occurrence, past ones too: to keep history, `PATCH` `until`. |
| `GET/POST …/locations`, `GET/POST …/opponents` | Pickers (`{items}`, `?q=`) and quick create. Send an `Idempotency-Key` on the create, or a double tap makes two. |

**`scope` on `PATCH …/events/{id}`** decides what happens to the rest of a series:

* `this` (default): only this event. If the change touches something the series sets (title,
  kind, place, opponent, end / gathering / deadline, invited / excluded), the occurrence is
  **detached** so the next series edit doesn't undo it (`series.detached` turns true).
* `this_and_following`: this and every later occurrence. The series is split in two: the old one
  stops just before this occurrence, a new one (with the change) takes over. Earlier occurrences
  are untouched.
* `all`: every upcoming occurrence that still follows the series (not detached ones, not past
  ones); the series' template changes too.

`start`, `competition`, `is_friendly`, `max_referees`, `max_officials`, the scores and `is_live`
are never part of a series template, so they always change this event only. `all` or
`this_and_following` on a one-off event: `422 not_in_series`. Changing `end`, `gathering` or
`deadline` for a series sets its durations from *this* occurrence (minutes before / after its
start). `PATCH …/series/{id}` is the way to change the pattern (weekdays, interval, `dtstart`): it
only decides future dates, existing occurrences keep theirs, and ones that no longer fit stay until
deleted.

**Validation** is the web form's: a `422` lists `fields` named after the JSON keys
(`location_id`, `invited_member_ids`, `competition`, …; `non_field_errors` for the rest). A game
is called `<team> vs <opponent>` automatically (`<opponent> vs <team>` when the location is not
the club's own ground); another kind with no `title` is called after its kind.

**Push to players:** a new event (`notify`) is an inbox push, `kind: "notification"`, `target:
{"type": "event", "id"}`: "New game: Tue 14 Oct, 18:30. Let us know if you can make it.", sent
in the background to everyone who still has to answer. A change (`notify` on `PATCH`) says
"Game changed: it is now on Tue 14 Oct, 18:30." with the same target. A cancel (`notify` on
`DELETE`) says "Game on Tue 14 Oct, 18:30 has been cancelled."; its target is the event when it
was kept, otherwise `{"type": "calendar"}` since the event is gone. A series never notifies per
occurrence. Nothing is sent unless asked for, apart from a new single event.

### Line-up

For games and tournaments (`404` for other kinds). All under `/teams/{team_id}/events/{event_id}/lineup`.

* `GET`: a **pure read**, any coach. Nothing is created by asking. `status` is `none` (nothing
  saved), `draft`, `scheduled` (`scheduled_publish_at`) or `published` (`published_at`);
  `selected_count`; `pending` (who publishing now would tell: `selected_member_ids`, `dropped_member_ids`;
  empty when the saved picks equal what players were last told); and `members`, everybody invited,
  ordered by `position_order`, then name. Each member: `first_name`, `last_name`, `jersey_number`,
  `position`, `rsvp_status`, `note`, `available` (false for `absent`, `excused`, `no_response`: they
  can't be picked), `selected`, `can_drop_out` (selected in a *published* line-up and the event has
  not started: the player can still pull out in their own app), `attendance_rate` (percent, this
  season; null before any reply) and `photo_url`.
* `PUT {"selected_member_ids": [...]}`: replaces the whole set of picks and answers with the saved
  state. **Saving only stores picks**: nobody is told and nobody's answer changes, even when the
  line-up is already published. An id that isn't invited or isn't available fails the whole call
  with `422 invalid_selection` (`fields.selected_member_ids` lists them). **Managers only**
  (`can_publish_lineup`).
* `POST …/publish {"mode": "now" | "schedule" | "cancel", "at"}`, **managers only**. Answers
  `{lineup, notified}`.
  * `now` writes the picks into the players' answers (`selected` / `not_selected`; players who were
    `absent`, `excused` or silent stay as they are) and tells **only the players whose situation
    changed since the last publish**; `notified` lists exactly who. A repeat publish with no changes
    notifies nobody. The first publish with nobody picked is `409 lineup_empty`.
  * `schedule` needs `at`, in the future (`422` with `fields.at` otherwise) and picks already saved;
    the server publishes by itself at that time with the same rules.
  * `cancel` drops a scheduled publish.
  Send an `Idempotency-Key`.

**Push to players:** an inbox push, `kind: "notification"`, `target: {"type": "event", "id"}`.
Newly selected: title "Line-up published", "You're in the line-up for Derby." Dropped since the
last publish: title "Line-up updated", "The line-up for Derby has changed -- you're not in it this
time." (both in the device's language). When a selected player later pulls out (`dropout`), the
event's managers get "Line-up dropout" the same way; reload the line-up when you see it.

## 10. Other rules

* **Maintenance:** any call can answer **503 `maintenance`** with `Retry-After`. Show a
  maintenance screen and retry later.
* **Retries:** for writes, send an `Idempotency-Key` (a new UUID per user action, reused on
  retries of that action).
* **Offline:** cache what you show. Refetch on pull-to-refresh and when the app returns to the
  foreground.
* **Errors:** see [Errors](../errors/).
