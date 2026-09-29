# RosterChief app API

The API behind the RosterChief native apps. **iOS comes first**; Android follows on the same
API. The web app (PWA) keeps working next to it.

| What | Where |
|---|---|
| **iOS developer guide** (start here) | [iOS guide](ios/) |
| Sign-in API reference (allauth headless) | [/api/auth/openapi.html](/api/auth/openapi.html) · [openapi.json](/api/auth/openapi.json) |
| App API reference | [/api/app/v1/docs](/api/app/v1/docs) · [openapi.json](/api/app/v1/openapi.json) |
| Error codes | [Errors](errors/) |
| What's built, what's next | [Status](status/) |

## The two APIs

1. **Sign-in** — `https://<base domain>/api/auth/app/v1/…`. Password, two-factor codes,
   token refresh, logout, password reset. It's [django-allauth's headless
   API](https://docs.allauth.org/en/latest/headless/) for the `app` client. Its responses use
   allauth's own `{status, data, meta}` shape.
2. **App API** — `/api/app/v1/…`, authenticated with the `access_token` the sign-in API
   returns (`Authorization: Bearer <token>`). Its errors use `{"error": {"code", "message",
   "fields"}}`.

The app API has **two planes**:

* **Account plane**, on the base domain (`https://rosterchief.app/api/app/v1`): `/meta`, `/me`,
  `/clubs`, `/devices`.
* **Club plane**, on each club's own host (`https://<club>.rosterchief.app/api/app/v1`):
  `/context` now, and the member and coach screens as they're built. Take each club's base URL
  from `api_base_url` in `GET /clubs`; never build it yourself.

One sign-in works for every club the account belongs to. The token says *who* you are, never
*which club*. The server re-checks what you may see on every club request.

## Conventions

* JSON, UTF-8, `snake_case`. Ids are UUID strings.
* Timestamps are ISO-8601 with an offset (`2026-09-20T18:30:00Z`). Dates are `YYYY-MM-DD`.
  Money is `{"amount": "12.50", "currency": "EUR"}`, never a float.
* Enums are machine values (`present`, `absent`, …). Translate them in the app.
  **Unknown fields and unknown enum values will appear** as the API grows. Ignore them rather
  than failing to decode.
* Send `Accept-Language` (the app's language). Error messages and push texts follow it.
* Lists that can grow return `{"items": [...], "next_cursor": "…"|null}`. Pass `?cursor=` to get
  the next page and `?limit=` (≤ 100) to size it. Cursors are opaque.
* Writes that a bad network might repeat accept an `Idempotency-Key` header (a UUID per
  user action). A retry with the same key gets the first response back instead of doing the
  work twice.
* Within `v1`, changes are additive only. A breaking change becomes `v2` next to `v1`.
  `GET /meta` tells an app when it's too old.
