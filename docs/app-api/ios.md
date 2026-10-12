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

Club staff (admins, editors, team managers, shop/evaluation managers) must have two-factor.
Until they enrol, every app-API call answers **403 `mfa_enrollment_required`**. Show an enrolment
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

## 9. Other rules

* **Maintenance:** any call can answer **503 `maintenance`** with `Retry-After`. Show a
  maintenance screen and retry later.
* **Retries:** for writes, send an `Idempotency-Key` (a new UUID per user action, reused on
  retries of that action).
* **Offline:** cache what you show. Refetch on pull-to-refresh and when the app returns to the
  foreground.
* **Errors:** see [Errors](../errors/).
