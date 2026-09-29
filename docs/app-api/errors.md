# Errors

## App API (`/api/app/v1/`)

Every error has the same body:

```json
{"error": {"code": "not_a_member", "message": "You're not a member of this club.", "fields": null}}
```

Branch on `code`, never on `message`. `message` is translated per `Accept-Language` and is
safe to show as is. `fields` only appears on `422` (`{"field": ["message", …]}`).

| HTTP | `code` | What the app does |
|---|---|---|
| 400 | `bad_request`, `invalid_cursor` | Bug or stale cursor: reload the list from the start. |
| 401 | `token_expired` | Refresh the token (see the iOS guide) and retry **once**. |
| 401 | `unauthenticated` | No or invalid token, or the session was revoked: show sign-in. |
| 403 | `mfa_enrollment_required` | The account must set up two-factor first: show the TOTP enrolment screen (sign-in API `GET/POST /account/authenticators/totp`). |
| 403 | `not_a_member` | No tie to this club (any more): refresh `GET /clubs`. |
| 403 | `forbidden` | Not allowed; hide the action. |
| 404 | `not_found` | Doesn't exist, or you may not see it. Also answered for a feature the club doesn't have. |
| 409 | `request_in_progress` | The same `Idempotency-Key` is still being processed: wait a moment and retry. |
| 409 | `conflict` and specific codes (e.g. `shop_closed`) | State doesn't allow it right now; show `message`. |
| 422 | `validation_error` | Show `fields` next to the inputs. |
| 422 | `idempotency_key_reused` | Bug: one key was used for two different requests. |
| 429 | `rate_limited` | Back off; honour `Retry-After`. |
| 503 | `maintenance` | Show the maintenance screen; retry after `Retry-After` seconds. |

More codes will appear per endpoint. Treat an unknown code by its HTTP status.

## Sign-in API (`/api/auth/app/v1/`)

allauth's own format: `{"status": 400, "errors": [{"code": "…", "param": "…", "message": "…"}]}`
for input errors, and **401 with `data.flows`** when sign-in needs another step. That's not a
failure: see "Signing in" in the iOS guide. The full list is in the [sign-in API
reference](/api/auth/openapi.html).
