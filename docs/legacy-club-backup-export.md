# Implement the v1 club backup exporter

Use this brief in the legacy `RosterChief` repository. The v2 importer in `RosterChiefv2/controlpanel/services/legacy_backup.py` implements this format.

Add a ZIP download to the legacy Control Panel club detail page. Require platform superuser access, audit the download, and stream the result with a private, no-store cache policy. Read club data consistently and scope every query to the requested club. On v1 the same member and login can belong to multiple clubs, so the backup must only contain the selected club’s relationships, roles, and records.

## ZIP format

Place one UTF-8 `manifest.json` at the root. Store each referenced upload as a separate ZIP entry under `media/<unique-number>/<original-basename>`. Copy the bytes through the source field’s storage, including private media. Use forward slashes and relative paths. Fail the export with a useful message if a referenced file cannot be read. The v2 reader accepts ZIPs up to 512 MiB, expanded data up to 2 GiB, and a manifest up to 32 MiB.

The root JSON object has these keys.

| Key | Value |
| --- | --- |
| `format` | The literal `rosterchief-club-backup` |
| `version` | The integer `1` |
| `exported_at` | Export timestamp with UTC offset |
| `club` | `slug`, `owner`, `fields`, `sport`, and `registration_open` |
| `accounts` | Array of login identities |
| `referee_types` | Object keyed by the original referee type ID as a string |
| `features` | Object keyed by v2 feature code |
| `records` | Object keyed by the legacy Django model label in lowercase |

Use the club’s source slug. For `club.owner`, choose an active login with the club’s `admin` role. If several exist, let the Control Panel operator choose. For a club with no active admin, have the operator designate a club login to take ownership after restore. Include that login in `accounts` with `enrolled: true`. The v2 restore assigns its Owner role. `club.fields` contains the club’s native data fields, including `shop_open`, legal and contact details, colours, season settings, logo and background. Resolve `club.sport` to `{"slug":"ice-hockey","name":"Ice hockey"}` using the source Sport code with hyphens in place of underscores. Use `null` if the sport is unavailable. Set `registration_open` to the effective availability of the public registration flow for the club.

Each `accounts` entry includes `id`, `email`, `first_name`, `last_name`, `active`, and `enrolled`. Derive names from the Member if needed. `enrolled` means the login has club access in v1, including guardians and coaches who have access through relationships. Include referenced audit actors even if they are no longer enrolled. Keep their `enrolled` value false. Do not include passwords, recovery codes, MFA, sessions, push credentials, or v1 platform staff grants. The v2 importer resolves existing verified accounts by email and creates other accounts with unusable passwords.

For each referee type referenced by an assignment, include `{"sport":{"slug":"ice-hockey","name":"Ice hockey"},"kind":"referee","name":"Linesman","ordering":1}`. `kind` is `referee` or `official`. V2 looks up or creates the shared type by sport, kind, and name.

For flags export `{"enabled":true,"limit":null}` with the effective club setting. Use v2 keys `shop`, `referee_officials`, `sponsoring`, `evaluations`, `competition_rbihf`, `competition_dfel`, and `competition_ijnl` where supported. Only include keys you can resolve from the old application.

## Model rows

A row is `{"fields":{"id":"…","member_id":"…","created":"…"},"many":{"teams":["…"]}}`. Use each concrete field’s `attname` so foreign keys are suffixed with `_id`. Omit `club_id`. Use strings for IDs and exact decimal amounts. Use ISO dates and datetimes, ordinary JSON booleans and numbers, and JSON null for nulls. Preserve the native values of JSON fields. Store file fields as their ZIP member paths or the empty string. List auto-created many-to-many memberships under their field name in `many`; use an empty object if there are none. Preserve UUIDs so old public team and game IDs work after cutover.

Export these legacy model names under their own app labels.

- `club` — `season`, `sponsor`, `clubmembership`, `feepayment`, `duesinvoice`, `onboardingrequirement`, `memberrequirementstatus`, `clubrole`, `shopmanager`, `refereemanager`, `evaluationmanager`.
- `members` — `member`, `family`, `familymembership`, `group`, `groupmembership`. Include the club’s attached members and every member referenced by its own included relationships or activity. Follow the selected club’s family links to include its parents and children. Do not traverse relationships belonging to another club.
- `teams` — `numberpool`, `team`, `teamphoto`, `position`, `teammembership`, `staffassignment`, `numberreservation`, `refereelevel`, `refereeprofile`, `officiallevel`, `officialprofile`. Include the complete `Team.import_sources` data.
- `events` — `opponent`, `location`, `event`, `eventseries`, `attendance`, `lineup`, `lineupselection`, `eventreferee`, `eventofficial`, `refereesignup`, `officialsignup`, `eventtask`, `eventtaskclaim`, `eventattachment`, `gametimeline`, `gamefollow`, `eventfollow`.
- `news` — `news`, `newsphoto`.
- `formbuilder` — `form`, `field`, `formsend`, `submission`, `answer`.
- `shop` — `productcategory`, `product`, `productvariant`, `productregistrantdiscounttier`, `order`, `orderline`, `inventorymovement`, `discount`, `applieddiscount`, `voucher`, `voucherconsumption`, `payment`, `invoice`.
- `registration` — `registrationbatch`, `registrationdetails`.
- `evaluations` — `evaluationchecklist`, `playerevaluation`, `evaluationnote`, `evaluationsession`, `evaluationoutcome`.

Scope child rows through their exported parents. For example, find attendance through included events and fee payments through included memberships. Check that every in-archive foreign key and many-to-many ID resolves before emitting the backup. Preserve existing form, payment, invoice, and signup history. Exclude generated analytics, push delivery data, carts, expiring family invites, platform billing, and generated file caches.

Provide a synthetic or anonymized sample ZIP for v2 validation. Include two clubs in the exporter test fixture with a shared login, plus an event with a referee type, a family, a registration with payment, an order, and public and private uploads. Verify the selected club’s ZIP has no data or file bytes from the other club’s activity.
