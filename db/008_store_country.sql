-- Per-app country and App Store support. See docs/plans/01-app-store.md.
--
-- App Keys become `play:<country>:<package>` or `ios:<country>:<track id>`.
-- Existing Apps were fetched from the US Play Store, so they become `play:us:<package>`.

begin;

alter table users
  add column timezone_set  boolean not null default false,
  add column saved_country text check (saved_country ~ '^[a-z]{2}$'),
  add column pending       jsonb;

-- Anyone not on the default timezone picked theirs in /settings.
update users set timezone_set = true where timezone <> 'Asia/Kolkata';

-- Every foreign key to apps(package) follows a key rename.
do $$
declare r record;
begin
  for r in select conrelid::regclass as tbl, conname, pg_get_constraintdef(oid) as def
           from pg_constraint where contype = 'f' and confrelid = 'apps'::regclass
  loop
    execute format('alter table %s drop constraint %I', r.tbl, r.conname);
    execute format('alter table %s add constraint %I %s on update cascade', r.tbl, r.conname, r.def);
  end loop;
end $$;

update apps set package = 'play:us:' || package where package !~ '^(play|ios):';
update spend set package = 'play:us:' || package where package is not null and package !~ '^(play|ios):';
update errors set package = 'play:us:' || package where package is not null and package !~ '^(play|ios):';

alter table apps
  add constraint apps_store_known check (store in ('google_play', 'app_store')),
  add constraint apps_key_matches_store check ((store = 'app_store') = (package like 'ios:%'));

insert into config (key, value) values ('availability_countries', 'us,gb,in') on conflict (key) do nothing;

commit;
