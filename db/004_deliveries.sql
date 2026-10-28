-- Deliveries: one row per Update or Alert sent to a User, so nothing is sent twice
-- and "DAU reached" can be counted. Kept 30 days.

begin;

create table deliveries (
  id            bigint generated always as identity primary key,
  chat_id       bigint not null,
  package       text not null references apps (package),
  kind          text not null check (kind in ('daily', 'monthly', 'quarterly', 'alert')),
  ref           text not null,          -- period_start for Updates, alert id for Alerts
  status        text not null check (status in ('sent', 'failed', 'blocked')),
  sent_at       timestamptz not null default now(),
  unique (chat_id, package, kind, ref)
);
create index deliveries_sent_idx on deliveries (sent_at);

alter table deliveries enable row level security;

commit;
