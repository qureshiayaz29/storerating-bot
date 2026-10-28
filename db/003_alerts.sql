-- Alert Event support. See PLAN.md → Alert Event.

begin;

-- Release detection for apps that report "Varies with device", and the
-- per-day review numbers used for 1★ spike detection.
alter table app_snapshots
  add column updated_at          timestamptz,
  add column release_notes       text,
  add column new_reviews         integer,
  add column new_one_star_share  numeric(5, 4);

-- One row per detected Alert Event. dedupe_key stops the same event firing
-- twice (e.g. the same version, or a rating drop already reported this week).
create table alerts (
  id           bigint generated always as identity primary key,
  package      text not null references apps (package),
  type         text not null check (type in ('new_version', 'rating_drop', 'one_star_spike', 'app_removed')),
  dedupe_key   text not null,
  detail       jsonb not null default '{}',
  detected_at  timestamptz not null default now(),
  sent_at      timestamptz,
  unique (package, type, dedupe_key)
);
create index alerts_detected_idx on alerts (detected_at);

alter table alerts enable row level security;

commit;
