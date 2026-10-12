# Status

What the app can use today, and what's next. The full plan is `API.md` in the repository.

| Area | Status |
|---|---|
| Sign-in: password, two-factor (TOTP, recovery codes), refresh, sign-out, password reset | **Available** |
| Two-factor enrolment from the app (TOTP, recovery codes) | **Available** (sign-in API) |
| `GET /meta`, `/me`, `/clubs`, `/context` | **Available** |
| Push registration (`POST`/`DELETE /devices`) and delivery | **Available** once the server has its APNs key set up |
| Member: home, calendar, events, RSVP, referee/official invites, tasks, news, notifications, profile (view/edit), member photos | **Available** |
| Member: payments & invoices, forms, photo upload, bug reports, calendar subscription | **Available** |
| Member: shop, cart, orders & invoices | **Available** |
| Member: re-registration | **Available** |
| Coach: today, attendance (check-ins, remind silent), per-team permissions in `/context` | **Available** |
| Coach: events, lineup, roster, news, evaluations | Next (phase 4, in slices) |
| Passkeys on iOS, "signed-in devices", universal links for password reset | Phase 5 |
| Android | After iOS, on the same API |
