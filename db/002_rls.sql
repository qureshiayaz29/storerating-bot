-- RLS Lockdown and App Role. See PLAN.md "Terms" → RLS Lockdown, App Role.
--
-- After applying: set the App Role password in the Supabase dashboard
-- (Database → Roles) and build DATABASE_URL with user storerating_app.

begin;

-- 1. Row Level Security on every table in public, no policies.
do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', t.tablename);
  end loop;
end $$;

-- 2. Supabase API roles get nothing, now and for future objects.
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;
revoke usage on schema public from anon, authenticated;

alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated;

-- 3. App Role: the only role the bot, the daily job and the Stats API use.
do $$
begin
  if not exists (select from pg_roles where rolname = 'storerating_app') then
    create role storerating_app login bypassrls;
  end if;
end $$;

grant usage on schema public to storerating_app;
grant select, insert, update, delete on all tables in schema public to storerating_app;
grant usage, select on all sequences in schema public to storerating_app;

alter default privileges in schema public grant select, insert, update, delete on tables to storerating_app;
alter default privileges in schema public grant usage, select on sequences to storerating_app;

commit;
