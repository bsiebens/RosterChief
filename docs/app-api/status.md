# Status

What the app can use today, and what's next. The full plan is `API.md` in the repository.

| Area | Status |
|---|---|
| Sign-in: password, two-factor (TOTP, recovery codes), refresh, sign-out, password reset | **Available** |
| Two-factor enrolment from the app (TOTP, recovery codes) | **Available** (sign-in API) |
| `GET /meta`, `/me`, `/clubs`, `/context` | **Available** |
| Push registration (`POST`/`DELETE /devices`) and delivery | **Available** once the server has SNS set up |
| Member: home, calendar, events, RSVP, referee/official invites, tasks, news, notifications, profile (view/edit), member photos | **Available** |
| Member: payments, forms, shop, orders, re-registration, photo upload, bug reports, calendar feed | Next (phase 3) |
| Coach: today, attendance, events, lineup, roster, news, evaluations | Phase 4 |
| Passkeys on iOS, "signed-in devices", universal links for password reset | Phase 5 |
| Android | After iOS, on the same API |
