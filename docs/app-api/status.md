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
| Coach: events and series (create, edit with `scope`, delete, detach), location / opponent pickers | **Available** |
| Coach: line-up (read, replace picks, publish / schedule / cancel) | **Available** |
| Coach: roster (list, candidates, add players / staff, edit, remove) | **Available** |
| Coach: news submission (with photos) and your own submissions | **Available**. No rejection reason yet (the web has no "send back with a note" step) |
| Coach: evaluations (checklists, file, history) | **Available**. Club-wide, not per team |
| Passkeys on iOS, "signed-in devices", universal links for password reset | Phase 5 |
| Android | After iOS, on the same API |
