# Contract Fixtures — google_play_scraper

Each case folder holds:

- `case.json` — package name and what the case covers
- `app.json`, `reviews_pages.json` — the library's raw output (`app()` and pages of `reviews()`), fed to the adapter
- `expected_listing.json`, `expected_reviews.json` — the normalised Listing and Reviews the adapter must return
- `raises` in `case.json` — for error cases, the library exception and the expected `SourceError` reason

The expected files are written by hand, independently of the adapter, so the tests catch mapping mistakes.

Cases marked `"synthetic": true` mirror the library's output shape. Run the **Record fixtures** workflow to replace raw files with real recordings from GitHub Actions; the expected files then need a manual review.
