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
| 403 | `self_removal` | A manager tried to remove themselves from a team's staff: hide the button on your own row (`is_me`). |
| 403 | `manager_required` | A coach action that needs a management position on the team. `GET /context` → `staffed_teams[].can_…` says so up front: hide the button. |
| 404 | `not_found` | Doesn't exist, or you may not see it (another family's person, another club's event). |
| 404 | `feature_disabled` | The club doesn't have this module (e.g. officials): hide it. `/clubs` → `features` says so up front. |
| 409 | `checklist_not_configured` | The evaluation checklist has no questions yet: tell the user to ask an admin. |
| 409 | `no_current_season` | The club has no season covering today, so there is no roster to change. |
| 409 | `lineup_empty` | Publishing a line-up nobody is picked in yet: ask the coach to pick players. |
| 409 | `request_in_progress` | The same `Idempotency-Key` is still being processed: wait a moment and retry. |
| 409 | `event_started` | RSVP after the event started: refresh the event; answers are read-only now. |
| 409 | `rsvp_closed` | The reply deadline passed. Only a line-up `dropout` is still possible. |
| 409 | `not_in_lineup` | `dropout` from someone who isn't selected: refresh the event. |
| 409 | `signup_unavailable` | A referee/official invite can't be accepted (e.g. all slots taken). The invite stays open. |
| 409 | `task_unavailable` | The task is full, or this account already has a slot. Refresh the event. |
| 409 | `form_unavailable` | The form is closed, not open yet, or the maximum number of responses was reached. |
| 409 | `shop_closed` | The club closed the shop for now: hide the shop's catalogue and cart (orders stay readable). |
| 409 | `checkout_failed` | The order couldn't be placed (empty cart, an unavailable line, changed prices, a code that can't be used): show `message` and refresh the cart. |
| 409 | `registration_failed` | The registration couldn't be submitted as it stands: show `message`. |
| 409 | `pdf_unavailable` | The invoice PDF can't be made right now: show `message`, try later. |
| 409 | `conflict` and other specific codes (e.g. `shop_closed`) | State doesn't allow it right now; show `message`. |
| 422 | `validation_error` | Show `fields` next to the inputs. |
| 422 | `not_a_candidate` | Someone being added to the roster or staff is not eligible (`fields.member_ids`). Nothing was added: reload the candidates. |
| 422 | `invalid_selection` | A line-up pick is not invited or not available (`fields.selected_member_ids`). Nothing was saved: reload the line-up. |
| 422 | `not_in_series` | `scope` other than `this`, or a detach, on an event that is not part of a series. |
| 422 | `unknown_attendance` | A check-in named a row that isn't part of this event (`fields.items` lists them). Nothing was saved: reload the sheet. |
| 422 | `note_required` | `absent`/`dropout` without a reason: ask for one (`fields.note`). |
| 422 | `idempotency_key_reused` | Bug: one key was used for two different requests. |
| 429 | `rate_limited` | Back off; honour `Retry-After`. |
| 503 | `maintenance` | Show the maintenance screen; retry after `Retry-After` seconds. |

More codes will appear per endpoint. Treat an unknown code by its HTTP status.

## Sign-in API (`/api/auth/app/v1/`)

allauth's own format: `{"status": 400, "errors": [{"code": "…", "param": "…", "message": "…"}]}`
for input errors, and **401 with `data.flows`** when sign-in needs another step. That's not a
failure: see "Signing in" in the iOS guide. The full list is in the [sign-in API
reference](/api/auth/openapi.html).
