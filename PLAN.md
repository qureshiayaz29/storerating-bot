# StoreRating — Build Plan

## Goal

Build **StoreRating** (`@storeratingbot`), a free Telegram bot that watches Google Play apps and tells developers, PMs and founders how their apps and competitors are doing: rating changes, new releases, review complaint themes and sudden 1★ spikes.

Why: a resume-worthy project that proves scheduled data pipelines, LLM summarisation at scale, fan-out notifications and cost-aware architecture, with real users and real numbers.

Constraints:
- Free for users. No paid tier.
- Near-zero running cost. Only the Claude API is paid; everything else stays on free tiers.
- Must scale to ~1,000 users × ~9 apps without a cost or rate-limit cliff.
- Store only what is useful. Raw reviews are processed and discarded.
- Store minimal user data: chat ID, timezone, settings, watchlist. No names or usernames.

Rejected approaches:
- **Resume-tailoring bot** — too many failure points in PDF generation, and little value beyond plain ChatGPT/Claude.
- **Per-user scraping and summarising** — cost grows with users. Work is done once per app and shared.
- **Storing raw reviews** — grows too fast for free DB tiers. Snapshots and summaries hold the long-term story.
- **Rendering cards on Cloudflare Workers** — the free plan allows 10 ms CPU per invocation, too little for PNG rendering.
- **Separate image and text messages** — doubles Telegram volume. One photo with caption per app.
- **Paid tier with faster checks** — the bot stays free.
- **"Play" in the name** — "Google Play" is a Google trademark.

## Terms

- **Bot** — the Telegram bot `@storeratingbot`.
- **User** — a Telegram chat that has sent `/start` to the Bot.
- **Admin** — Ayaz; identified by `ADMIN_CHAT_ID`.
- **App** — a Google Play listing identified by its package name (e.g. `com.whatsapp`).
- **Watchlist** — the set of Apps a User follows. Maximum 10 Apps.
- **Watcher** — a User who has a given App in their Watchlist.
- **Webhook Worker** — the Cloudflare Worker that receives Telegram updates and answers commands and button taps.
- **Daily Job** — the GitHub Actions workflow that fetches, summarises, renders and sends.
- **Fetch** — one scrape of an App's listing and new reviews.
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
- **Event** — one row in the `events` table describing a User action or delivery result.
- **Dashboard** — the Admin-only web page that shows metrics.

## Architecture

| Part | Runs on | Language |
|---|---|---|
| Webhook Worker | Cloudflare Workers (free) | TypeScript |
| Daily Job | GitHub Actions (cron) | Python 3.11+ |
| Database | Supabase free (Postgres) | SQL |
| Summaries | Claude Haiku 5.5, Batch API for scheduled work | — |
| Card render | Playwright (HTML/CSS + inline SVG chart) in Daily Job | Python |
| Dashboard | Cloudflare Pages + Cloudflare Access | Astro or HTML + Chart.js |

Data flow per day:
1. Daily Job does a Fetch for every App with at least one Watcher.
2. Daily Job writes a Snapshot and detects Alert Events by diffing against the previous Snapshot.
3. Daily Job filters new reviews (star split, junk removal, rank by thumbs-up and recency) and submits one Batch API request per App.
4. Daily Job renders one Card per changed App.
5. Daily Job sends the Card to the first Watcher, stores the returned Telegram `file_id`, and resends by `file_id` to every other Watcher.
6. Daily Job writes a `job_runs` row and sends the Admin an alert on failure.

## Data model

- `users` — `chat_id` (PK), `language_code`, `timezone`, `digest_time`, `paused`, `blocked`, `created_at`, `last_active_at`
- `watchlist` — `chat_id`, `package`, `paused`, `added_at` (PK `chat_id`, `package`)
- `apps` — `package` (PK), `name`, `developer`, `icon_url`, `first_seen_at`, `removed`
- `app_snapshots` — `package`, `date`, `version`, `rating`, `review_count`, `installs_tier`, `size`, `one_star_share` (PK `package`, `date`)
- `app_cursor` — `package` (PK), `last_review_id`, `last_review_at`
- `summaries` — `package`, `period` (`daily` | `monthly` | `quarterly`), `period_start`, `complaints` (JSON), `praise` (JSON), `verdict`
- `cards` — `package`, `period`, `period_start`, `telegram_file_id`
- `events` — `id`, `chat_id`, `event`, `props` (JSON), `ts`
- `job_runs` — `id`, `started_at`, `finished_at`, `apps_fetched`, `apps_failed`, `reviews_processed`, `claude_calls`, `claude_tokens_in`, `claude_tokens_out`, `cards_rendered`, `sent`, `failed`, `rate_limited`
- `errors` — `id`, `source` (`scraper` | `claude` | `render` | `telegram` | `worker`), `package`, `message`, `stack`, `ts`

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

**Admin**
- Admin alerts in Telegram: Daily Job failure, scraper health-check failure, Claude spend over threshold, block-rate spike.
- `/stats` (Admin only).
- Weekly Admin report Card every Monday.
- Dashboard: Overview, Retention, Funnel, Apps, Jobs, Errors.

## Steps

### Phase 0 — Setup
1. Set the Bot description with `/setdescription` in @BotFather.
2. Set the Bot about text with `/setabouttext` in @BotFather.
3. Set the Bot profile picture with `/setuserpic` in @BotFather.
4. Set the Bot command list with `/setcommands` in @BotFather: `add`, `apps`, `pause`, `resume`, `settings`, `delete`.
5. Send one message to the Bot from your Telegram account.
6. Read your `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
7. Create an API key in the Anthropic Console.
8. Set a monthly spend limit in the Anthropic Console.
9. Create a free Supabase project.
10. Copy the Supabase connection string.
11. Add the secrets `BOT_TOKEN`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, `ADMIN_CHAT_ID` to this repository.
12. Create a Cloudflare account.

### Phase 1 — Database
13. Write the migration file `db/001_init.sql` with every table in "Data model".
14. Apply `db/001_init.sql` to the Supabase project.

### Phase 2 — Webhook Worker
15. Create the Worker project in `worker/` with Wrangler.
16. Add `BOT_TOKEN`, `DATABASE_URL` and `ADMIN_CHAT_ID` as Worker secrets.
17. Implement the `/start` handler. Insert the User into `users`.
18. Implement the `/add` handler. Validate the package against the Play Store. Insert the row into `watchlist`. Insert the App into `apps`.
19. Implement the 10-App limit in the `/add` handler.
20. Implement the `/apps` handler with inline buttons.
21. Implement the Pause, Resume and Remove button callbacks with `editMessageText`.
22. Implement the `/pause` and `/resume` handlers.
23. Implement the `/settings` handler.
24. Implement the `/delete` handler.
25. Write an Event for every handler.
26. Write an `errors` row for every caught exception.
27. Deploy the Worker.
28. Set the Telegram webhook to the Worker URL with `setWebhook` and a secret token.

### Phase 3 — Fetch and Snapshots
29. Create the Python package in `job/`.
30. Implement the Fetch with `google-play-scraper` for one App.
31. Write the Snapshot to `app_snapshots`.
32. Update `app_cursor` with the newest review.
33. Implement the scraper health check: Fetch `com.whatsapp` and alert the Admin when required fields are empty.

### Phase 4 — Alert Events
34. Implement the diff between today's Snapshot and the previous Snapshot.
35. Detect each Alert Event type.
36. Send Alerts to every Watcher without App Pause or Global Pause.

### Phase 5 — Summaries
37. Implement the review filter: star split, junk removal, rank by thumbs-up and recency, cap at 150 reviews per side.
38. Write the Summary prompt. Return JSON with themes, shares, one short example each and a one-line verdict.
39. Submit all Apps as one Batch API request.
40. Poll the batch until it ends.
41. Fall back to a standard API call for every App without a result at 10:45 local.
42. Write each Summary to `summaries`.
43. Log tokens in `job_runs`.

### Phase 6 — Cards
44. Move the Card HTML template to `job/card/`.
45. Render one Card per changed App with Playwright.
46. Write the caption template. Keep it under 1,024 characters.

### Phase 7 — Sending
47. Implement the send queue with a pace of 25 messages per second overall and 1 per second per chat.
48. Retry on HTTP 429 after `retry_after` seconds.
49. Set `users.blocked = true` on HTTP 403.
50. Send the Card to the first Watcher with `sendPhoto`. Store the returned `file_id` in `cards`.
51. Send the stored `file_id` to every other Watcher.
52. Set `disable_notification` on every Update after the first per User per run.
53. Send Updates per User at their Digest Time.

### Phase 8 — On-demand Runs
54. Add the table `on_demand` with `chat_id`, `package`, `requested_at`, `status`.
55. In the `/add` handler, look up today's Card for the App in `cards`.
56. Send the stored `file_id` to the User when today's Card exists.
57. Reply with basic listing data when today's Card does not exist.
58. Count the User's On-demand Run Apps for today in `on_demand`.
59. Skip the On-demand Run when the count is at the New-App Quota. Reply that the first Update arrives at the next Digest Time.
60. Count all On-demand Run Apps for today in `on_demand`.
61. Skip the On-demand Run when the count is at the On-demand Budget. Send an Admin alert once per day.
62. Insert a pending row into `on_demand`.
63. Start the On-demand Run with `repository_dispatch` 60 seconds after the last pending row for that User.
64. Write `.github/workflows/on-demand.yml` triggered by `repository_dispatch`.
65. In the On-demand Run, Fetch every pending App, make one standard Claude call per App, render the Card, send it, and store the `file_id` in `cards`.
66. Set the `on_demand` rows to `done` or `failed`.
67. Count add and remove actions per User in the last hour from `events`.
68. Reject the action and reply "Too many changes, try again in a few minutes" when the count is at the Action Limit.
69. Write an Event `action_limited` for every rejected action.
70. Show Users with 3 or more `action_limited` Events in one day on the Dashboard.
71. In the Daily Job, select only Apps with at least one row in `watchlist`.

### Phase 9 — Schedule
72. Write `.github/workflows/daily.yml` with a cron for Fetch and Batch submission at 05:00 IST.
73. Write the send workflow with a cron every 30 minutes for Digest Times.
74. Write the Monthly and Quarterly Report workflows.
75. Write a `job_runs` row at the end of every workflow.
76. Send an Admin alert when a workflow fails.

### Phase 10 — Admin and Dashboard
77. Implement the `/stats` handler for the Admin only.
78. Write the stats builder that writes `stats.json`: DAU (active and reached), new Users, D1/D7/D30 retention, funnel, block rate, top Apps, failed `/add` reasons, job runs, errors.
79. Build the Dashboard pages in `dashboard/`.
80. Deploy the Dashboard to Cloudflare Pages.
81. Protect the Dashboard with Cloudflare Access for the Admin email only.
82. Implement the weekly Admin report Card.

### Phase 11 — Launch
83. Write the privacy note: "We store your chat ID, timezone and watchlist. Nothing else. /delete removes it all."
84. Add the privacy note to the Bot description and the README.
85. Test every command with a second Telegram account.
86. Announce the Bot on LinkedIn and Inside Android.

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
- `job_runs` has one row per workflow run with non-zero `apps_fetched`.
- Claude spend for one day with all Apps stays under the estimate shown in `job_runs` tokens × Haiku 5.5 batch rates.
- The Dashboard loads for the Admin email and returns an Access login page for any other email.
- Adding an App that already has today's Card returns that Card within 2 seconds, and no `on_demand` row is created.
- Adding an App with no Card today returns basic listing data within 2 seconds and the Card within 5 minutes.
- Adding 5 new Apps within 60 seconds starts exactly one On-demand Run.
- The 6th new-App add in one day from one User creates no `on_demand` row.
- The 21st add or remove action within one hour from one User changes nothing and writes an `action_limited` Event.
- Removing and re-adding the same App on the same day creates no new `on_demand` row.
- Every GitHub Actions workflow shows green for 7 consecutive days.
