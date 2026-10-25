# API.md — Native-app API (design)

Status: **phase 3 (member write/commerce) done on the server side** (2026-09-29): with
phases 1-2, every member screen of the PWA now has an app API equivalent (§14). **Developer
docs are online at `/api/docs/`** (Markdown in `docs/app-api/`, iOS first), next to the live
OpenAPI references, and `docs/app-api/openapi-app-v1.json` is the committed snapshot clients
are generated from. Push setup for operators is in `DEPLOYMENT.md` "Native app push". Items
that need real devices or cloud accounts (Android passkey prototype, SNS round trip) are
still open (§14, §16). Branch `API`, drafted 2026-09-20. Companion to `ARCHITECTURE.md` (models, tenancy,
RBAC), `DEPLOYMENT.md` (Caddy/AWS topology) and `BILLING.md`.

Where this document and the code disagree, the code wins. Statements about what allauth, ninja or the current views do were checked against the installed sources (allauth 65.18, django-ninja 1.6.2) and the `mobile/*` views on 2026-09-20, and re-checked
against development at 0.7.12 on 2026-09-29; items still unverified are marked **UNVERIFIED** and collected in §16.

---

## 1. Goals, non-goals, and the decision this reverses

**Why.** The mobile presence is a PWA (`/app/`, the `mobile` app). We may later ship real iOS and Android builds. A native app cannot ride on the server-rendered, cookie-session, htmx-flavoured views, so it needs a first-class authenticated API. This
**supersedes the earlier "mobile is a PWA, not native" decision** — the PWA stays as-is; the API makes a native client *possible*, it does not retire the PWA.

**Goals (v1)**

- Member + coach parity with the current PWA: everything under `mobile/views.py` and
  `mobile/coach_views.py` (home, calendar, RSVP, news, notifications, profile, payments, forms, shop, re-registration, calendar feed, bug reports; coach today/attendance/lineup/roster/events/ news/evaluations).
- Real authentication, including the existing MFA (TOTP, recovery codes, passkeys), designed for a native client.
- Native push (APNs + FCM) through **AWS**, next to the existing Web Push.
- One API contract that also serves as the OpenAPI source for client codegen.

**Non-goals (v1)**

- Management/admin (`/manage`, control panel, billing, shop administration, formbuilder admin). Stays web-only.
- Tournaments (lives on the separate `tournaments` branch — see §14 for how it slots in).
- Online payments. Checkout is pay-at-pickup today; there is no payment provider (§12).
- Replacing or changing the public read-only `/api/v1/` (§2).
- Real-time channels. The deployment is WSGI/gunicorn; the only server-initiated channel is push.
- Offline-first sync engine. We give clients cheap "what changed since" reads (§9), not CRDTs.

---

## 2. Surface layout and coexistence with `/api/v1/`

Today `/api/v1/` is an **unauthenticated, read-only, club-scoped** ninja API with `Access-Control-
Allow-Origin: *` (`api/urls.py`, `api/middleware.py::PublicApiCorsMiddleware`, GET/OPTIONS only). It must not gain authenticated endpoints: a wildcard-CORS prefix is the wrong place for them.

| Prefix              | Host                             | What                                                       | Auth           |
|---------------------|----------------------------------|------------------------------------------------------------|----------------|
| `/api/v1/`          | club subdomain                   | existing public API (unchanged)                            | none           |
| `/api/auth/app/v1/` | **base domain**                  | allauth.headless, app client (login, MFA, tokens, account) | public / token |
| `/api/app/v1/`      | base domain *and* club subdomain | the authenticated app API (this doc)                       | token          |

- `/api/app/v1/` is a **second `NinjaAPI`** (`urls_namespace="app_api"`, own `title`/`version`, own OpenAPI). Sharing an instance with the public API would leak auth defaults and schema.
- **No CORS** on `/api/app/` or `/api/auth/`. Native clients don't need it, and its absence keeps browsers from calling these from arbitrary origins. `PublicApiCorsMiddleware` already only matches `/api/v1/`; keep it that way and add a test that
  asserts no `Access-Control-*` header on the new prefixes.
- Versioning is **URL-major** (`v1`). Within a major version changes are additive only (new optional request fields, new response fields, new endpoints, new enum values — clients must ignore unknown fields and tolerate unknown enum values). Breaking
  change ⇒ `v2` mounted beside
  `v1`. Deprecation is announced via a `Sunset`/`Deprecation` header and `GET /api/app/v1/meta`
  (`min_supported_app_version`, see §15).

---

## 3. Authentication

### 3.1 Choice: allauth.headless, app client

Already decided with the product owner. It reuses the existing account stack (email login, TOTP, recovery codes, WebAuthn/passkeys, rate limits, password reset) instead of a second identity system, and it is the only option that gives the app **the
same MFA** as the web. No DRF, simplejwt or oauth-toolkit is added.

Implemented in phase 0 (`rosterchief/settings.py`, `rosterchief/urls.py`):

```python
INSTALLED_APPS += ["allauth.headless"]  # + allauth.usersessions in phase 5 (§3.7)
HEADLESS_CLIENTS = ("app",)  # mount only the app client; no browser client
HEADLESS_TOKEN_STRATEGY = "allauth.headless.tokens.strategies.jwt.JWTTokenStrategy"
HEADLESS_JWT_STATEFUL_VALIDATION_ENABLED = True
HEADLESS_JWT_ALGORITHM = "HS256"  # allauth's default is RS256, which needs HEADLESS_JWT_PRIVATE_KEY
HEADLESS_JWT_ACCESS_TOKEN_EXPIRES_IN = 600  # env-overridable
APP_SESSION_LIFETIME = 90 * 24 * 3600  # ours; env-overridable (§3.3)
HEADLESS_JWT_REFRESH_TOKEN_EXPIRES_IN = APP_SESSION_LIFETIME
# Do NOT set HEADLESS_ONLY: it removes the HTML /accounts/ URLs the web UI still uses.
```

```python
(path("api/auth/", base_domain_only_urls(include("allauth.headless.urls"))),)  # -> /api/auth/app/v1/...
(path("api/app/v1/", app_api.urls),)  # phase 1 (appapi)
```

`base_domain_only_urls` (the admin's helper) makes every auth route 404 on a club subdomain.

Dependencies: `pyjwt[crypto]` arrives with the `headless` extra (`django-allauth[mfa,headless]`),
added in phase 0; `PyYAML` (the `headless-spec` extra, for `HEADLESS_SERVE_SPECIFICATION`),
and `boto3` (direct) in phase 1.

### 3.2 Endpoints (base domain, prefix `/api/auth/app/v1`)

| Method + path                                                                            | Meaning                                                                              |
|------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `GET /config`                                                                            | public: login methods, MFA types, `passkey_login_enabled`, `is_open_for_signup`      |
| `POST /auth/login`                                                                       | `{email, password}` → 200, or **401** with pending `flows` (MFA)                     |
| `GET /auth/session`                                                                      | who am I / is the session complete (401 + flows if not)                              |
| `DELETE /auth/session`                                                                   | log out (flushes the server session ⇒ token dead)                                    |
| `POST /auth/2fa/authenticate`                                                            | `{code}` TOTP or recovery code — completes the pending MFA stage                     |
| `GET/POST /auth/webauthn/authenticate`                                                   | security-key/passkey assertion as MFA second factor                                  |
| `GET/POST /auth/webauthn/login`                                                          | **passkey first-factor login** (no password, no MFA prompt)                          |
| `POST /auth/reauthenticate`, `/auth/2fa/reauthenticate`, `/auth/webauthn/reauthenticate` | step-up before sensitive changes                                                     |
| `POST /auth/password/request`, `POST /auth/password/reset`                               | reset flow (`GET` validates the key from `X-Password-Reset-Key`)                     |
| `POST /account/password/change`                                                          | change password (authenticated)                                                      |
| `GET/POST/PUT/DELETE/PATCH /account/email`                                               | list/add/verify/remove/primary email                                                 |
| `GET /account/authenticators`                                                            | list enrolled MFA methods                                                            |
| `GET/POST/DELETE /account/authenticators/totp`                                           | enrol (GET returns `meta.secret`, `meta.totp_url`), activate `{code}`, remove        |
| `GET/POST /account/authenticators/recovery-codes`                                        | view / regenerate                                                                    |
| `GET/POST/PUT/DELETE /account/authenticators/webauthn`                                   | register / rename / remove passkeys                                                  |
| `POST /tokens/refresh`                                                                   | `{refresh_token}` → new access (+ rotated refresh) — only with a refreshing strategy |
| `GET /openapi.json`, `/openapi.yaml`                                                     | spec (when `HEADLESS_SERVE_SPECIFICATION`)                                           |

Unused-but-mounted routes we accept as harmless (login-by-code, phone) are off by their own settings; `POST /auth/signup` needs the fix below.

### 3.3 Token strategy — recommendation

allauth ships two strategies:

- **Session (default):** the token *is* the Django session key, sent as `X-Session-Token`. Revocable (delete the row), but lifetime is plain Django session expiry (≈ 2 weeks from the last session *write*; the ninja auth never writes, so it does not
  slide).
- **JWT (`allauth.headless.tokens.strategies.jwt.JWTTokenStrategy`):** short-lived access token (default 300 s) + rotating refresh token (default 86 400 s), in `meta.access_token` /
  `meta.refresh_token`; `HEADLESS_JWT_STATEFUL_VALIDATION_ENABLED=True` makes access tokens die with the underlying session (revocation). Sent as `Authorization: Bearer …`.

**Decided (2026-09-20): the JWT strategy with stateful validation on**, refresh lifetime raised to the
"stay signed in" horizon (target **90 days**, rotating on every refresh), access token **5–15 min** (set to 10). Reasons: short-lived bearer credentials limit the blast radius of a leaked access token; refresh rotation gives theft detection; stateful validation
keeps *server-side revocation* (logout,
"sign out other devices", admin lock-out) which is the property a club app handling minors' data needs. Algorithm HS256 via `SECRET_KEY` is acceptable for a single deployment (tokens are only ever validated by us); use RS256 +
`HEADLESS_JWT_PRIVATE_KEY` if a second verifier ever appears. **Consequence:** rotating `SECRET_KEY` (without a separate JWT key) signs every app user out, because the session id inside each token is also encrypted with `SECRET_KEY`, and
`SECRET_KEY_FALLBACKS` isn't consulted.

**Resolved in the phase-0 spike (§16-1)** — read from allauth 65.18's source, pinned by
`authentication/test_headless_app.py`:

- **A refresh token only works while its server session exists.** The set of valid refresh tokens (`jti → exp`) is stored *in* the session, so an expired or deleted session kills every refresh token for it, whatever the token's own `exp` says.
- **App logins get a 90-day session; web logins don't change.** `authentication/signals.py` handles `user_logged_in` and, only when `request.allauth.headless.client == "app"`, calls `request.session.set_expiry(APP_SESSION_LIFETIME)`. That signal fires when
  the login *completes*, i.e. after the MFA stage, and for passkey logins too.
- **Refresh slides the session.** `/tokens/refresh` ends with `session.save()`, and a relative `set_expiry` recomputes `expire_date` from now on every save. An app in regular use stays signed in; one idle for 90 days has to log in again.
- **Refresh rotates**, and replaying a used refresh token fails (400). Logging out (`DELETE /auth/session`) or deleting the session kills live access tokens at once (stateful validation → 401).
- The JWT strategy doesn't fight us, so no custom `AbstractTokenStrategy` is needed.

Tokens live in the platform secure store (iOS Keychain / Android Keystore-backed storage), never in plain preferences or logs. Refresh is single-flight in the client (one refresh at a time, queue the rest) — rotation makes concurrent refreshes look
like replay.

### 3.4 Our own ninja auth wrapper (required — allauth's is not enough)

`allauth.headless.contrib.ninja.security.jwt_token_auth` (or `x_session_token_auth`) authenticates but stops short of what this platform needs. Write **one** `AppAuth` (a ninja `AuthBase` subclass wrapping allauth's) used by every `/api/app/` router;
it must:

1. Reject inactive users (allauth does) and half-finished sessions (no `SESSION_KEY` — an MFA stage not completed).
2. **Set `request.user`.** allauth's ninja auth puts the user only in `request.auth`; our services (`club/services/access.py`, `has_management_access`, `teams_staffed_by(user, club)`) and
   `request.user`-based code expect it.
3. **Enforce the MFA-enrolment requirement.** `authentication.middleware.RequireMFAMiddleware`
   redirects privileged users (admins, editors, staff) who haven't enrolled MFA — but it inspects the cookie session, and app requests carry only a header, so at middleware time `request.user` is anonymous and it **never fires for the API**. Without
   this step a privileged user could use the whole API on a password alone. `AppAuth` therefore checks `mfa_required_for(user) and not
   is_mfa_enabled(user)` and answers **403 `mfa_enrollment_required`** on everything except
   `/api/auth/…` (where the app can enrol TOTP itself: `GET/POST /account/authenticators/totp`). The app shows an "enrol MFA to continue" screen driven by that code.
4. Not depend on the Django session-auth hash. allauth's ninja auth does **not** verify
   `HASH_SESSION_KEY`, so a password change does not invalidate other sessions on its own. **Decided (2026-09-20):** a `password_changed` receiver flushes the user's *other* app sessions; `ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE` stays off, so web behaviour does not change.
5. Attach nothing about the club: the token proves *who*, never *which club* (§4).

### 3.5 Login sequences

**Password + TOTP** (privileged users, and any member who opted in):

```
POST /auth/login {email,password}                → 401, flows:[{id:mfa_authenticate,types:[totp,recovery_codes]}]
                                                    + meta.session_token   (pre-auth)
POST /auth/2fa/authenticate {code}  (X-Session-Token) → 200 + meta.access_token/refresh_token
```

**Passkey** (skips the MFA stage — passwordless login counts as MFA):

```
GET  /auth/webauthn/login                        → data.request_options + meta.session_token
POST /auth/webauthn/login {credential}  (X-Session-Token) → 200 + tokens
```

The WebAuthn challenge is held in the *server session*, so the client **must** store
`meta.session_token` from the GET and send it on the POST. In general: whenever a response carries
`meta.session_token`, overwrite the stored one.

**Unverified email** (`ACCOUNT_EMAIL_VERIFICATION = "mandatory"`, as on the web): the login answers
**401** with a `verify_email` flow and no tokens. The app has to show "confirm your email first";
the confirmation link goes to the web. Web sign-in has the same requirement, so every account that
can log in today already has a verified address.

**Client notes found in the spike:**
- Logout (`DELETE /auth/session`) answers **401** with `is_authenticated: false`. That is success, not an error.
- A non-GET request with `Content-Type: application/json` must have an empty body or valid JSON. Anything else is a bare 400.

**Recovery code** goes through the same `/auth/2fa/authenticate`.

### 3.6 Passkeys on native — constraints (iOS phase 5, Android after a prototype)

- WebAuthn RP ID is already pinned to the base domain (`RosterChiefMFAAdapter`, via
  `webauthn_rp_id()` and `ROSTERCHIEF_BASE_DOMAIN`). It must be **set in every environment**: if empty it falls back to the request host, which would be wrong for club-subdomain calls.
- **iOS:** ASAuthorization sends `https://<rpId>` as the origin, so allauth's default check passes. Needs the *Associated Domains* entitlement `webcredentials:rosterchief.app` and an
  `apple-app-site-association` file served over HTTPS at
  `https://rosterchief.app/.well-known/apple-app-site-association` (Caddy static or a Django view).
- **Android: confirmed server-side, still needs a device.** Credential Manager reports the origin for a native app as `android:apk-key-hash:<hash>`. Checked in phase 0: fido2's default check (`fido2.rpid.verify_rp_id`) returns **False** for that origin with RP ID
  `rosterchief.app`, and allauth only exposes an all-or-nothing `MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN`. `Fido2Server` does accept a `verify_origin` callable, but allauth builds it in the module-level `get_server()` in
  `allauth/mfa/webauthn/internal/auth.py`, which that module's own functions look up by name. The fix is to replace that function at app start-up with one that accepts `verify_rp_id(...)` **or** an origin in our allow-list of APK signing-key hashes. It's a
  patch on an allauth internal, so it needs a test that fails when allauth changes it. Also needs Digital Asset Links at `/.well-known/assetlinks.json` (`delegate_permission/common.get_login_creds`). What's left needs a device: that a passkey created on the web asserts from
  the Android app with this origin.
- **Decided (2026-09-20):** v1 ships **password + TOTP/recovery** on both platforms; **iOS passkeys** in a follow-up; **Android passkeys** only after a prototype proves the origin check. Existing web passkeys keep working — same RP ID, same credentials, as long
  as the RP ID does not change.

### 3.7 Sessions / devices (phase 5)

`DELETE /auth/session` covers "log out this device". "Signed-in devices" + "sign out others" needs
`allauth.usersessions` (`GET/DELETE /auth/sessions`), which is **not installed** (new app, migration, `UserSessionsMiddleware`). **Decided (2026-09-20): deferred to phase 5**; launch has "log out this device" only (revisit if the security review asks for it). Pair it with a per-install `device_name` sent on login so
the list is readable.

### 3.8 Hardening checklist

- **Signup is closed (done, phase 0).** `RosterChiefAccountAdapter.is_open_for_signup` returns `False`, so `POST /auth/signup` answers 403 and `/config` says `is_open_for_signup: false`. The web's own URL override (`signup_closed`) stays.
- **Rate limiting** is allauth's (`ACCOUNT_RATE_LIMITS`; defaults e.g. login 30/m/ip,
  `login_failed` 10/m/ip + 5/300s/key, reset 20/m/ip + 5/m/key → HTTP 429; MFA code entry shares
  `login_failed`). The client IP is **fixed on development** (security audit): `ALLAUTH_TRUSTED_CLIENT_IP_HEADER = "X-Real-IP"`, which Caddy sets on every proxied request, read through `RosterChiefAccountAdapter.get_client_ip` (falls back to
  `REMOTE_ADDR`). No `ALLAUTH_TRUSTED_PROXY_COUNT` needed. Behind Cloudflare, Caddy's `trusted_proxies` makes `X-Real-IP` the visitor, not Cloudflare.
- **Default cache** is Redis when `REDIS_URL` is set, else locmem — rate limits are only meaningful across gunicorn workers with Redis. Fine in prod; note in tests.
- **Password-reset link:** without `HEADLESS_FRONTEND_URLS["account_reset_password_from_key"]` the email links to the *web* `/accounts/password/reset/key/…` page, which works from a phone browser. Keep that for v1; move to a universal/app link later.
- **Maintenance mode** (`features.middleware.MaintenanceMiddleware`) closes club hosts and all but a few base-domain paths. `/api/` should return a **503 JSON `{code: "maintenance"}`** rather than the HTML page (§15), and the client should show a
  maintenance screen.
- **Email verification** is `"mandatory"` (security audit): login requires a verified address, and so does MFA enrolment. See §3.5 for what the app shows. Since web sign-in has the same rule, this adds no new gap for existing users (§16-6).

---

## 4. Tenancy: base-domain account plane, club-host resource plane

Decided: **club subdomain host + base-domain account endpoints.**

- **Account plane** (base domain, `request.club is None`): auth (§3), `GET /api/app/v1/me`,
  `GET /api/app/v1/clubs`, device/push registration, `meta`.
- **Resource plane** (club host `https://<slug>.rosterchief.app/api/app/v1/…`): everything else.
  `ClubTenantMiddleware` already resolves `request.club` from the `Host`, so the API reuses
  `api.errors.require_club` (404 when no club). Archived/unknown slugs 404 exactly as the web does.

The token is **host-independent** (session/JWT lookup uses no host, cookie or club), so one login serves every club the user belongs to; the client just changes the base URL. `SESSION_COOKIE_DOMAIN`
and CSRF are irrelevant (no cookies; allauth app views are `csrf_exempt`, and ninja's `csrf` is off).

**Club discovery.** `GET /api/app/v1/clubs` (base domain) returns the clubs the user can act in:

```json
{
  "items": [
    {
      "id": "…uuid",
      "slug": "hc-example",
      "name": "HC Example",
      "api_base_url": "https://hc-example.rosterchief.app/api/app/v1",
      "logo_url": "…",
      "primary_color": "#…",
      "relationship": [
        "member",
        "guardian",
        "coach"
      ],
      // enum, additive
      "features": {
        "shop": true,
        "shop_open": false,
        "forms": true,
        "officials": false,
        "evaluations": true
      }
    }
  ]
}
```

The client caches this, lets the user pick a club (auto-select if exactly one), and stores the
`api_base_url` per club. **Authorization is never in the token**: every club request re-derives what the user may see from `(user, request.club)` via `club/services/access.py`. A valid token on a club the user does not belong to → **403
`not_a_member`** (not 404: it is a known club).

**Deep links / push payloads** carry `club_slug` so a tap can switch club context before routing.

---

## 5. Authorization model

Order of checks, matching the current web behaviour (**flag → role → scope**, so a hidden feature 404s and a forbidden action 403s):

1. **Authenticated** (`AppAuth`, §3.4) → 401 `unauthenticated` / `token_expired` (client refreshes and retries once).
2. **MFA enrolment satisfied** → 403 `mfa_enrollment_required`.
3. **Club resolved** → 404. **User belongs to the club** → 403 `not_a_member`.
4. **Feature flag / `Club.shop_open`** (waffle: `shop`, `formbuilder`, `officials`, `evaluations`)
   → 404 `feature_disabled` for a disabled module; 409 `shop_closed` when the shop exists but is closed (today the web redirects home with a flash).
5. **Role** (`club/services/access.py`) → 403 `forbidden`.
6. **Object scope**: the person or team named in the request must be reachable by the caller → **404** (do not disclose existence of another family's member, order, form response, or a team the coach doesn't staff).
7. Cross-club ids: leaf rows are scoped through a parent (`club` FK on `ClubScopedModel`); every lookup goes through the club-scoped manager so a valid UUID from another club is a 404, never a leak. Add a generic test per endpoint (§13).

### 5.1 Person scope (member parity)

"Managed people" = the caller's own `Member` + children in their `Family` (guardian relationship). The PWA holds the choice in `?as=` and defaults by household size; the API makes it **explicit and stateless**:

- `member_id=<uuid>` names one managed person; `member_id=all` (or omitted where documented) means every managed person, allowed **only on aggregate reads** (home, calendar, notifications, payments, forms, orders, `me`).
- **Writes always name one member** (RSVP, cart beneficiary, profile edit). A `member_id` outside the managed set → 404.
- The managed set is computed by **one** service, `members.services.family.managed_people(member,
  club)`, to be extracted from three divergent copies in the PWA (`CalendarFeedView`,
  `PersonScopeMixin._managed_people`, and `members_visible_to`; the latter omits the
  `member_of__club` filter — reconcile deliberately, do not copy one) (§11).

### 5.2 Team scope (coach parity)

The PWA keeps the active team in the session (`ACTIVE_TEAM_SESSION_KEY`, written even on GET). The API has **no server-held selection**: every coach route carries `team_id` in the path (`/teams/{team_id}/…`) and is validated against
`teams_staffed_by(user, club)`; manager-only actions additionally against `teams_managed_by`. Non-staffed team → 404. Season is the club's current season unless a `season_id` query param says otherwise (same rule as
`current_season(club)`).

---

## 6. Conventions

**Format.** JSON, UTF-8, `snake_case`. IDs are UUID strings. Timestamps are **ISO-8601 UTC with offset** (`2026-09-20T18:30:00Z`); dates are `YYYY-MM-DD`; money is a **decimal string plus
`currency`** (`{"amount": "12.50", "currency": "EUR"}`) — never floats. Enums are the model's stored **value** (never the translated label); clients localise labels themselves and must tolerate unknown values. Presentation concerns (CSS pill classes,
`team_manager_label`, reversed URLs,
"today/earlier/older" grouping headings) are **not** returned — return the data (status enum, team list, `{type,id}` source refs) and let the client render.

**Errors.** One envelope for everything, including allauth's own where we can wrap it:

```json
{
  "error": {
    "code": "rsvp_closed",
    "message": "…localised…",
    "fields": {
      "note": [
        "required"
      ]
    }
  }
}
```

`code` is a stable machine string; `message` is localised (`Accept-Language`, §10) and safe to show;
`fields` only for validation. Mapping: 400 malformed; 401 `unauthenticated`/`token_expired`; 403
`forbidden`/`not_a_member`/`mfa_enrollment_required`; 404 not found *or* not visible; 409 state conflict (`shop_closed`, `already_claimed`); 410 gone; 422 validation (`fields`); 429 rate limit (`Retry-After`); 503 `maintenance`. This replaces the
flash-message + `HttpResponseBadRequest`
pattern in the views: domain exceptions (`RefereeAssignmentError`, `OfficialAssignmentError`,
`TaskClaimError`, `FormSubmissionError(.errors)`, `CheckoutError`, `PricingError`,
`RegistrationError`, `EvaluationSubmissionError(.errors)`, `EvaluationRubricNotConfigured`→404, the invoice PDF errors) map in one ninja exception handler. (allauth's own responses keep their native `{status, data, errors}` shape on `/api/auth/`; the
client has two small parsers. Do not try to re-wrap them.)

**Pagination.** Cursor-based for anything unbounded (`news`, `notifications`, `orders`, calendar windows): `?limit=` (default 25, max 100, `Link`-free) → `{ "items": [...], "next_cursor": "…"|null }`. Cursors are opaque, encode `(sort_key, id)` for
stable ordering. Small bounded collections (clubs, my teams, a coach's roster) return plain arrays under `items` with no cursor. Only
`news_list` paginates today, so this convention is new for the rest.

**Ordering/filters.** Explicit and documented per endpoint; no free-form `?filter=`. Calendar takes
`from`/`to` (max 92-day window) plus `kind=game|training`.

**Idempotency.** State-changing POSTs that a flaky mobile network will retry (`rsvp`, `cart/items`, `orders`, `forms/*/submit`, `registration` submit) accept an
`Idempotency-Key` header; the server stores `(user, key) → response` for 24 h and replays it. RSVP, notification read and check-ins are naturally idempotent (upserts) and additionally return the resulting state so the client can reconcile. Checkout
and registration submission *must* honour the key: a double-tap must not create two orders/batches.

**Uploads** (member photo, news photos, form file fields): `multipart/form-data`, size and type limited server-side (same limits as today's forms), stored via the existing storage backend (S3). No direct-to-S3 presigned flow in v1; revisit if photo
sizes hurt mobile networks.

**Private media** (documents, invoices, private-storage files) are **never** exposed as public S3 URLs. Return an authenticated streaming endpoint (`GET …/invoice.pdf`, `Content-Type:
application/pdf`, `Content-Disposition`) or, later, short-lived presigned URLs minted per request after the same authorization.

**Member photos are private media now** (security audit, 0.7.9). `Member.player_photo` (official) and `Member.profile_photo` (self-service, club-wide, never public) live in private storage, and `Member.photo_url` points at `members:photo`, which checks
`members.services.photos.can_view_member_photo`: the member, their parents/guardians, and staff who may see that member, plus anyone when `photo_public_consent` is on. The web view authenticates by session cookie, so the app API needs its own
token-authenticated photo endpoint (e.g. `GET /members/{id}/photo`) calling the same rule. Don't hand out the web URL.

---

## 7. Resource catalogue (v1)

`M` = member/guardian, `C` = coach (`/teams/{team_id}/…`, staffed-team required; `C+` = manager).
`P` = base-domain account plane. Person scope (`member_id`) and team scope as in §5. All club-host paths are under `/api/app/v1`.

### 7.1 Account plane (`P`)

| Endpoint                                                                       | Notes                                                                        |
|--------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| `GET /me`                                                                      | user, email, linked `Member`(s) across clubs, MFA state, `must_enrol_mfa`    |
| `GET /clubs`                                                                   | §4                                                                           |
| `POST /devices` `{platform, installation_id, push_token, app_version, locale}` | register/refresh this install for push (§9); idempotent on `installation_id` |
| `DELETE /devices/{installation_id}`                                            | unregister (also called on logout)                                           |
| `GET /meta`                                                                    | `min_supported_app_version`, maintenance notice, server time                 |

### 7.2 Member (club host)

| Area          | Endpoints                                                                                                                                                                                                                                                           |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Context       | `GET /context` — caller's `Member`, managed people (self + children), season, role flags, staffed teams, features                                                                                                                                                   |
| Home          | `GET /home?member_id=` — hero RSVP, "going", needs-answer (RSVP + referee/official invites), open task slots, dues, news, forms (caps in the service, not the client)                                                                                               |
| Calendar      | `GET /calendar?from&to&kind&member_id` — merged agenda of attendance, referee/official signups, blocked events; each item tagged `type: rsvp\|referee\|official\|blocked`                                                                                           |
| Events        | `GET /events/{id}` — member view: lineup (if published), squad summary, tasks, `attachments` (files from the team's managers, only when a managed person is invited; download via `GET /events/{id}/attachments/{aid}`), per-person answers, `blocked` requirements, and for games `game_timeline` (goals/penalties, with names; feature `game_timeline`)                                                                                                                                           |
| RSVP          | `POST /events/{id}/rsvp` `{member_id, status: present\|absent\|maybe\|dropout, note?}`                                                                                                                                                                              |
| Officiating   | `POST /events/{id}/referee-signups/{sid}/respond` and `/official-signups/{sid}/respond` `{response: accept\|decline}`                                                                                                                                               |
| Tasks         | `POST /events/{id}/tasks/{tid}/claim`, `DELETE …/claim`                                                                                                                                                                                                             |
| News          | `GET /news?cursor`, `GET /news/{slug}` (`lang` follows `Accept-Language`)                                                                                                                                                                                           |
| Game follows  | `GET /game-follows` — the club's teams with `following`; `PUT /game-follows` `{team_ids}` replaces the set (opt-in live game pushes; feature `game_timeline`, else `404 feature_disabled`) |
| Notifications | `GET /notifications?member_id&cursor`, `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`, `DELETE /notifications` (clear; hard delete, as today) — each carries `source: {type: news\|event\|form_send, id\|slug}` |
| Profile       | `GET /members/{id}` (managed only), `PATCH /members/{id}`, `PUT /members/{id}/photo` (multipart, `photo_public_consent`)                                                                                                                                            |
| Payments      | `GET /payments?member_id`, `GET /payments/batches/{id}/invoice.pdf`                                                                                                                                                                                                 |
| Forms         | `GET /forms`, `GET /forms/{send_id}` (schema: fields, types, options, required), `POST /forms/{send_id}/submit`, `GET /forms/submissions/{id}` (raw values)                                                                                                         |
| Shop          | `GET /shop/products` (excl. `MEMBERSHIP` type), `GET /shop/cart`, `POST /shop/cart/items`, `PATCH/DELETE /shop/cart/items/{id}`, `POST /shop/orders`, `GET /shop/orders`, `GET /shop/orders/{id}`, `GET /shop/orders/{id}/invoice.pdf`                              |
| Registration  | `GET /registration/products`, `POST /registration/quote`, `POST /registration` (re-register managed people; phase 3)                                                                                                                                                |
| Calendar feed | `GET /calendar-feed` (token + URL), `POST /calendar-feed/regenerate`                                                                                                                                                                                                |
| Support       | `POST /bug-reports`, `GET /bug-reports`                                                                                                                                                                                                                             |

### 7.3 Coach (club host, `/teams/{team_id}/…`)

| Area                                                       | Endpoints                                                                                                                                                                                                                                                                             |
|------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Today                                                      | `GET …/today` — squad count, tonight/session event, in/out/silent counts, "needs you" as `[{type: silent_players\|unpublished_lineup, event_id, count}]`                                                                                                                              |
| Attendance                                                 | `GET …/events/{id}/attendance?filter=silent\|declined\|responded`, `POST …/events/{id}/check-ins` `{items:[{attendance_id, showed_up}]}`, `POST …/events/{id}/remind-silent` → `{notified: n}`                                                                                        |
| Events                                                     | `GET/POST …/events`, `GET/PATCH/DELETE …/events/{id}`, `POST/PATCH …/series`, `POST …/events/{id}/detach`; kinds limited to `COACH_EVENT_KINDS`; team is the path's team                                                                                                              |
| Lookups                                                    | `POST …/locations`, `POST …/opponents` (quick create)                                                                                                                                                                                                                                 |
| Lineup                                                     | `GET …/events/{id}/lineup` (**pure read**, no create-on-GET), `PUT …/events/{id}/lineup` `{selected_member_ids}` (replace-set), `POST …/lineup/publish` `{mode: now\|schedule\|cancel, at?}`                                                                                          |
| Roster                                                     | `GET …/roster`, `GET …/roster/candidates?filter=suggested\|no_team&q`, `POST …/roster/players` `{member_ids}`, `POST …/roster/staff` `{member_ids, position_id}`, `PATCH …/roster/{membership_id}`, `DELETE …/roster/{membership_id}` (staff removal of oneself → 403 `self_removal`) |
| News                                                       | `POST …/news` (submit for review, photos multipart; needs `can_add_news`)                                                                                                                                                                                                             |
| Evaluations (flag `evaluations`, `can_manage_evaluations`) | `GET /evaluations/checklists`, `GET …/players/{member_id}/evaluations`, `POST …/players/{member_id}/evaluations` (requires an explicit `checklist_slug` — the web's "exactly one ⇒ auto-pick" is UI-only)                                                                             |

> **As built (phase 4):** see `docs/app-api/ios.md` §9 for the real shapes. Differences from the table above: staff removal is `DELETE …/staff/{assignment_id}` (players keep `…/roster/{membership_id}`);
> `PATCH …/events/{id}` takes `scope: this|this_and_following|all`; evaluations are club-wide, not under `/teams/{team_id}`: `GET /evaluations/checklists`, `GET|POST /players/{member_id}/evaluations`
> (the evaluation grant is not tied to a team); `GET …/news` lists the caller's own submissions; every `staffed_teams` item in `GET /context` carries `role` and the `can_*` flags.

### 7.4 Deliberately not in v1

Management screens, tournament brackets/scheduling (§14), shop/forms/registration *administration*, club branding editing, member import. Anything a coach can do only via `/manage` on the web stays there.

---

## 8. Permissions detail worth calling out

- **Minors' PII and the family graph.** Only the caller's own managed set is ever returned. A guardian sees their children's data; nobody sees another household's. Staff see rosters via the existing `members_visible_to` rules, not by widening the
  person scope. Member responses expose the minimum: name, photo (respecting `photo_public_consent` for anything public), team/position/ jersey; DOB, phone and emergency contacts only on `GET /members/{id}` for the managed person.
- **Financial data** (`payments`, dues, invoices, vouchers) — managed set only; never coach-visible unless the coach is also the guardian.
- **Staff-only content** (evaluations, notes) — `can_manage_evaluations`, team-scoped, flag-gated. A player/guardian never receives evaluation data through the member endpoints.
- **Secrets never in responses or logs:** access/refresh tokens, TOTP secrets after activation, recovery codes after first display, calendar-feed token except on the explicit endpoint, push tokens. Request logging on `/api/auth/` and `/api/app/` must
  scrub `Authorization`,
  `X-Session-Token`, `password`, `code`, `refresh_token`, `credential`.
- **Form submission for a child is allowed** (decided and built during the security audit). A parent/guardian can answer a form on a child's behalf. `Submission` records who filled it in (`submitted_on_behalf_by`), and `formbuilder/services/submission.py`
  takes the submitting person separately from the member it's for. `POST /forms/{send_id}/submit` therefore takes the `member_id` the answers are for (a managed person), like the other writes (§5.1).

---

## 9. Push notifications

Native push goes **straight to the platform providers**: Apple's APNs (HTTP/2, token auth) for iOS and
Firebase Cloud Messaging (HTTP v1) for Android. There is no SNS or any other intermediary (the original
SNS plan was dropped). See DEPLOYMENT.md "Native app push" for the credentials and `appapi/push.py` for the code.

**Gateways** (`appapi/push.py`). Everything talks to a `PushGateway`: `ApnsGateway` (iOS, when the APNs key is
set), `FcmGateway` (Android, when a Firebase service account is set), and `PlatformGateway`, which
`get_gateway()` returns and which routes a device to the gateway of its platform. A platform without
credentials is simply not `supports()`-ed: its devices still register, nothing is sent. `NullGateway` covers
"nothing configured"; tests swap in a fake with `APP_PUSH_GATEWAY`. `push_configured()` (also in `appapi/push.py`)
says whether any platform can deliver.

**Model.** `appapi.PushDevice(user, installation_id UNIQUE, platform: ios|android, push_token, environment, app_version,
locale, enabled, last_seen)`, keyed per `User` + install, not per Member/club: one install serves every club the user
belongs to (the account plane, §4). `POST /devices` upserts (safe to repeat, also on token rotation); `DELETE
/devices/{id}` and logout remove it. The PWA's `mobile.PushSubscription` is separate (per Member, per club).

**Dispatcher** (`notifications/dispatch.py`). The `notifications_created` signal is gone. `notify_members` writes the
`Notification` rows (the in-app inbox) and calls `schedule_notifications`, which, after the surrounding transaction
commits and off the request thread (`run_in_background`), hands the batch to every registered *channel*. Channels
register themselves in their `AppConfig.ready` via `register_channel` (idempotent per name), so `notifications` never
imports `appapi`/`mobile`:

| Channel  | Class                                | Does                                                         |
|----------|--------------------------------------|--------------------------------------------------------------|
| `email`  | `notifications.channels.EmailChannel` | plain notification mail to the member and guardians          |
| `native` | `appapi.delivery.NativePushChannel`   | APNs + FCM to every enabled device of the recipient accounts |
| `web`    | `mobile.channels.WebPushChannel`      | Web Push to the PWA subscriptions                            |

A channel that raises is logged and never stops the others. `dispatch_to_user(club, user, texts, data, url,
thread_id)` is the second entry point, for messages with no inbox row (live game pushes, `events.services.game_push`):
every channel's `deliver_to_user`, returning how many devices/subscriptions each reached. With
`NOTIFICATIONS_DISPATCH_EAGER` (on in the test suite) everything is delivered synchronously instead of after commit.

**Recipients and dedupe.**
- Recipients are *accounts*: the member's own login plus every parent/guardian's in that club (the `recipient_emails`
  rule). One push per account per distinct notification, so a club-wide item doesn't buzz a parent once per child.
- Web Push is skipped for an account with an enabled native device seen in the last 60 days
  (`users_with_native_push`): the phone already got it.
- An account set to "email unless the app can notify me" (`User.email_notifications`) gets no plain notification mail
  while it has such a device. Mail **with attachments** (invoices) is a document, not a nudge: it is sent inline by
  `notify_members` (once, never again by the email channel) and ignores that preference.
- A device whose token the provider reports dead is disabled.

**Payload = pointer, not content.** `{kind, notification_id, club_slug, target: {type, id, ...}}` plus a localised
title and short body. Texts may be plain strings, lazy translations or callables; they are resolved per recipient in the
language of the device's registered `locale` (Web Push has no language preference and goes out in English). On Android,
`target` arrives as JSON text (FCM data values are strings). Anything sensitive is fetched by the app over the
authenticated API after tap; never put minors' details or financial data in a push body.

**Live game pushes** (`events.services.game_push`, GAME_TIMELINE.md phase 4) have no `Notification` row: no
`notification_id`, `kind: "game_moment"`, `target: {type: "event", id}`. Who gets them is opt-in per team:
`GET/PUT /game-follows`.

**Push policy** (`notifications/policy.py`, one table for both providers). Each category maps to a TTL, whether a newer
push replaces the older one (collapse), a priority and an Android channel. The server sets them on every send: APNs
`apns-expiration` (now + TTL), `apns-priority`, `apns-collapse-id` (the event id, max 64 bytes); FCM `android.ttl`,
`android.priority`, `android.collapse_key`, `android.notification.channel_id`. A `critical` notification is always
priority 10 / high with a TTL of at least a day. A retry re-sends with the same policy and is dropped once the TTL has
passed. The custom payload carries `category` (the category key) so the app can route and style.

| Category       | TTL    | Collapse | Priority (APNs / FCM) | Android channel (importance) |
|----------------|--------|----------|-----------------------|------------------------------|
| `news`         | 7 days | no       | 5 / normal            | `rc_news` (default)          |
| `event`        | 3 days | no       | 10 / high             | `rc_event` (high)            |
| `reminder`     | 1 day  | no       | 5 / normal            | `rc_reminder` (default)      |
| `lineup`       | 1 day  | no       | 10 / high             | `rc_lineup` (high)           |
| `crew`         | 2 days | no       | 10 / high             | `rc_crew` (high)             |
| `form`         | 3 days | no       | 5 / normal            | `rc_form` (default)          |
| `shop`         | 2 days | no       | 5 / normal            | `rc_shop` (default)          |
| `coach`        | 1 day  | no       | 10 / high             | `rc_coach` (high)            |
| `admin`        | 3 days | no       | 5 / normal            | `rc_admin` (default)         |
| `announcement` | 7 days | no       | 5 / normal            | `rc_announcement` (default)  |
| `game`         | 15 min | yes, per event | 10 / high       | `rc_game` (high)             |
| `general`      | 3 days | no       | 5 / normal            | `rc_general` (default)       |

**Delivery log and retries** (`notifications.NotificationDelivery`, `notifications/delivery_log.py`). Every send on
every channel is logged per target (install, subscription or address): sent, failed (refused), dead (target gone) or
retrying. A transient failure (throttling, a 5xx, a network error) keeps its payload and is retried by the
`retry_failed_deliveries` job (every 2 min) after 1, 5 and 30 minutes, 4 attempts in total, then it is marked failed.
Rows are purged after 14 days (sent) or 60 days (failed/dead); retrying rows are never purged. Read-only in the Django
admin. `PUSH_FOLLOWUPS.md` lists what is still open.

**Preferences:** none per type in v1; a per-install `enabled` toggle only. Badge/unread count from
`GET /notifications/unread-count`.

---

## 10. Internationalisation

Every user-facing string the API returns is translatable like the rest of the codebase (`gettext_lazy`, `%(name)s` placeholders — see `CLAUDE.md`). Language comes from
`Accept-Language` (the app sends the device/app locale), falling back to the club default; a `locale` on the device registration drives push text. Only `News.title_en/body_en` are true *content* translations: `GET /news/{slug}` returns the requested
language's fields with a
`language` field saying which was served (replaces the view's `get_language() == "en"` switch). Error `message`s are localised; `code`s are not.

---

## 11. Service-layer prerequisites (do these first, in the web app, behind the existing tests)

The API must **call the same rules the PWA uses**, not fork them. The audit of `mobile/*` found logic that lives inline in views; extract it *before* (or as) each endpoint is built, keeping
`mobile/tests.py` (≈8 k lines) green as the safety net. Ordered by risk of drift:

| #  | Extract                                                                                                                                                 | From                                                       | To (proposed)                                                                                                                          |
|----|---------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| 1  | Managed people + scope resolution                                                                                                                       | 3 divergent copies                                         | `members/services/family.py::managed_people`, `resolve_scope`                                                                          |
| 2  | **RSVP rules** (status set, dropout only if `SELECTED`, deadline unless dropout, note rules, `notify_dropout`) + `rsvp_is_closed` (computed inline 3×)  | `EventDetailView.post`                                     | `events/services/rsvp.py::submit_rsvp` raising `RsvpError(code)`                                                                       |
| 3  | Home summary, calendar agenda, member event view, squad summary, task rows                                                                              | `HomeView`, `CalendarView`, `EventDetailView`              | `mobile/services/home.py`, `events/services/{agenda,member_view,attendance,tasks}.py`                                                  |
| 4  | Signup/task response wrappers                                                                                                                           | referee/official/task views                                | `respond_to_signup`, `unclaim_task_for`                                                                                                |
| 5  | Notifications inbox (`inbox_for`, `mark_read`, `mark_all_read`, `clear_all`, `unread_count`)                                                            | `NotificationsView`, `MX`                                  | `notifications/services.py`                                                                                                            |
| 6  | Cart (`add_to_cart`, `update_cart_item`, `open_cart`, catalog), `place_order` pre-check, `orders_for`                                                   | shop views                                                 | `shop/services/{cart,orders}.py`                                                                                                       |
| 7  | Coach today / attendance / remind-silent (duplicate of `send_deadline_reminders`)                                                                       | `CoachTodayView`…                                          | `events/services/coach.py`                                                                                                             |
| 8  | Event/series create/update/detach + notify side-effects; roster add/remove/update (jersey rules in `CoachRosterEditForm.clean`); news submit-for-review | coach views/forms                                          | `events/services/coach_events.py`, `teams/services/roster.py` (use `place_member_on_team`), `news/services.py::submit_news_for_review` |
| 9  | Lineup read/replace-set/schedule validation                                                                                                             | `CoachLineupView` (**does `Lineup.get_or_create` on GET**) | `events/services/lineup.py`                                                                                                            |
| 10 | Profile update, payments overview, forms schema/rows, evaluations player lookup, usable vouchers, dues (`open_dues_rows` filter copied 3×)              | various                                                    | `members/services/profile.py`, `club/services/fees.py`, `formbuilder/services/…`, `shop/services/vouchers.py`                          |

**Already clean and reusable as-is** (call directly): `events.services.lineup.{toggle_selection,
publish_lineup, schedule_lineup_publish, cancel_scheduled_publish, selected_members_by_position,
notify_dropout}`, `events.services.attendance.{record_check_in, blocked_upcoming_events_for_members,
effective_members, …}`, `events.services.{tasks.claim_task/unclaim_task, referees.*, officials.*,
recurrence.*}`, `notifications.services.notify_members`, `club.services.access.*`,
`club.services.fees.open_dues_rows`, `club.services.onboarding.*`, `formbuilder.services.
{submit_form, build_form, form_status_rows_for}`, `shop.services.{checkout.place_order,
pricing.cart_totals}`, `registration.services.{submission,pricing,invoicing}.*`,
`evaluations.services.{active_checklists, submit_evaluation}`, `bugs.services.file_report`.

**Coupling to remove while extracting** (each becomes an explicit API concept):

- Flash messages (`controlpanel.messages.notify`) mixed into logic → exceptions/status codes; success flashes vanish or become response fields (`added`, `checked_in`, `nudged`).
- `HttpResponseForbidden`/`Http404`/redirect-on-GET semantics → 403/404 per §5.
- `request.session` state (`coach_active_team_id`, `mobile_context`), `?as=`, `?next=` → explicit params or dropped.
- `flag_is_active(request, …)` → club-derived (`officials_enabled_for(club)` already exists).
- `request.build_absolute_uri` (calendar feed, `send_registration_confirmation_email(request=…)`) → a base-URL parameter.
- `get_language()` → an explicit language argument.

**Latent bug found by this audit — fixed on `development`** (security audit L-11): the PWA's push subscribe/unsubscribe now only touches subscriptions whose `member__user` is the caller (`mobile/views.py`). The native `DELETE /devices/{id}` is defined
ownership-scoped from day one.

---

## 12. Payments and app-store rules

There is **no online payment provider** today (checkout = pay-at-pickup; `ARCHITECTURE.md` marks the gateway undecided). So v1 initiates no in-app payment. The shop sells **physical goods** (merch)
and **real-world services** (club membership/registration fees), which Apple's App Store guidelines (3.1.3 (e)) and Google Play policy generally exempt from mandatory in-app purchase. **This is not legal advice and app review is a judgment call**:the
risk cases are (a) anything that could be read as a *digital* good or an in-app unlock, and (b) if a payment provider is added later, whether the club-fee flow stays out of the store's billing. Keep shop/registration checkout as "place order, pay at
the club / by invoice" and open any future payment page in the system browser rather than an embedded flow until reviewed. Sign-in-with-Apple is only mandatory if we offer *third-party social login*; we don't.

---

## 13. Testing strategy

Follow `api/tests.py` (Django `TestCase` + ninja test client, `rosterchief.test_support.make_club`,
`make_season`, `enrol_mfa`). Layers:

1. **Contract tests per endpoint**: happy path, 401 unauthenticated, 403 wrong role, 404 flag off, 404 unmanaged member / other-club id (**a generic "cross-tenant id → 404" test driven from the route table so a new endpoint cannot ship without it**),
   422 validation, idempotency replay.
2. **Auth tests**: login without MFA; TOTP flow (login → 401 flow → `2fa/authenticate` → tokens); recovery code; refresh rotation + replay of an old refresh token is rejected; logout kills access token (stateful validation); privileged user without
   MFA → 403 `mfa_enrollment_required` on
   `/api/app/` but able to reach enrolment; **signup endpoint refuses** (closed); password change invalidates other app sessions.
3. **Middleware interaction**: `RequireMFAMiddleware` does not redirect API paths; maintenance → 503 JSON; no CORS headers on the new prefixes.
4. **Push**: APNs/FCM behind a small `PushGateway` interface with a fake in tests (no network); disabled- endpoint pruning; payload contains no PII.
5. **Regression net**: the service extractions in §11 are refactors — `mobile/tests.py` must stay green at every step, and the new service functions get direct unit tests.
6. **OpenAPI diff check in CI** (§15) so an accidental breaking change fails the build.

---

## 14. Phasing

| Phase                         | Scope                                                                                                                                                                     | Exit criteria                                                                 |
|-------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| **0 — Spike**                 | allauth.headless mounted on the branch; JWT strategy vs session lifetime (§3.3); Android passkey origin prototype (§3.6); SNS platform-app round trip to a real device    | §16-1,2,3 answered; strategy chosen                                           |
| **1 — Foundations**           | `AppAuth`, error envelope, pagination/idempotency helpers, `/clubs`, `/me`, `/context`, signup closed, proxy-count fix, `managed_people`, push device model + SNS gateway | a test client can log in with MFA, list clubs, hit an empty club-scoped route |
| **2 — Member read + RSVP**    | home, calendar, events, RSVP (after `submit_rsvp` extraction), news, notifications, profile, push end-to-end                                                              | an internal build reaches PWA parity for a parent                             |
| **3 — Member write/commerce** | payments, forms, shop, orders, re-registration, photo upload, bug reports, calendar feed                                                                                  | parity for the remaining member screens                                       |
| **4 — Coach**                 | today, attendance/check-ins, events/series, lineup, roster, news submit, evaluations                                                                                      | parity for a coach                                                            |
| **5 — Hardening**             | usersessions/devices UI, iOS then Android passkeys, universal links for reset, rate-limit tuning, load test, security review                                              | store submission                                                              |

**Phase 0 status (2026-09-29).** Done: headless mounted (app client, JWT, base domain only),
signup closed, and 14 tests pinning login/MFA/refresh/revocation/session-lifetime behaviour. **§16-1
answered, strategy kept.** Android origin checked server-side (§3.6), and a device prototype is still
needed. Not started, needing accounts or hardware: Apple Developer and Firebase projects, SNS
platform applications, a test device. None of these block phase 1.

**Phase 1 status (2026-09-29).** Done, in `appapi/` (tests in `appapi/tests.py`):

- `AppAuth` (§3.4): 401 `unauthenticated` / `token_expired`, inactive accounts refused, 403
  `mfa_enrollment_required` for privileged accounts without a second factor.
- Error envelope for every failure (ninja validation → 422 with `fields`), and maintenance
  mode answers 503 in the same envelope on `/api/app/` and `/api/auth/`. Responses are `private,
  no-store`.
- `paginate()` (cursor), `idempotent()` (claim-first `IdempotencyRecord`), unit-tested and ready
  for phase 2.
- `/meta` (unauthenticated; minimum app versions from `APP_MIN_VERSION_IOS`/`_ANDROID`), `/me`,
  `/clubs` (relationship, feature flags, per-club `api_base_url`), `/context` on the club host.
  `members.services.family.managed_people` is shared with the PWA.
- `PushDevice` + `POST`/`DELETE /devices`. `SnsGateway` (APNs production/sandbox, FCM ready)
  is chosen when an `APP_PUSH_SNS_*_ARN` is set, otherwise registration is stored with
  `push_enabled: false`. Delivery starts in phase 2.
- Docs: `/api/docs/` (overview, iOS guide, errors, status), `/api/app/v1/docs` (Swagger) and
  `/api/auth/openapi.html` (allauth's spec). `manage.py export_app_openapi` refreshes the
  snapshot; a test fails when it's stale.

Still needed from outside the code before push can be tested end to end: an Apple Developer
account (APNs key), the SNS platform applications (production + sandbox), and a test iPhone.

**Phase 2 status (2026-09-29).** Done. Service extractions first (§11 rows 2, 3 and 5), with
the PWA switched over and `mobile/tests.py` green:

- `events.services.rsvp.submit_rsvp` / `RsvpError(code)` / `rsvp_is_closed` (the RSVP rules
  from `EventDetailView.post`);
- `events.services.agenda.member_agenda` (Calendar), `events.services.member_view`
  (event page, squad summary, tasks), `mobile.services.home.home_summary` (Home);
- `news.services.{live_news, member_news, localized_news}` (replaces the view's
  `get_language() == "en"` switch with an explicit language);
- `notifications.services.{inbox_for, unread_count, mark_read, mark_all_read, clear_all}`.

Endpoints (club host, `appapi/routers/{member,inbox,people}.py`, tests in
`appapi/test_member.py`): `GET /home`, `GET /calendar` (from/to window, max 92 days, `kind`),
`GET /events/{id}`, `POST /events/{id}/rsvp`, referee/official `…/respond` (officials behind
the `officials` flag → 404 `feature_disabled`), task `claim`/`unclaim`, `GET /news` (cursor)
and `/news/{slug}` (`Accept-Language`), the five notification endpoints, `GET`/`PATCH
/members/{id}` (through `MemberProfileForm`) and the token-authenticated `GET
/members/{id}/photo` (§6's private-photo rule, `can_view_member_photo`). Person scope is
`appapi.scope.ClubScope` (§5.1). A route-table test checks that every id-taking route answers
an unknown id with the 404 envelope (§13).

Push delivery (`appapi/delivery.py`): the `native` channel of `notifications.dispatch` (§9), after commit
and off the request thread, only when a gateway (APNs, FCM or a test gateway) is
configured. Recipients are accounts: the member's own login plus their parents/guardians in that
club (the `recipient_emails` rule, answering §16-4's mapping). One push per account per distinct
notification, so a club-wide item doesn't buzz a parent once per child. Dead tokens disable the
device. **Open:** the Web Push / native dedupe for someone using both (§9); both fire for now.

Not in phase 2, by design: photo upload (phase 3, multipart), `Me`-screen extras (vouchers,
staff assignments: use `/context` and phase 3's payments), `ETag`s and `updated_since` (§15).

**Phase 3 status (2026-09-29).** Done, in three slices (`appapi/routers/{payments,forms,
people,support,shop,registration}.py`, tests in `appapi/test_phase3.py`). Service extractions
(§11 rows 6 and 10), with the PWA switched over: `mobile.services.payments.payments_overview`,
`shop.services.cart` (catalogue, cart, `orders_for`), `mobile.services.reregistration`.

- Payments: `GET /payments` (registration-grouped, as the PWA) and the registration invoice PDF.
- Forms: list, schema, `submit` (JSON, or multipart with files; idempotent; for one addressed
  managed person, §8), submitted response. Not flag-gated, like the PWA: `formbuilder` licenses
  creating forms.
- Photos: `POST`/`DELETE /members/{id}/photo` set/clear the *profile* picture (`Member.profile_photo`) via `MemberProfileForm`, 10 MB cap; the official `player_photo` is manager-only (web). `photo_url` is the effective picture (profile, else player); `ProfileOut` also has `profile_photo_url` and `player_photo_url`; `GET .../photo[?kind=profile|player]`.
- Shop: catalogue, cart (re-priced, code preview), idempotent checkout (pay on pickup, §12),
  orders and invoice PDF. Unlicensed → 404 `feature_disabled`; closed → 409 `shop_closed` for
  catalogue/cart/checkout, orders stay readable.
- Re-registration: `GET /registration/options`, `POST /registration/quote`, idempotent
  `POST /registration`. JSON entries are fed through the PWA's own `RegistrationEntryFormSet`,
  so every rule (season scope, product kind, jersey numbers) is the same.
- `GET/POST /bug-reports`, `GET/POST /calendar-feed` (link shown once).

**Bug found and fixed on the way** (also on `development`, commit "Fix mobile re-registration
booking a child's registration on the parent"): the PWA's re-registration reuses `is_contact`
as "Include this person", and `submit_registration` read `is_contact` as "this is me", so a
child's registration was booked on the parent. Existing data may need checking (the parent
has a `RegistrationDetails` row per child registered that way, and the child no membership).

**Tournaments** (separate `tournaments` branch) are not blocked by this design: they slot in as a new router group under `/api/app/v1` with the same scope/flag/role stack (`tournaments` flag → role → object scope), member-facing reads first
(schedule, standings, brackets, "my team's next match"), read-only public bracket data already fits the existing `/api/v1/` shape.

---

## 15. Cross-cutting

- **OpenAPI.** ninja generates `/api/app/v1/openapi.json`; allauth serves `/api/auth/openapi.json`
  (rewrites its `/_allauth` prefix to our mount and pins to the `app` client). Publish both to the native repos for codegen; commit a snapshot and diff it in CI. Disable the docs UI in production or protect it (`openapi.html` pulls ReDoc from a CDN —
  set
  `HEADLESS_SPECIFICATION_TEMPLATE_NAME=None`).
- **Client minimum version.** `GET /meta` returns `min_supported_app_version` per platform; the app sends `X-App-Version`/`X-App-Platform`; below-minimum → 426-style `upgrade_required` screen. The only defence against an old client that would break
  on a future v2 sunset.
- **Observability.** Request id header echoed; structured logs with user id + club slug (never tokens/bodies); push failures logged per device; counters for 401/403/429 per route.
- **Caching.** Personalised routes: `Cache-Control: private, no-store`. Cheap conditional reads via
  `ETag`/`If-None-Match` on `context`, `home`, `news` list. (The public `api/cache.py` layer is not reused — it caches by club, not by user.)
- **Delta sync.** Most models have `created`/`modified`; list endpoints for
  `calendar`, `news`, `notifications` accept `updated_since=<ts>` and return changed rows. **Almost nothing is soft-deleted**, so deletions need tombstones: those endpoints also return
  `deleted: [{id, at}]` for the (few) removable things clients cache (notifications cleared, events cancelled/hard-deleted, cart items). Cheap `deleted` sourcing is an open item (§16-8).
- **Rate limiting of the resource API.** allauth only limits the auth endpoints. Add a per-user throttle (Redis) on write endpoints and on `/notifications` polling; 429 + `Retry-After`.

---

## 16. Open questions / to verify

1. ~~**(blocking)** Token lifetime~~ **Answered (phase 0):** yes to both. App logins get `set_expiry(APP_SESSION_LIFETIME)`, web logins are unchanged, and each refresh re-saves and slides the session. No custom strategy (§3.3).
2. **(blocking for Android passkeys)** Android Credential Manager origin (`android:apk-key-hash:…`): **server side confirmed** that fido2 rejects it and that an allow-list via a replaced `get_server()` is the way in (§3.6). Still UNVERIFIED on a
   device.
3. **iOS passkeys** need the AASA file served at the base domain — where (Caddy static vs Django)?
4. ~~Push recipient resolution~~ **Answered (phase 2):** per `User`, resolved like email (the member's login + their guardians' in that club), deduplicated per account (§14). Still open: the Web-Push/native dedupe rule (§9).
5. ~~`ALLAUTH_TRUSTED_PROXY_COUNT`~~ **Resolved on development:** `ALLAUTH_TRUSTED_CLIENT_IP_HEADER = "X-Real-IP"` (set by Caddy, with Cloudflare as a trusted proxy). See §3.8.
6. ~~Existing users' `EmailAddress` rows~~ **Moot:** verification is `"mandatory"`, and the web requires it too, so anyone who can sign in today has a verified address (§3.8).
7. ~~Guardian submits a form for a child?~~ **Decided: yes**, and built on development (§8).
8. Tombstone source for `deleted[]` — a small `Deleted` log table vs per-model soft delete vs
   "client re-fetches the window". Recommend re-fetch windows for v1 and tombstones only for notifications/cart.
9. ~~`boto3`~~ **Done (phase 1):** a direct dependency, used by the SNS gateway.
10. `pyjwt[crypto]` is now a runtime dependency (headless extra, phase 0) and covered by the CI `pip-audit` job. `PyYAML` arrived in phase 1 with the served spec.
11. ~~Session/`usersessions` at launch or phase 5?~~ Decided: phase 5 (§3.7).
12. Native identity linking: the account plane returns *all* clubs for a user — confirm there is no scenario where two people share one login (family login) that would need a profile switcher beyond the managed-people model.
13. App-store review posture for the shop/registration flow (§12) — confirm with each store before the first submission.
