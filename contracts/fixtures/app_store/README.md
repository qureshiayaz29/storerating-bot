# App Store Contract Fixtures

Raw responses from Apple's public endpoints, replayed by `AppStoreAdapter` (Python)
and `AppStoreLookup` (TypeScript). Each case has:

- `case.json` — track ID, country, and `synthetic` (true until recorded for real).
- `lookup.json` — iTunes Lookup API response.
- `reviews_pages.json` — customer reviews RSS (JSON) pages, newest first.
- `expected_listing.json`, `expected_reviews.json` — what every adapter must return.

Replace synthetic cases with real ones via the **Record fixtures** workflow (store: app_store).
