# Push & notifications: open items

Collected while building the multi-channel push redesign (phase 1 FCM, phase 2 dispatcher, phase 3
delivery log + retries, phase 4 categories + preferences + control panel push monitoring). **All
four phases are done**; none of the items below is scheduled. Each says why it matters; pick by value.

## Recommended next steps (in order)

1. **Verify on real devices and providers.** Nothing has been sent to a real Android phone or real
   FCM project yet, and the monitoring page has only been checked by tests, not in a browser.
   Set up the Firebase project (`DEPLOYMENT.md`), send a test push, trigger a category mute and watch
   `/push/` in the control panel. Then build the Android client against `docs/app-api/`.
2. **Outbox for first sends** (section B): the one remaining way a notification can be lost
   (restart between commit and dispatch), and the game push `delivered_at` ordering bug.
3. **Alerting on the monitoring data**: the `/push/` page only helps when someone looks at it. Turn its
   `down`/`stuck queue` states into a notification to platform admins (email + push), with a
   cool-down so one outage is one alert.
4. **Android channel per category + TTL/collapse per category** (sections B and C): now that categories
   exist, users can tune them in system settings and live game updates stop arriving late.
5. **Granular preferences** (section A): per child, per team, quiet hours, digests, priorities. Start
   with *per child* and *quiet hours*, the most requested kind of control.
6. **Announcement controls** (section C): audience filters, test send, merge duplicates for people in
   several clubs, before the first platform-wide announcement goes out to everyone by email.
7. Everything else in sections B to D.

**Already done in phase 4** (so not open): categories on every notification, per-account
push/email switches per club or for all clubs (API + PWA), enforcement in all channels, delivery
log with provider and duration, and the control panel *Push monitoring* page.

**Monitoring page follow-ups**: no alerting yet (step 3 above); no historical retention beyond the
log's 14/60 days (add daily rollups if long-term trends matter); no per-club drill-down page; the
success rate ignores dead tokens by design (a dead device is not the provider's fault) so a spike of
dead tokens needs its own threshold; p50/p95 are computed from the 2000 most recent samples per
provider, not the full window.

How the system fits together today: `API.md` §9 and `ARCHITECTURE.md` §8.6.

## A. Granular control over what a person receives

Phase 4 delivers **category × channel × club** switches (`NotificationPreference`). People will want
finer control than that. Capture these as the next layer, in rough order of value:

1. **Per person, for parents.** A parent mutes or customises notifications *per child* ("everything
   for Lou, only match results for Sam"). Today an account is one preference set across everyone it manages.
2. **Per team / per squad.** "Only my teams", "not the U9s": coaches and staff in several teams, parents
   with children in several teams. Follow/unfollow a team for general news, not only for live games.
3. **Per game follow detail.** Live game pushes are all-or-nothing per followed team/game. Options:
   goals only, result only, start/end only; follow-with-level on `GameFollow`/`EventFollow`.
4. **Quiet hours / do-not-disturb.** A daily window (per account, with timezone) in which non-urgent
   pushes are held until it ends or delivered silently.
5. **Digest mode.** Instead of instant: one daily (or weekly) summary of the non-urgent ones, per channel
   (especially email).
6. **Snooze / temporary mute.** Mute a club, a team or one noisy thread for a day or week.
7. **Priority levels.** Mark some notifications *critical* (cancelled training, changed venue, same-day
   changes) so they bypass mutes and quiet hours, and some *informational* (news) that can be digested.
8. **Per device.** Turn push off on one phone but not another (today only the server-managed `enabled`
   flag), and per-device quiet settings.
9. **Per role.** Separate defaults for player / parent / coach / board (e.g. board members get admin
   alerts by default, players don't).
10. **Club-level defaults and locks.** A club sets the default preference set for its members, and marks
    some categories *mandatory* (members can't turn them off), e.g. payment reminders.
11. **Where to edit.** App API (`GET/PATCH /me/notification-preferences`), PWA settings screen, and a
    per-notification "stop notifications like this" shortcut in the inbox and in emails (an unsubscribe
    link per category, required for bulk mail anyway).

## B. Delivery quality and reliability

- **First send is still a daemon thread.** Phase 3 retries *transient failures after the first
  attempt*, but a restart mid-dispatch loses work that was never attempted. Real fix: an **outbox**
  (persist "to dispatch" rows in the same transaction as the `Notification`, a worker drains them).
- **Game push `delivered_at` is set before sending** (`events/services/game_push.py`): a crash in between
  drops the moments for good. Mark after, or drive game pushes from the outbox too.
- **Throughput.** One HTTP call per device, sequential, in one thread. For big clubs use a bounded thread
  pool or async batching (FCM has no cheap batch API in v1; APNs multiplexes on one HTTP/2 connection).
  Respect APNs `Retry-After`/FCM quota responses.
- **TTL / expiry.** APNs `apns-expiration` and FCM `ttl`: a live game update must not arrive two hours
  late; a newsletter can wait. Per category.
- **Collapsing.** `apns-collapse-id` / FCM `collapse_key` so a burst for the same thing replaces itself.
  Coalesce rapid non-game notifications (many RSVP changes) into one.
- **Stale device cleanup.** Disable devices not seen for N days (and fix the 60-day "active native
  install" rule suppressing web push/email for people who turned OS notifications off).
- **Badge sync.** Reset the iOS badge (silent push) when notifications are read on another device or in
  the inbox; Android has no badge count.
- **Email bounce/complaint tracking** (Resend webhooks) and an address suppression list; email retries
  (phase 3 logs email outcomes but only push is retried).
- **Delivery log UI.** Phase 3 gives the data and Django admin only. Add a per-notification "delivered to
  X of Y, failed Z" view for admins, failure-rate metrics and an alert on spikes.
- **SQLite "table is locked" noise** in the test output (80 lines on a green run) is background threads
  from `run_in_background` in tests; make those eager under the test runner like notification dispatch.
- **`Notification.sent_at`/`sent_to_emails`** are written by the email channel after commit, so the instances
  `notify_members` returns are stale. Nothing in production reads them from the return value; keep it so,
  or return fresh rows.

## C. Reach and correctness

- **Platform-wide announcements** create one notification per club for a person in several clubs
  (two inbox rows, possibly two emails/pushes). Merge per person for `club=None`.
- **Announcement email volume.** Every account-holding member of every club is emailed. Add audience
  filters (club, team, role), a test send to yourself, a rate limit, and a per-category email opt-out
  (phase 4 covers the opt-out; the rest is open).
- **Email is English only** (`_send_email` uses the stored English text). Use `Notification.text_for(language)`
  with a per-account language preference (accounts have none yet; devices have `locale`).
- **Web push is English only for game pushes** and has no icon/badge in `sw.js`; no read-state sync.
- **`PushSubscription` (member + club scoped) vs `PushDevice` (account scoped).** Unify into one
  account-level device registry so channel logic, dedupe and preferences stop special-casing two models.
- **Per-club apps.** APNs key/topic and FCM project are platform-wide. White-label club apps need
  per-club credentials (`ApnsGateway._post`, `FcmGateway`).
- **Android parity.** No Live Activity equivalent (ongoing notification / "live updates" for games);
  one notification channel id for everything: with categories, create one Android channel per category so
  users can tune them in system settings.
- **Rich notifications / actions.** Images, and actions from the notification itself (RSVP yes/no) on
  iOS and Android.
- **Payload contract.** `target` is an object on iOS and JSON text on Android (FCM data is string-only);
  version the payload and document deep links per platform. Write `docs/app-api/android.md` for the
  Android developers (registration, channel, permission, payload).

## D. Ops and docs

- A `send_test_push <email>` management command and an admin action on `PushDevice` ("send test push"):
  today the check is a shell snippet in `DEPLOYMENT.md`.
- `APP_PUSH_GATEWAY` is hard-coded in settings (test override only); push credentials could come from a
  secrets manager instead of env vars.
- **Data protection.** The delivery log stores email addresses and device ids; retention is 14 days for
  sent and 60 for problems (`notifications/delivery_log.py`). Erase a person's rows when they are deleted
  (`members/services/deletion.py`) and mention the log in the privacy statement.
- Keep `DEPLOYMENT.md`'s scheduled-jobs table and `API.md` §9 in sync with the retry job and preferences.
