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
  "id": "…", "slug": "hc-example", "name": "HC Example",
  "api_base_url": "https://hc-example.rosterchief.app/api/app/v1",
  "logo_url": "https://…", "primary_color": "#1e40af", "secondary_color": null,
  "relationship": ["guardian", "coach"],
  "features": {"shop": true, "shop_open": false, "forms": true, "officials": false, "evaluations": false}
}]}
```

* `relationship`: `member` (plays or is signed up), `guardian` (acts for children), `coach`
  (staffs a team this season), `staff` (club admin, editor or member admin). Ignore values you
  don't know; more may come.
* Exactly one club: open it. More: show a picker and remember the choice.
* Use `api_base_url` for every club call. Theme the club with `primary_color` and `logo_url`.
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
4. The response's `push_enabled` is `false` until the server has push delivery set up. Don't show
   an error for that.

**Payload** (arrives in `userInfo`): an `aps.alert` with a translated title and body, plus
`notification_id`, `club_slug`, `type` and `target` (`{"type": "event", "id": "…"}`). The
payload is only a pointer: fetch the details over the API. It never contains personal data.
*(The server starts sending these with the member endpoints; registration works now.)*

## 8. Other rules

* **Maintenance:** any call can answer **503 `maintenance`** with `Retry-After`. Show a
  maintenance screen and retry later.
* **Retries:** for writes, send an `Idempotency-Key` (a new UUID per user action, reused on
  retries of that action).
* **Offline:** cache what you show. Refetch on pull-to-refresh and when the app returns to the
  foreground.
* **Errors:** see [Errors](../errors/).
