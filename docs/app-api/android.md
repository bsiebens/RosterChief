# Android

What an Android client needs for push notifications. The rest of the API is the same as on iOS (see `index.md`
and `ios.md`). The server sends through Firebase Cloud Messaging (HTTP v1); add the Firebase SDK to the app and use
the project the server was set up for.

## Registration

1. Android 13+ (API 33): request the `POST_NOTIFICATIONS` runtime permission at a sensible moment, not on first launch.
2. Get the FCM registration token (`FirebaseMessaging.getInstance().token`) and register it:

   ```http
   POST https://<base domain>/api/app/v1/devices
   Authorization: Bearer ...

   {"installation_id": "<UUID generated once>",
    "platform": "android",
    "push_token": "<FCM token>",
    "app_version": "1.0.0",
    "locale": "nl"}
   ```

   Call it again at launch and in `onNewToken`: it is safe to repeat. `environment` is ignored for Android.
   `push_enabled: false` in the response means the server has no Firebase credentials yet: no error to show.
3. Title and body arrive in the language of the `locale` last registered; re-register when the in-app language changes.

## Notification channels

Create one channel per category at startup, before the first push. The list (id, category, importance) is in the
`categories` of `GET /me/notification-preferences` (`android_channel_id` and `android_importance`, plus a `description` to show in the app's own settings). The server puts the channel
id on every message (`rc_news`, `rc_event`, `rc_game`, ...), so people can tune each category in Android's own
settings. A push for a channel the app did not create falls back to a default channel with default sound: create
them all. Importance `high` means sound and heads-up; `default` means sound without the pop-up.

Per-person switches (`member_id` on `/me/notification-preferences`, `people` per club) and quiet hours
(`GET`/`PUT`/`DELETE /me/quiet-hours`) work as described in ios.md, section 7.

## Payload

The system shows the notification (title, body) itself when the app is closed. The `data` map, all values strings:

| Key               | Meaning |
|-------------------|---------|
| `kind`            | `notification` (stored in the inbox) or `game_moment` (live game update, not stored) |
| `category`        | the category, for routing and styling (`news`, `event`, `game`, ...) |
| `club_slug`       | the club to switch to on tap |
| `notification_id` | the inbox id, absent for `game_moment`; mark it read with `POST /notifications/{id}/read` |
| `target`          | **JSON text**, not an object: parse it. `{"type": "event", "id": "..."}`, or absent/`null` |

The payload is a pointer: fetch the content over the API after the tap, as on iOS (`ios.md` lists the target types).
The notification `tag` is the club slug (inbox pushes) or the event id (live game), so related notifications replace
each other instead of piling up.

## Expiry and collapsing

The server sets a time to live per category (`android.ttl`): a push the phone could not receive in time is dropped,
for instance 15 minutes for live game updates and 7 days for news. Live game pushes also use a `collapse_key` (the
event id): while the phone is offline only the newest update for that game is delivered. Priority is `high` for events,
line-ups, game pushes and critical notifications and `normal` for the rest (it may be delayed in Doze). Don't rely on
every push arriving: the inbox (`GET /notifications`) is the record. The full table is in `API.md` section 9.
