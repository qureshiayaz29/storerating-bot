-- StoreRating schema. See PLAN.md "Data model".

begin;

create table users (
  chat_id         bigint primary key,
  language_code   text,
  timezone        text not null default 'Asia/Kolkata',
  digest_time     time not null default '11:00',
  paused          boolean not null default false,
  blocked         boolean not null default false,
  created_at      timestamptz not null default now(),
  last_active_at  timestamptz not null default now()
);

create table apps (
  package         text primary key,
  store           text not null default 'google_play',
  name            text,
  developer       text,
  icon_url        text,
  first_seen_at   timestamptz not null default now(),
  removed         boolean not null default false
);

create table watchlist (
  chat_id   bigint not null references users (chat_id) on delete cascade,
  package   text   not null references apps (package),
  paused    boolean not null default false,
  added_at  timestamptz not null default now(),
  primary key (chat_id, package)
);
create index watchlist_package_idx on watchlist (package);

create table app_snapshots (
  package          text not null references apps (package),
  date             date not null,
  source_adapter   text not null,
  version          text,
  rating           numeric(3, 2),
  review_count     bigint,
  installs_tier    text,
  size             text,
  one_star_share   numeric(5, 4),
  primary key (package, date)
);

create table app_cursor (
  package          text primary key references apps (package),
  last_review_id   text,
  last_review_at   timestamptz
);

create table summaries (
  package       text not null references apps (package),
  period        text not null check (period in ('daily', 'monthly', 'quarterly')),
  period_start  date not null,
  complaints    jsonb not null default '[]',
  praise        jsonb not null default '[]',
  verdict       text,
  created_at    timestamptz not null default now(),
  primary key (package, period, period_start)
);

create table cards (
  package           text not null references apps (package),
  period            text not null check (period in ('daily', 'monthly', 'quarterly')),
  period_start      date not null,
  telegram_file_id  text,
  created_at        timestamptz not null default now(),
  primary key (package, period, period_start)
);

create table on_demand (
  id            bigint generated always as identity primary key,
  chat_id       bigint not null references users (chat_id) on delete cascade,
  package       text   not null references apps (package),
  requested_at  timestamptz not null default now(),
  status        text not null default 'pending' check (status in ('pending', 'done', 'failed'))
);
create index on_demand_requested_idx on on_demand (requested_at);

create table spend (
  id           bigint generated always as identity primary key,
  ts           timestamptz not null default now(),
  source       text not null check (source in ('daily', 'on_demand', 'report', 'alert')),
  package      text,
  model        text not null,
  batch        boolean not null default false,
  tokens_in    integer not null default 0,
  tokens_out   integer not null default 0,
  cost_usd     numeric(12, 6) not null default 0
);
create index spend_ts_idx on spend (ts);

create table config (
  key         text primary key,
  value       text not null,
  updated_at  timestamptz not null default now()
);

create table daily_stats (
  date              date primary key,
  users_total       integer,
  users_new         integer,
  dau_active        integer,
  dau_reached       integer,
  blocked_total     integer,
  blocked_new       integer,
  apps_watched      integer,
  updates_sent      integer,
  alerts_sent       integer,
  claude_cost_usd   numeric(12, 6),
  d1_retention      numeric(5, 4),
  d7_retention      numeric(5, 4),
  d30_retention     numeric(5, 4)
);

create table platform_usage (
  date       date not null,
  provider   text not null check (provider in ('vercel', 'github', 'supabase')),
  resource   text not null,
  used       numeric not null,
  "limit"    numeric,
  unit       text not null,
  source     text not null check (source in ('api', 'self_metered')),
  primary key (date, provider, resource)
);

create table fn_metrics (
  date                   date not null,
  route                  text not null,
  invocations            bigint not null default 0,
  duration_ms_total      bigint not null default 0,
  response_bytes_total   bigint not null default 0,
  primary key (date, route)
);

-- chat_id has no foreign key: events outlive /delete only until the
-- delete handler removes them, and admin_denied events may come from
-- chats that never sent /start.
create table events (
  id       bigint generated always as identity primary key,
  chat_id  bigint,
  event    text not null,
  props    jsonb not null default '{}',
  ts       timestamptz not null default now()
);
create index events_ts_idx on events (ts);
create index events_chat_ts_idx on events (chat_id, ts);

create table job_runs (
  id                   bigint generated always as identity primary key,
  workflow             text not null,
  started_at           timestamptz not null default now(),
  finished_at          timestamptz,
  success              boolean,
  apps_fetched         integer not null default 0,
  apps_failed          integer not null default 0,
  reviews_processed    integer not null default 0,
  claude_calls         integer not null default 0,
  claude_tokens_in     bigint  not null default 0,
  claude_tokens_out    bigint  not null default 0,
  cards_rendered       integer not null default 0,
  sent                 integer not null default 0,
  failed               integer not null default 0,
  rate_limited         integer not null default 0
);

create table errors (
  id       bigint generated always as identity primary key,
  source   text not null check (source in ('store_source', 'claude', 'render', 'telegram', 'web')),
  package  text,
  message  text not null,
  stack    text,
  ts       timestamptz not null default now()
);
create index errors_ts_idx on errors (ts);

-- Defaults. Monthly budget is changed at runtime with /setbudget.
insert into config (key, value) values
  ('monthly_budget_usd',              '30'),
  ('vercel_limit_invocations',        '1000000'),
  ('vercel_limit_active_cpu_hours',   '4'),
  ('vercel_limit_data_transfer_gb',   '100'),
  ('new_app_quota_per_user_per_day',  '5'),
  ('on_demand_budget_per_day',        '200'),
  ('action_limit_per_hour',           '20'),
  ('watchlist_max_apps',              '10');

commit;
