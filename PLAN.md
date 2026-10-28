# StoreRating — Build Plan

## Goal

Build **StoreRating** (`@storeratingbot`), a free Telegram bot that watches Google Play apps and tells developers, PMs and founders how their apps and competitors are doing: rating changes, new releases, review complaint themes and sudden 1★ spikes.

Why: a resume-worthy project that proves scheduled data pipelines, LLM summarisation at scale, fan-out notifications and cost-aware architecture, with real users and real numbers.

Constraints:
- Free for users. No paid tier.
- Near-zero running cost. Only the Claude API is paid; everything else stays on free tiers.
- Must scale to ~1,000 users × ~9 apps without a cost or rate-limit cliff.
- Fixed Claude budget from day 1. The Bot spends up to the budget, never past it, and degrades gracefully instead of failing. A budget is a design input, not a risk: every guardrail below exists so the money goes to the Apps people actually watch.
- Store only what is useful. Raw reviews are processed and discarded.
- Store minimal user data: chat ID, timezone, settings, watchlist. No names or usernames.

Rejected approaches:
- **Resume-tailoring bot** — too many failure points in PDF generation, and little value beyond plain ChatGPT/Claude.
- **Per-user scraping and summarising** — cost grows with users. Work is done once per app and shared.
- **Storing raw reviews** — grows too fast for free DB tiers. Snapshots and summaries hold the long-term story.
- **Rendering cards on Cloudflare Workers** — the free plan allows 10 ms CPU per invocation, too little for PNG rendering.
- **Separate image and text messages** — doubles Telegram volume. One photo with caption per app.
- **Paid tier with faster checks** — the bot stays free.
- **Calling `google-play-scraper` directly from job code** — the library is unofficial and breaks when Google changes the page. All store access goes through the Store Source contract so the library can be swapped in one place.
- **"Play" in the name** — "Google Play" is a Google trademark.

## Terms

- **Bot** — the Telegram bot `@storeratingbot`.
- **User** — a Telegram chat that has sent `/start` to the Bot.
- **Admin** — Ayaz; identified by `ADMIN_CHAT_ID`.
- **Admin Command** — `/stats`, `/budget`, `/setbudget`. Works only for the Admin.
- **Admin Check** — the single function `isAdmin(update)` in the Webhook Worker. True only when `message.from.id` equals `ADMIN_CHAT_ID` and the chat type is `private`.
- **App** — a Google Play listing identified by its package name (e.g. `com.whatsapp`).
- **Watchlist** — the set of Apps a User follows. Maximum 10 Apps.
- **Watcher** — a User who has a given App in their Watchlist.
- **Webhook Worker** — the Cloudflare Worker that receives Telegram updates and answers commands and button taps.
- **Daily Job** — the GitHub Actions workflow that fetches, summarises, renders and sends.
- **Store Source** — the contract for all store access: `get_listing(package)`, `get_reviews(package, since_id)`, `exists(package)`. Defined once in `contracts/`. No other code talks to a store directly.
- **Source Adapter** — one implementation of the Store Source. First adapter: `GooglePlayScraperAdapter` (JoMingyu `google-play-scraper`). Possible later adapters: the Node `google-play-scraper` through `google-play-api`, a paid scraping API, the official Play Developer API for Apps a User owns.
- **Active Source** — the Source Adapter selected by `STORE_SOURCE`. Changed without code edits.
- **Fallback Source** — the Source Adapter used when the Active Source fails for an App. Selected by `STORE_SOURCE_FALLBACK`. Optional.
- **Listing** — the normalised App data every Source Adapter returns: `store`, `package`, `name`, `developer`, `icon_url`, `version`, `rating`, `review_count`, `installs_tier`, `size`, `updated_at`, `release_notes`. Missing values are `null`, never adapter-specific.
- **Review** — the normalised review every Source Adapter returns: `id`, `stars`, `text`, `thumbs_up`, `app_version`, `created_at`. No reviewer name or avatar.
- **Lookup Adapter** — the TypeScript implementation of `exists` and `get_listing` used by the Webhook Worker for `/add`. Returns the same Listing.
- **Contract Fixtures** — saved raw responses plus the expected Listing and Review JSON in `contracts/fixtures/`. Every Source Adapter and the Lookup Adapter must pass them.
- **Fetch** — one call to the Active Source for an App's Listing and new Reviews.
- **Snapshot** — one stored row of an App's listing data for one day.
- **Summary** — Claude's output for one App for one period: top 3 complaint themes and top 3 praise themes with rough shares.
- **Card** — the 1200×630 PNG image for one App for one period.
- **Update** — one Telegram message: a Card with a caption. Sent once per App per Watcher.
- **Daily Update**, **Monthly Report**, **Quarterly Report** — Updates for those periods.
- **Alert** — an Update sent outside the schedule because of an Alert Event.
- **Alert Event** — new version released, rating drop ≥ 0.1 within 7 days, 1★ share spike, or App removed from the store.
- **Digest Time** — the local time a User receives Daily Updates. Default 11:00, timezone default `Asia/Kolkata`.
- **Global Pause** — `users.paused = true`. No Updates or Alerts to that User.
- **App Pause** — `watchlist.paused = true`. No Updates or Alerts for that App to that User.
- **On-demand Run** — a one-off Daily Job run for Apps that have no Card for today, started by the Webhook Worker through `repository_dispatch`.
- **New-App Quota** — the maximum number of On-demand Run Apps one User can trigger per day. Value: 5.
- **On-demand Budget** — the maximum number of On-demand Run Apps across all Users per day. Value: 200.
- **Action Limit** — the maximum number of add and remove actions one User can do per hour. Value: 20.
- **Monthly Budget** — the maximum Claude spend per calendar month. Value: $30. Set in two places that must match: the spend limit in the Anthropic Console, and `monthly_budget_usd` in the `config` table (seeded from `MONTHLY_BUDGET_USD`).
- **Daily Budget** — `MONTHLY_BUDGET_USD` ÷ days in the month. The Daily Job and On-demand Runs share it.
- **Spend Ledger** — the `spend` table. One row per Claude call with tokens and cost in USD.
- **Priority Order** — the order Apps get Summaries in: most Watchers first, then most new reviews.
- **Degrade Mode** — the state when today's spend reaches 80% of the Daily Budget. Remaining Apps get Updates from Snapshot data only (rating, version, counts) and no Summary.
- **Top-up Alert** — the Admin Telegram message sent when month-to-date spend reaches 70% of the Monthly Budget. Contains spent, remaining, days left, projected month-end spend and a link to the Anthropic Console billing page.
- **Pace Alert** — the Admin Telegram message sent when projected month-end spend is more than the Monthly Budget, even below 70%.
- **Kill Switch** — `CLAUDE_ENABLED=false`. Stops every Claude call at once; Updates continue in Degrade Mode.
- **Event** — one row in the `events` table describing a User action or delivery result.
- **Dashboard** — the Admin-only web page that shows metrics. Static frontend on Cloudflare Pages.
- **Stats API** — the Webhook Worker routes under `/admin/api/*` that read the database and return JSON to the Dashboard.
- **Daily Stats** — the `daily_stats` table. One row per day of pre-computed metrics, written by the Daily Job.

## Architecture

| Part | Runs on | Language |
|---|---|---|
| Webhook Worker | Cloudflare Workers (free) | TypeScript |
| Store Source | `contracts/` (JSON Schema), Source Adapters in `job/sources/`, Lookup Adapter in `worker/src/sources/` | Python, TypeScript |
| Daily Job | GitHub Actions (cron) | Python 3.11+ |
| Database | Supabase free (Postgres) | SQL |
| Summaries | Claude Haiku 5.5, Batch API for scheduled work | — |
| Card render | Playwright (HTML/CSS + inline SVG chart) in Daily Job | Python |
| Dashboard | Cloudflare Pages + Cloudflare Access | Astro or HTML + Chart.js |
| Stats API | Webhook Worker, `/admin/api/*`, behind Cloudflare Access | TypeScript |

Store Source layout:
```
contracts/
  listing.schema.json      # Listing
  review.schema.json       # Review
  fixtures/                # raw responses + expected JSON per adapter
job/sources/
  base.py                  # StoreSource protocol, Listing, Review dataclasses, SourceError
  google_play_scraper.py   # GooglePlayScraperAdapter
  registry.py              # STORE_SOURCE / STORE_SOURCE_FALLBACK → adapter
worker/src/sources/
  lookup.ts                # Lookup Adapter
```
Rules:
- Job code imports only `job/sources/registry.py`. Never a scraping library.
- Every Source Adapter raises only `SourceError` with a reason: `not_found`, `rate_limited`, `blocked`, `parse_failed`, `unavailable`.
- Every Source Adapter validates its output against the JSON Schemas before returning.
- Database rows store the `store` value and the adapter name, so data from different adapters can be told apart.

Data flow per day:
1. Daily Job does a Fetch for every App with at least one Watcher.
2. Daily Job writes a Snapshot and detects Alert Events by diffing against the previous Snapshot.
3. Daily Job filters new reviews (star split, junk removal, rank by thumbs-up and recency) and submits one Batch API request per App.
4. Daily Job renders one Card per changed App.
5. Daily Job sends the Card to the first Watcher, stores the returned Telegram `file_id`, and resends by `file_id` to every other Watcher.
6. Daily Job writes a `job_runs` row and sends the Admin an alert on failure.

## User flow

First use and adding an App:

```mermaid
flowchart TD
    A[Start bot<br/>User sends /start] --> B[Welcome<br/>How it works, try /add]
    B --> C[Add an app<br/>/add or paste a link]
    C --> D{App found?}
    D -- No --> D1[Not found<br/>Ask to check link]
    D -- Yes --> E{Watchlist full?<br/>Max 10 apps}
    E -- Yes --> E1[Limit reached<br/>Remove one first]
    E -- No --> F{Card ready today?}
    F -- Yes --> F1[Card sent now<br/>Instant, no cost]
    F -- No --> F2[Basic info now<br/>Card in 2–5 min]
    F1 --> G[Daily updates<br/>11 AM, one card per app]
    F2 --> G
```

Every day after that:

```mermaid
flowchart TD
    A[Daily at 11 AM<br/>User's own timezone] --> C{Paused?<br/>All apps or this app}
    B[Instant alert<br/>Release, drop, 1★ spike] --> C
    C -- Yes --> C1[Skipped<br/>Still tracked]
    C -- No --> D{Anything new?<br/>Since last update}
    D -- No --> D1[Nothing sent]
    D -- Yes --> E[Card + summary<br/>One message per app]
```

Managing Apps is available anytime from `/apps`: pause or resume one App, remove one (with confirmation), or pause all. Pausing never stops tracking.

## Data model

- `users` — `chat_id` (PK), `language_code`, `timezone`, `digest_time`, `paused`, `blocked`, `created_at`, `last_active_at`
- `watchlist` — `chat_id`, `package`, `paused`, `added_at` (PK `chat_id`, `package`)
- `apps` — `store` (default `google_play`), `package` (PK), `name`, `developer`, `icon_url`, `first_seen_at`, `removed`
- `app_snapshots` — `package`, `date`, `source_adapter`, `version`, `rating`, `review_count`, `installs_tier`, `size`, `one_star_share` (PK `package`, `date`)
- `app_cursor` — `package` (PK), `last_review_id`, `last_review_at`
- `summaries` — `package`, `period` (`daily` | `monthly` | `quarterly`), `period_start`, `complaints` (JSON), `praise` (JSON), `verdict`
- `cards` — `package`, `period`, `period_start`, `telegram_file_id`
- `on_demand` — `chat_id`, `package`, `requested_at`, `status` (`pending` | `done` | `failed`)
- `spend` — `id`, `ts`, `source` (`daily` | `on_demand` | `report`), `package`, `model`, `batch`, `tokens_in`, `tokens_out`, `cost_usd`
- `config` — `key` (PK), `value`, `updated_at`. Holds `monthly_budget_usd`.
- `daily_stats` — `date` (PK), `users_total`, `users_new`, `dau_active`, `dau_reached`, `blocked_total`, `blocked_new`, `apps_watched`, `updates_sent`, `alerts_sent`, `claude_cost_usd`, `d1_retention`, `d7_retention`, `d30_retention`
- `events` — `id`, `chat_id`, `event`, `props` (JSON), `ts`
- `job_runs` — `id`, `started_at`, `finished_at`, `apps_fetched`, `apps_failed`, `reviews_processed`, `claude_calls`, `claude_tokens_in`, `claude_tokens_out`, `cards_rendered`, `sent`, `failed`, `rate_limited`
- `errors` — `id`, `source` (`store_source` | `claude` | `render` | `telegram` | `worker`), `package`, `message`, `stack`, `ts`

Retention: `events` and `errors` 90 days, then roll into daily aggregates. `app_snapshots` daily for 90 days, then weekly averages. `summaries` kept.

## Features

**Watchlist**
- `/add <link or package>`, or paste a Play Store link.
- `/apps` lists Apps with inline buttons: Pause, Resume, Remove (with confirmation), Add, Pause all.
- At 10 Apps, `/add` shows the Watchlist with Remove buttons.
- After `/add`, the User gets an Update for that App immediately:
  - App already has today's Card → the Worker sends the stored `file_id` at once. No cost.
  - App has no Card today → the Worker replies with basic listing data, then starts an On-demand Run. The Card arrives in about 2–5 minutes. Adds within 60 seconds share one On-demand Run.
- Remove keeps the App's data. Re-adding the same App the same day sends the stored Card, so add/remove churn costs nothing.
- Abuse limits:
  - Above the New-App Quota, the App is added, and its first Update arrives at the next Digest Time.
  - Above the On-demand Budget, same behaviour for every User, and the Admin gets an alert.
  - Above the Action Limit, the Bot replies "Too many changes, try again in a few minutes" and changes nothing.
  - Users who hit the Action Limit 3 times in a day are flagged on the Dashboard.
- The Daily Job processes only Apps that have at least one Watcher when it starts, so removed Apps stop costing.

**Notifications**
- `/pause`, `/resume` — Global Pause.
- App Pause and resume from `/apps`.
- Data collection continues while paused.

**Updates**
- Daily Update at Digest Time, one per changed App. Unchanged Apps are skipped.
- Monthly Report on the 1st, Quarterly Report on the 1st of Jan, Apr, Jul, Oct.
- Card + caption ≤ 1,024 characters.
- First Update of a send run makes a sound; the rest use `disable_notification`.

**Alerts**
- Sent when an Alert Event is detected, outside the schedule.

**Settings**
- `/settings` — Digest Time, timezone.
- `/delete` — deletes all rows for the User.

**Budget guardrails (day 1)**
- Monthly Budget set in the Anthropic Console as a hard stop, and enforced again in code.
- Every Claude call is checked against the remaining Daily Budget before it is sent, and written to the Spend Ledger after.
- Summaries run in Priority Order, so if money runs out, the long tail misses out, not popular Apps.
- Degrade Mode at 80% of the Daily Budget: Updates still go out, with Snapshot data and no Summary.
- Unused Daily Budget rolls over within the month.
- Cheapest path by default: Haiku 5.5, Batch API for all scheduled work, standard calls only for On-demand Runs and Alerts.
- Every prompt stays under 100,000 tokens, so it is billed at the lower Haiku 5.5 rate. Input capped at 150 reviews per side, review text truncated at 500 characters, `max_tokens` 600.
- No Summary for an App with fewer than 5 new reviews since the last Summary.
- Monthly and Quarterly Reports are built from stored Summaries, not raw reviews.
- Top-up Alert to the Admin on Telegram at 70% of the Monthly Budget, so there is time to top up before Degrade Mode.
- Pace Alert when spend is on track to pass the Monthly Budget before the month ends.
- Admin alerts at 50%, 80% (Degrade Mode near) and 100% (summaries stopped) of the Monthly Budget.
- Kill Switch for emergencies.
- Topping up credit does not raise the cap. To spend more, the Admin does all three:
  1. Top up credit in the Anthropic Console.
  2. Raise the spend limit in the Anthropic Console.
  3. Send `/setbudget <amount>` to the Bot.
- Every budget alert and `/budget` reply ends with this reminder.
- Dashboard shows spend per day, per source and per App, and cost per active User.

**Admin access**
- Every Admin Command, Admin alert, budget alert, Top-up Alert, Pace Alert, weekly Admin report and the Dashboard are for the Admin only.
- Admin Commands run only after the Admin Check. Checking the sender ID, not only the chat ID, blocks use from groups the Admin is in.
- For anyone else, Admin Commands get the same reply as an unknown command, so their existence is not revealed.
- Every rejected Admin Command writes an Event `admin_denied` with the sender's `chat_id`.
- Admin Commands appear in the command menu only in the Admin's chat (`setMyCommands` with `BotCommandScopeChat`).
- Admin alerts are sent only to `ADMIN_CHAT_ID`, never to Users.
- The Webhook Worker rejects requests without the correct `X-Telegram-Bot-Api-Secret-Token` header, so nobody can fake a Telegram update.
- The Dashboard is behind Cloudflare Access for the Admin email only.

**Admin**
- Admin alerts in Telegram: Daily Job failure, Store Source health-check failure, Fallback Source in use, Claude spend over threshold, block-rate spike.
- `/stats` (Admin only).
- Weekly Admin report Card every Monday.
- Dashboard: Overview, Retention, Funnel, Apps, Jobs, Errors, Budget.

**Dashboard data**
- Login: Cloudflare Access in front of both the Dashboard and the Stats API. Policy allows only the Admin email. Login by one-time code sent to that email.
- The Stats API verifies the `Cf-Access-Jwt-Assertion` header on every request, so the API stays closed even if the Access policy is misconfigured.
- History comes from Daily Stats. Today's numbers come from live queries on `events`, `users`, `watchlist`, `spend`, `job_runs` and `errors`.
- No stats files in the repository. The repository is public; all numbers stay in the database.
- The database credentials live only in the Worker and in repository secrets, never in the Dashboard frontend.

| Page | Shows | Source |
|---|---|---|
| Overview | Total and new Users, DAU active vs reached, block rate, updates sent, Claude spend today | `daily_stats` + live `events`, `users`, `spend` |
| Retention | D1, D7, D30 by signup week | `daily_stats`, `users.created_at`, `events` |
| Funnel | Start → first App added → first Update → still subscribed day 7 | `events`, `users` |
| Apps | Most watched, failed `/add` reasons, Store Source failures | `watchlist`, `events`, `errors` |
| Jobs | Duration, Apps fetched, sends, 429s per run | `job_runs` |
| Errors | Latest errors by source with stack | `errors` |
| Budget | Month-to-date spend, projection, spend per App and per source, flagged Users | `spend`, `config`, `events` |

## Steps

### Phase 0 — Setup
1. Set the Bot description with `/setdescription` in @BotFather.
2. Set the Bot about text with `/setabouttext` in @BotFather.
3. Set the Bot profile picture with `/setuserpic` in @BotFather.
4. Set the Bot command list with `/setcommands` in @BotFather: `add`, `apps`, `pause`, `resume`, `settings`, `delete`. Keep the Admin commands `/stats`, `/budget` and `/setbudget` out of the list.
5. Send one message to the Bot from your Telegram account.
6. Read your `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
7. Create an API key in the Anthropic Console.
8. Set the Monthly Budget ($30) as the spend limit in the Anthropic Console.
9. Create a free Supabase project.
10. Copy the Supabase connection string.
11. Add the secrets `BOT_TOKEN`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, `ADMIN_CHAT_ID` to this repository.
12. Create a Cloudflare account.

### Phase 1 — Database
13. Write the migration file `db/001_init.sql` with every table in "Data model".
14. Apply `db/001_init.sql` to the Supabase project.

### Phase 2 — Store Source
15. Write `contracts/listing.schema.json` for the Listing.
16. Write `contracts/review.schema.json` for the Review.
17. Write `job/sources/base.py` with the `StoreSource` protocol, the `Listing` and `Review` dataclasses and `SourceError`.
18. Write `job/sources/google_play_scraper.py` with `GooglePlayScraperAdapter`.
19. Map every library field to the Listing and the Review in `GooglePlayScraperAdapter`. Set unknown values to `null`.
20. Map every library exception to a `SourceError` reason.
21. Validate the adapter output against the JSON Schemas.
22. Write `job/sources/registry.py`. Return the Active Source from `STORE_SOURCE` and the Fallback Source from `STORE_SOURCE_FALLBACK`.
23. Save raw responses for 3 Apps (one large, one small, one removed) to `contracts/fixtures/`.
24. Write the contract tests. Run every Source Adapter against `contracts/fixtures/`.
25. Write `worker/src/sources/lookup.ts` with the Lookup Adapter.
26. Run the Lookup Adapter against `contracts/fixtures/`.
27. Add a CI check that fails when any file outside `job/sources/` imports `google_play_scraper`.

### Phase 3 — Webhook Worker
28. Create the Worker project in `worker/` with Wrangler.
29. Add `BOT_TOKEN`, `DATABASE_URL` and `ADMIN_CHAT_ID` as Worker secrets.
30. Reject every webhook request without the correct `X-Telegram-Bot-Api-Secret-Token` header with HTTP 401.
31. Implement the Admin Check `isAdmin(update)`.
32. Route every Admin Command through the Admin Check.
33. Reply to a rejected Admin Command with the unknown-command reply. Write an Event `admin_denied`.
34. Set the Admin command menu with `setMyCommands` and `BotCommandScopeChat` for `ADMIN_CHAT_ID`: `stats`, `budget`, `setbudget`.
35. Implement the `/start` handler. Insert the User into `users`.
36. Implement the `/add` handler. Validate the package with the Lookup Adapter. Insert the row into `watchlist`. Insert the App into `apps`.
37. Implement the 10-App limit in the `/add` handler.
38. Implement the `/apps` handler with inline buttons.
39. Implement the Pause, Resume and Remove button callbacks with `editMessageText`.
40. Implement the `/pause` and `/resume` handlers.
41. Implement the `/settings` handler.
42. Implement the `/delete` handler.
43. Write an Event for every handler.
44. Write an `errors` row for every caught exception.
45. Deploy the Worker.
46. Set the Telegram webhook to the Worker URL with `setWebhook` and a secret token.

### Phase 4 — Fetch and Snapshots
47. Create the Python package in `job/`.
48. Implement the Fetch through `registry.py` for one App.
49. Use the Fallback Source when the Active Source raises `SourceError` with `blocked`, `rate_limited` or `parse_failed`. Write an `errors` row.
50. Write the Snapshot to `app_snapshots` with `source_adapter`.
51. Update `app_cursor` with the newest Review.
52. Implement the Store Source health check: Fetch `com.whatsapp` with the Active Source and validate the Listing against the JSON Schema.
53. Send an Admin alert when the health check fails or when the Fallback Source is used.

### Phase 5 — Alert Events
54. Implement the diff between today's Snapshot and the previous Snapshot.
55. Detect each Alert Event type.
56. Send Alerts to every Watcher without App Pause or Global Pause.

### Phase 6 — Budget Guardrails
57. Add the table `spend` from "Data model".
58. Check that the Anthropic Console spend limit equals the Monthly Budget.
59. Add `MONTHLY_BUDGET_USD` and `CLAUDE_ENABLED` as repository secrets and Worker secrets.
60. Write the module `job/budget.py` with the Haiku 5.5 standard and batch rates per million tokens.
61. Implement the function `remaining_today()`: Daily Budget plus unused budget from earlier days this month, minus today's spend in the Spend Ledger.
62. Implement the function `can_spend(estimated_cost)`. Return false when `CLAUDE_ENABLED` is false or when `estimated_cost` is more than `remaining_today()`.
63. Implement the token estimate for a prompt before sending.
64. Reject every prompt with an estimate over 100,000 tokens.
65. Wrap every Claude call in `can_spend()`.
66. Write one Spend Ledger row after every Claude call with the token counts from the API response.
67. Sort Apps in Priority Order before Summaries.
68. Switch to Degrade Mode when today's spend reaches 80% of the Daily Budget.
69. Skip the Summary for every App with fewer than 5 new reviews.
70. Send an Admin alert at 50%, 80% and 100% of the Monthly Budget, once per threshold per month.
71. Send the Top-up Alert at 70% of the Monthly Budget, once per month. Format:
    ```
    💰 StoreRating budget at 70%
    Spent $21.00 of $30.00 · $9.00 left
    12 days left · projected $33.40 by month end
    Top up: https://console.anthropic.com/settings/billing
    To spend more: top up, raise the Console limit, then /setbudget <amount>
    ```
72. Compute the projected month-end spend as month-to-date spend ÷ days elapsed × days in month.
73. Send the Pace Alert when the projected month-end spend is more than the Monthly Budget, at most once per 3 days.
74. Add a `/budget` handler for the Admin only. Reply with the Top-up Alert text for the current state.
75. Read the Monthly Budget from the `config` table in `job/budget.py` and in the Webhook Worker. Use `MONTHLY_BUDGET_USD` only to seed the row.
76. Add a `/setbudget <amount>` handler for the Admin only. Update `monthly_budget_usd` in `config`.
77. Reply to `/setbudget` with the new budget, the new Daily Budget and the reminder: "Check that the Anthropic Console spend limit is at least $<amount>."
78. Reset the 50%, 70%, 80% and 100% alert flags for the month when the Monthly Budget changes.
79. Write a test that runs the Summary step with a $0.01 Daily Budget and asserts Degrade Mode.

### Phase 7 — Summaries
80. Implement the review filter: star split, junk removal, rank by thumbs-up and recency, cap at 150 reviews per side.
81. Write the Summary prompt. Return JSON with themes, shares, one short example each and a one-line verdict.
82. Submit all Apps as one Batch API request.
83. Poll the batch until it ends.
84. Fall back to a standard API call for every App without a result at 10:45 local.
85. Write each Summary to `summaries`.
86. Log tokens in `job_runs`.

### Phase 8 — Cards
87. Move the Card HTML template to `job/card/`.
88. Render one Card per changed App with Playwright.
89. Write the caption template. Keep it under 1,024 characters.

### Phase 9 — Sending
90. Implement the send queue with a pace of 25 messages per second overall and 1 per second per chat.
91. Retry on HTTP 429 after `retry_after` seconds.
92. Set `users.blocked = true` on HTTP 403.
93. Send the Card to the first Watcher with `sendPhoto`. Store the returned `file_id` in `cards`.
94. Send the stored `file_id` to every other Watcher.
95. Set `disable_notification` on every Update after the first per User per run.
96. Send Updates per User at their Digest Time.

### Phase 10 — On-demand Runs
97. Add the table `on_demand` with `chat_id`, `package`, `requested_at`, `status`.
98. In the `/add` handler, look up today's Card for the App in `cards`.
99. Send the stored `file_id` to the User when today's Card exists.
100. Reply with basic listing data when today's Card does not exist.
101. Count the User's On-demand Run Apps for today in `on_demand`.
102. Skip the On-demand Run when the count is at the New-App Quota. Reply that the first Update arrives at the next Digest Time.
103. Count all On-demand Run Apps for today in `on_demand`.
104. Skip the On-demand Run when the count is at the On-demand Budget. Send an Admin alert once per day.
105. Insert a pending row into `on_demand`.
106. Start the On-demand Run with `repository_dispatch` 60 seconds after the last pending row for that User.
107. Write `.github/workflows/on-demand.yml` triggered by `repository_dispatch`.
108. In the On-demand Run, Fetch every pending App, make one standard Claude call per App, render the Card, send it, and store the `file_id` in `cards`.
109. Set the `on_demand` rows to `done` or `failed`.
110. Count add and remove actions per User in the last hour from `events`.
111. Reject the action and reply "Too many changes, try again in a few minutes" when the count is at the Action Limit.
112. Write an Event `action_limited` for every rejected action.
113. Show Users with 3 or more `action_limited` Events in one day on the Dashboard.
114. In the Daily Job, select only Apps with at least one row in `watchlist`.

### Phase 11 — Schedule
115. Write `.github/workflows/daily.yml` with a cron for Fetch and Batch submission at 05:00 IST.
116. Write the send workflow with a cron every 30 minutes for Digest Times.
117. Write the Monthly and Quarterly Report workflows.
118. Write a `job_runs` row at the end of every workflow.
119. Send an Admin alert when a workflow fails.

### Phase 12 — Admin and Dashboard
120. Implement the `/stats` handler for the Admin only.
121. Write the stats builder in the Daily Job. Write one `daily_stats` row for yesterday.
122. Add the Stats API routes `/admin/api/overview`, `/retention`, `/funnel`, `/apps`, `/jobs`, `/errors`, `/budget` to the Webhook Worker.
123. Verify the `Cf-Access-Jwt-Assertion` header in every Stats API route. Return HTTP 403 when it is missing or invalid.
124. Build the Dashboard pages in `dashboard/`.
125. Deploy the Dashboard to Cloudflare Pages.
126. Protect the Dashboard and `/admin/api/*` with one Cloudflare Access application. Allow only the Admin email. Use one-time code login.
127. Implement the weekly Admin report Card.

### Phase 13 — Launch
128. Write the privacy note: "We store your chat ID, timezone and watchlist. Nothing else. /delete removes it all."
129. Add the privacy note to the Bot description and the README.
130. Test every command with a second Telegram account.
131. Announce the Bot on LinkedIn and Inside Android.

## Done when

- `/start`, `/add`, `/apps`, `/pause`, `/resume`, `/settings` and `/delete` each give the expected reply within 2 seconds from a second Telegram account.
- Adding an 11th App shows the Watchlist with Remove buttons and does not add the App.
- `select count(*) from app_snapshots where date = current_date` equals the number of Apps with at least one Watcher after the Daily Job.
- A Daily Update arrives at 11:00 IST with a Card and a caption under 1,024 characters.
- Two Users watching the same App receive identical Updates, and `cards.telegram_file_id` has one row for that App and day.
- A User with App Pause receives no Update for that App; a User with Global Pause receives nothing.
- Blocking the Bot from a test account sets `users.blocked = true` after the next send.
- `/delete` leaves zero rows for that `chat_id` in `users`, `watchlist` and `events`.
- Breaking the scraper (wrong field name) sends an Admin alert within one run.
- `grep -rn "google_play_scraper" --include=*.py . | grep -v "^./job/sources/"` returns nothing.
- Every Source Adapter and the Lookup Adapter pass the contract tests: `pytest job/tests/test_contracts.py` and `npm test` in `worker/`.
- Changing `STORE_SOURCE` to a stub adapter makes the Daily Job run end to end with no other code change.
- Making the Active Source raise `blocked` for one App switches that App to the Fallback Source, writes an `errors` row and sends an Admin alert.
- `job_runs` has one row per workflow run with non-zero `apps_fetched`.
- `select sum(cost_usd) from spend where ts::date = current_date` never exceeds that day's Daily Budget plus rollover.
- `select sum(cost_usd) from spend where date_trunc('month', ts) = date_trunc('month', now())` never exceeds `MONTHLY_BUDGET_USD`.
- Every Claude call has one matching row in `spend`.
- The $0.01 Daily Budget test passes: Updates go out with Snapshot data only, and no Claude call is made.
- Setting `CLAUDE_ENABLED=false` stops all Claude calls on the next run while Updates still arrive.
- Admin alerts arrive at 50%, 80% and 100% of the Monthly Budget, once each per month.
- Inserting test `spend` rows up to 70% of the Monthly Budget sends exactly one Top-up Alert to `ADMIN_CHAT_ID` on the next run, with correct spent, left and projected values.
- Inserting test `spend` rows that project past the Monthly Budget while under 70% sends one Pace Alert.
- `/budget` from the Admin returns the current budget state; `/budget` from any other User returns nothing.
- `/setbudget 40` from the Admin sets `monthly_budget_usd` to 40, and the next run uses the new Daily Budget without a redeploy.
- `/setbudget` from any other User changes nothing.
- `/stats`, `/budget` and `/setbudget` from a second Telegram account get the unknown-command reply and write an `admin_denied` Event.
- `/stats` sent by the Admin in a group chat gets no Admin reply.
- The command menu shows `stats`, `budget` and `setbudget` in the Admin's chat and not in a second account's chat.
- `curl -X POST <worker-url>` without the secret header returns HTTP 401.
- Every budget alert ends with the top-up, Console limit and `/setbudget` reminder.
- With money left for only half the Apps, the Apps with the most Watchers get Summaries first.
- The Dashboard loads for the Admin email and returns an Access login page for any other email.
- `curl <worker-url>/admin/api/overview` without an Access token returns HTTP 403.
- `daily_stats` has one row per day after the Daily Job.
- `git grep -i "DATABASE_URL\|stats.json" dashboard/` returns nothing.
- Adding an App that already has today's Card returns that Card within 2 seconds, and no `on_demand` row is created.
- Adding an App with no Card today returns basic listing data within 2 seconds and the Card within 5 minutes.
- Adding 5 new Apps within 60 seconds starts exactly one On-demand Run.
- The 6th new-App add in one day from one User creates no `on_demand` row.
- The 21st add or remove action within one hour from one User changes nothing and writes an `action_limited` Event.
- Removing and re-adding the same App on the same day creates no new `on_demand` row.
- Every GitHub Actions workflow shows green for 7 consecutive days.
