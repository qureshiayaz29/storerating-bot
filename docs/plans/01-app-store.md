# Plan: App Store support

Priority 1 in [ROADMAP.md](../../ROADMAP.md).

## Goal

Users watch iPhone apps the same way they watch Google Play apps: `/add` with an App Store link, the same Daily Updates, Alerts, Summaries and Reports. Most teams ship on both stores, and rival apps often get different complaints on iOS.

Constraints:
- Free to run. Use Apple's public endpoints only: the iTunes Lookup API for the Listing and the customer reviews RSS feed for Reviews. No API keys, no scraping, no paid service.
- App Store data is per country. Every App Store App is tracked in exactly one App Store Country.
- Reuse everything after the Store Source: Fetch, Alerts, Summaries, Cards, Sending, Reports, Dashboard. No second pipeline.
- Keep the `package` column as the single App key in every table. No foreign-key rework.
- The 10-app Watchlist limit covers both stores together.

Rejected:
- **Several countries combined per App.** More requests, and rating moves become hard to explain ("dropped in which country?"). One country per App.
- **A new `app_id` key with a store column in every table.** Touches 10 foreign keys and every query for no user-visible gain. An App Store Key in the existing `package` column does the same job.
- **Third-party scraper libraries** (`app-store-scraper`). They wrap the same Apple endpoints and add a dependency that can break. Call the endpoints directly.
- **Asking every User for a country up front.** Most links already contain the country. Ask only when the link does not.

## Terms

- **App Store App** — an iPhone app on Apple's App Store, identified by its numeric Track ID (e.g. `310633997` for WhatsApp).
- **App Store Country** — the two-letter App Store storefront code an App Store App is tracked in (e.g. `in`, `us`). Lower case.
- **App Store Key** — the `package` value for an App Store App: `ios:<country>:<track id>`, e.g. `ios:in:310633997`. Play package names never contain `:`, so keys cannot collide.
- **Store** — `google_play` or `app_store`. Stored in `apps.store`; derived from the key.
- **AppStoreAdapter** — the Source Adapter for App Store Apps. Listing from the iTunes Lookup API, Reviews from the customer reviews RSS feed.
- **Lookup API** — `https://itunes.apple.com/lookup?id=<track id>&country=<country>`.
- **Reviews Feed** — `https://itunes.apple.com/<country>/rss/customerreviews/page=<n>/id=<track id>/sortby=mostrecent/json`, pages 1–10, 50 Reviews each.
- **Saved Country** — `users.app_store_country`: the App Store Country a User last chose. Null until first chosen.
- **Country Prompt** — the Bot message with country buttons, sent when an `/add` gives no App Store Country and the User has no Saved Country.
- **Store Badge** — the small label on a Card and in its caption: "Google Play" or "App Store · IN".

## Steps

### Phase A — Contract and adapter
1. Add `app_store` to the `store` enum in `contracts/listing.schema.json`.
2. Record Lookup API and Reviews Feed responses for 3 App Store Apps (large, small, removed) in `contracts/fixtures/app_store/` with `job/record_fixtures.py`.
3. Write the expected Listing and Review JSON for each recorded App Store fixture.
4. Create `job/sources/app_store.py` with the class `AppStoreAdapter`.
5. Parse the App Store Key into App Store Country and Track ID in `AppStoreAdapter`.
6. Map Lookup API fields to the Listing: `trackName` → `name`, `sellerName` → `developer`, `artworkUrl512` → `icon_url`, `version`, `averageUserRating` → `rating`, `userRatingCount` → `review_count`, `fileSizeBytes` → `size`, `currentVersionReleaseDate` → `updated_at`, `releaseNotes` → `release_notes`. Set `installs_tier` to null.
7. Map Reviews Feed entries to Reviews: `id.label` → `id`, `im:rating.label` → `stars`, `title.label` and `content.label` joined → `text`, `im:voteCount.label` → `thumbs_up`, `im:version.label` → `app_version`, `updated.label` → `created_at` in UTC.
8. Stop reading Reviews Feed pages at the first Review older than `since_id`.
9. Return `SourceError('not_found')` when the Lookup API returns `resultCount: 0`.
10. Map HTTP 429 to `rate_limited`, HTTP 403 to `blocked`, bad JSON to `parse_failed`, other failures to `unavailable`.
11. Make `AppStoreAdapter` pass the App Store Contract Fixtures in `job/tests/test_contracts.py`.

### Phase B — Routing by Store
12. Add `store_of(package)` to `job/sources/registry.py`: `app_store` for keys that start with `ios:`, else `google_play`.
13. Make `registry.active_source()` and `registry.fallback_source()` take a Store.
14. Select the App Store Active Source with the environment variable `APP_STORE_SOURCE`, default `itunes_api`.
15. Pass the Store of each App to the registry in `job/fetch.py` and `job/on_demand.py`.
16. Run the Store Source health check once per Store in `job/daily.py`.

### Phase C — Database
17. Create `db/007_app_store.sql`.
18. Add the column `users.app_store_country text` with a check for two lower-case letters.
19. Add a check on `apps.store` for `google_play` or `app_store`.
20. Add a check that `apps.package` starts with `ios:` exactly when `apps.store` is `app_store`.
21. Apply `db/007_app_store.sql` to Supabase.

### Phase D — Webhook Function
22. Add `parseAppStore(input)` to `web/src/sources/contract.ts`. Accept `apps.apple.com/<country>/app/<slug>/id<track id>`, `apps.apple.com/app/id<track id>` and `id<track id>`. Return Track ID and App Store Country or null.
23. Add `AppStoreLookup` to `web/src/sources/lookup.ts` with `exists` and `getListing` on the Lookup API.
24. Make `AppStoreLookup` pass the App Store Contract Fixtures in `web/test/lookup.test.ts`.
25. In `/add`, use the App Store Country from the link when the link has one.
26. In `/add`, use the Saved Country when the link has no App Store Country.
27. In `/add`, send the Country Prompt when the link has no App Store Country and the User has no Saved Country.
28. Put the timezone's country first in the Country Prompt (map `users.timezone` to a country), then India, US, UK, and "Other…".
29. On "Other…", ask the User to send a two-letter code. Reject codes the Lookup API does not accept.
30. Save the chosen App Store Country as the Saved Country.
31. Build the App Store Key and continue the normal `/add` flow with it.
32. Add an "App Store country" row to `/settings` that changes the Saved Country. Existing App Store Apps keep their country.
33. Show the Store Badge for each App in `/apps`.
34. Add `/add` examples for App Store links to `TEXT.addUsage` and `TEXT.welcome`.

### Phase E — Cards and captions
35. Add the Store Badge to the Card header in `job/card/render.py`.
36. Add the Store Badge to the caption head line.
37. Show "App Store rating" instead of "rating" on App Store Cards. The Lookup API rating covers all versions in that country.
38. Hide the installs stat on App Store Cards. Apple does not publish installs.
39. Render one daily Card for an App Store App from the stub data and check it at 1200×630.

### Phase F — Dashboard and docs
40. Add a Store column to the Apps page of the Dashboard.
41. Split "Apps watched" by Store on the Overview page.
42. Add App Store to README.md, PLAN.md Terms and the Bot description in `job/bot_setup.py`.
43. Run the Bot Setup Workflow.

## Done when

- `TEST_DATABASE_URL=… python -m pytest` passes, including App Store contract tests for the large, small and removed fixtures.
- `cd web && npm test && npm run build` passes, including `parseAppStore` tests for all three link forms and the bare `id` form.
- `/add https://apps.apple.com/in/app/whatsapp-messenger/id310633997` adds `ios:in:310633997` without a Country Prompt and confirms with "App Store · IN".
- `/add id310633997` from a User with no Saved Country shows the Country Prompt with India first for an `Asia/Kolkata` User. One tap adds the App.
- The same User adding a second bare `id` gets no Country Prompt.
- A User can watch `com.whatsapp` and `ios:in:310633997` at the same time; both count toward the 10-app limit.
- The next Daily Job fetches the App Store App and its Reviews, writes a Snapshot, makes a Summary and a Card with the Store Badge, and the User receives it at Digest Time.
- An App Store App that returns `resultCount: 0` raises the "App removed" Alert.
- The Dashboard Apps page lists the App Store App with Store `app_store`.
- Claude spend per App Store App per day is within 10% of a Google Play App with a similar review count.
