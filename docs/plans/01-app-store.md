# Plan: App Store support and per-app country

Priority 1 in [ROADMAP.md](../../ROADMAP.md). Built 31 Oct 2026, except Phase G: existing Apps became `play:us:…` and Users move them themselves with 🌍 in `/apps`. Country codes are validated against ISO 3166 (`contracts/countries.json`); availability per store is checked by lookup.

## Goal

Users watch iPhone apps the same way they watch Google Play apps, and every App is tracked in a known country. `/add` accepts a Play or App Store link, package name or App Store ID; the Bot detects the Store, picks the country, checks the App exists there, and confirms. Users can move any App to another country later.

Why country matters for both Stores: Apple shows a separate rating per storefront, and since 2021 Google Play shows each user the ratings and reviews of their own country. The Bot today fetches every Play App from the US store, so an Indian User sees US numbers for Indian apps.

Country rules, in order:
1. A link that names a country (`apps.apple.com/in/…`, `play.google.com/…&gl=IN`) → that country.
2. Otherwise, the User's Timezone Country, when the User set a timezone in `/settings`.
3. Otherwise, the User's Saved Country, when they answered the Country Question before.
4. Otherwise, ask the Country Question. On an invalid code, ask again. The answer becomes the Saved Country.

Constraints:
- Free to run. Apple's public endpoints only: the iTunes Lookup API and the customer reviews RSS feed. No keys, no paid service.
- One country per App. The same app in two countries is two Apps and counts twice toward the 10-app limit.
- The Bot detects the Store from the input and never asks for it.
- An App missing from the chosen country is not added. The Bot says where it is available and lets the User send another country code.
- Reuse everything after the Store Source: Fetch, Alerts, Summaries, Cards, Sending, Reports, Dashboard.
- Keep `package` as the single App key column in every table.

Rejected:
- **Using the default timezone as the country.** Telegram does not share timezones; every User starts on `Asia/Kolkata`. Trusting it would put every User in India.
- **A global rating.** Neither store publishes one. Estimating it needs one request per storefront per App per day (~175 for Apple) against a rate-limited API.
- **Country buttons instead of a typed code.** Buttons cover few countries; a typed two-letter code covers all, and the requirement is a code with re-ask on error.
- **Silently tracking another country when the App is missing.** The User would read ratings from a market they did not choose.
- **A new `app_id` column with a store column in every table.** Touches 8 foreign keys and every query. Country and Store live inside the App Key instead.
- **Keeping US history under the new India key.** Different market, different numbers. The old rows stay as `play:us:…`; India starts fresh.

## Terms

- **Store** — `google_play` or `app_store`.
- **Store Country** — the two-letter, lower-case country an App is tracked in (e.g. `in`, `us`).
- **App Key** — the `package` value: `play:<country>:<package name>` (e.g. `play:in:com.zerodha.kite3`) or `ios:<country>:<track id>` (e.g. `ios:in:310633997`).
- **Track ID** — the numeric App Store ID (e.g. `310633997`).
- **Store Detection** — `detectStore(input)`: Google Play for `play.google.com` and `market://` links and package names; App Store for `apps.apple.com` and `itunes.apple.com` links, `id<digits>` and 6–12 bare digits; null otherwise.
- **Link Country** — the country a link names: the `/<cc>/` path segment of an App Store link, or the `gl` parameter of a Play link.
- **Timezone Set** — `users.timezone_set`, true once the User picks a timezone in `/settings`.
- **Timezone Country** — the country of the User's timezone from the IANA `zone1970.tab` table, used only when Timezone Set is true. Null for zones with no single country (e.g. `UTC`).
- **Saved Country** — `users.saved_country`: the User's last answer to the Country Question.
- **Country Question** — "Which country's store should I track <name> in? Send a two-letter code, like IN, US or GB."
- **Valid Country Code** — two letters that are in the Store's country list (`contracts/countries/app_store.json`, `contracts/countries/google_play.json`).
- **Pending Reply** — `users.pending`: what the Bot waits for from a User (`{kind, input, app_key, expires_at}`), so a plain-text reply such as `us` answers the right question. Expires after 10 minutes.
- **Availability Check** — after an App is not found in the chosen country, one lookup per Availability Country to find where it exists.
- **Availability Countries** — `us`, `gb`, `in`, plus the Timezone Country. Stored in `config` as `availability_countries`.
- **Add Confirmation** — "✅ Added <name> · <Store Badge>. Your first update arrives in a few minutes." with a Change country button.
- **Country Change** — moving one App in a User's Watchlist from one Store Country to another.
- **Store Badge** — "Google Play · IN" or "App Store · US", on Cards, captions, `/apps` and confirmations.
- **AppStoreAdapter** — the Source Adapter for App Store Apps.
- **Lookup API** — `https://itunes.apple.com/lookup?id=<track id>&country=<country>`.
- **Reviews Feed** — `https://itunes.apple.com/<country>/rss/customerreviews/page=<n>/id=<track id>/sortby=mostrecent/json`, pages 1–10, 50 Reviews each.

## Steps

### Phase A — Country foundation
1. Create `contracts/countries/app_store.json` with every App Store storefront code.
2. Create `contracts/countries/google_play.json` with every Google Play country code.
3. Create `web/src/lib/timezones.ts` with the timezone → country map generated from IANA `zone1970.tab`.
4. Create `db/008_store_country.sql`.
5. Add the columns `users.timezone_set boolean not null default false`, `users.saved_country text` and `users.pending jsonb`.
6. Set `timezone_set = true` for Users whose timezone differs from `Asia/Kolkata` (they changed it themselves).
7. Change every foreign key that references `apps (package)` to `on update cascade`.
8. Add a check that `apps.package` matches `^(play|ios):[a-z]{2}:` and that its prefix agrees with `apps.store`.
9. Rename every existing App Key `<package>` to `play:us:<package>` (they were fetched from the US store).
10. Set `timezone_set = true` in the `/settings` timezone handler.
11. Apply `db/008_store_country.sql` to Supabase.

### Phase B — Google Play per country
12. Add `parseAppKey(key)` to `job/sources/base.py`, returning Store, Store Country and store ID.
13. Make `GooglePlayScraperAdapter` take the Store Country from the App Key for listings and reviews.
14. Make the Node Play lookup in `web/src/sources/lookup.ts` take the Store Country.
15. Re-record the Play Contract Fixtures with an explicit country.

### Phase C — App Store adapter
16. Add `app_store` to the `store` enum in `contracts/listing.schema.json`.
17. Record Lookup API and Reviews Feed responses for 3 App Store Apps (large, small, removed) in `contracts/fixtures/app_store/` with `job/record_fixtures.py`.
18. Write the expected Listing and Review JSON for each App Store fixture.
19. Create `job/sources/app_store.py` with `AppStoreAdapter`.
20. Map Lookup API fields to the Listing: `trackName` → `name`, `sellerName` → `developer`, `artworkUrl512` → `icon_url`, `version`, `averageUserRating` → `rating`, `userRatingCount` → `review_count`, `fileSizeBytes` → `size`, `currentVersionReleaseDate` → `updated_at`, `releaseNotes` → `release_notes`. Set `installs_tier` to null.
21. Map Reviews Feed entries to Reviews: `id.label` → `id`, `im:rating.label` → `stars`, `title.label` and `content.label` joined → `text`, `im:voteCount.label` → `thumbs_up`, `im:version.label` → `app_version`, `updated.label` → `created_at` in UTC.
22. Stop reading Reviews Feed pages at the first Review at or before `since_id`.
23. Return `SourceError('not_found')` when the Lookup API returns `resultCount: 0`.
24. Map HTTP 429 to `rate_limited`, 403 to `blocked`, bad JSON to `parse_failed`, other failures to `unavailable`.
25. Make `AppStoreAdapter` pass the App Store Contract Fixtures.
26. Route each App to its adapter by the App Key prefix in `job/sources/registry.py`. Select the App Store Active Source with `APP_STORE_SOURCE`, default `itunes_api`.
27. Run the Store Source health check once per Store in `job/daily.py`.
28. Add `AppStoreLookup` to `web/src/sources/lookup.ts` and make it pass the App Store Contract Fixtures.

### Phase D — `/add`
29. Add `detectStore(input)` to `web/src/sources/contract.ts`.
30. Add `parseAppStore(input)` and extend `parsePackage(input)` to return the store ID and the Link Country (null when the input has none).
31. Send `TEXT.addInvalid` when `detectStore` returns null.
32. Use the Link Country when the input has one.
33. Use the Timezone Country when there is no Link Country and Timezone Set is true.
34. Use the Saved Country when neither applies.
35. Otherwise, save a Pending Reply `{kind: "add_country", input}` and send the Country Question.
36. On a plain-text reply while a Pending Reply of kind `add_country` exists, read a two-letter code, trimmed and case-insensitive.
37. When the code is not a Valid Country Code, reply "<code> isn't a country code I know. Send two letters, like IN, US or GB." and keep the Pending Reply.
38. When the code is valid, save it as the Saved Country, clear the Pending Reply and continue the add with that country.
39. Look up the App in the chosen Store Country.
40. When the App is not found, run the Availability Check.
41. When the Availability Check finds the App, reply "<name> isn't on <Store> in <country>. It's available in US, GB. Send one of these codes, or another country code." and save a Pending Reply `{kind: "add_country", input}`.
42. When the Availability Check finds nothing, send `TEXT.notFound`.
43. When the App is found, build the App Key, add the App to the Watchlist and send the Add Confirmation.
44. Clear a Pending Reply when the User sends any command, and ignore Pending Replies past `expires_at`.

### Phase E — Country Change
45. Add a "🌍 Change country" button to each App in `/apps` and to the Add Confirmation.
46. On tap, save a Pending Reply `{kind: "change_country", app_key}` and send "Which country should I track <name> in? It's <country> now. Send a two-letter code."
47. Validate the reply as in steps 36–37.
48. When the code equals the current Store Country, reply "<name> is already tracked in <country>." and clear the Pending Reply.
49. Look up the App in the new country. When it is not found, run the Availability Check and reply as in steps 41–42. Keep the old country.
50. When found, replace the Watchlist row's App Key with the new App Key in one transaction, keeping its paused state.
51. Send "🌍 <name> moved from <old Store Badge> to <new Store Badge>. Ratings, reviews and trends now come from <country>; the trend starts fresh today."
52. Start an On-demand Run for the new App Key so the User gets its first Update within minutes.
53. Leave the old App in the database; the existing cleanup removes unwatched Apps.

### Phase F — Cards and captions
54. Replace the fixed "Google Play" in `daily_caption` with the Store Badge.
55. Add the Store Badge to the Card header in `job/card/render.py`.
56. Hide the installs stat on App Store Cards.
57. Render one daily Card for an App Store App and one for a Play App with a non-US country, and check both at 1200×630.

### Phase G — Current Apps
58. After step 11, add `play:in:<package>` for each current App and move every Watchlist row from `play:us:<package>` to `play:in:<package>`.
59. Send each affected User one message: "Your apps now use India's Play Store ratings and reviews. Change any app's country in /apps."

### Phase H — Dashboard and docs
60. Add Store and Store Country columns to the Dashboard Apps page.
61. Split "Apps watched" by Store on the Overview page.
62. Add the country rules and App Store support to README.md, PLAN.md Terms, `TEXT.welcome`, `TEXT.addUsage` and the Bot description in `job/bot_setup.py`.
63. Run the Bot Setup Workflow.

## Done when

- `TEST_DATABASE_URL=… python -m pytest` and `cd web && npm test && npm run build` pass, including App Store contract tests (large, small, removed), `detectStore` and Link Country tests for every link form, and Pending Reply tests.
- `/add https://apps.apple.com/us/app/whatsapp-messenger/id310633997` adds `ios:us:310633997` with no question and confirms "✅ Added WhatsApp Messenger · App Store · US".
- `/add https://play.google.com/store/apps/details?id=com.zerodha.kite3&gl=IN` adds `play:in:com.zerodha.kite3` with no question.
- `/add com.whatsapp` from a User with Timezone Set and timezone `Europe/London` adds `play:gb:com.whatsapp` with no question.
- `/add 310633997` from a User without Timezone Set and without Saved Country gets the Country Question. Replying `xx` gets the invalid-code reply; replying `in` adds `ios:in:310633997` and saves `in` as the Saved Country.
- A second `/add` with a bare ID from that User adds with no question.
- Adding an App missing from the chosen country replies with the countries where it exists and adds nothing until the User sends one of them.
- Tapping Change country on WhatsApp and replying `us` moves the Watchlist row to `ios:us:310633997`, sends the "moved from … to …" message, and a new Update arrives within 10 minutes.
- After Phase G, the current Apps show "Google Play · IN" and their ratings match the Play Store app on an Indian phone.
- The next Daily Job fetches Apps of both Stores and several countries, and every Update shows its Store Badge.
