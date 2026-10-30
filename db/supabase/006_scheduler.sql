-- Scheduler: Supabase pg_cron starts the GitHub workflows on time.
-- GitHub's own `schedule` trigger ran */30 jobs only every 4–6 hours and the
-- 05:00 daily job ~3 hours late, so it stays in the workflow files as a backup only.
--
-- Supabase-only (pg_cron, pg_net, Vault), so it lives outside db/*.sql, which
-- tests apply to plain Postgres.
--
-- Needs one Vault secret, added by hand in the SQL editor (never committed):
--   select vault.create_secret('<GITHUB_DISPATCH_TOKEN>', 'github_dispatch_token');
-- The token needs Actions read and write on this repository.

create extension if not exists pg_cron;
create extension if not exists pg_net;

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create or replace function private.dispatch_workflow(workflow text) returns bigint
language plpgsql security definer set search_path = '' as $$
declare
  token text;
begin
  select decrypted_secret into token from vault.decrypted_secrets where name = 'github_dispatch_token';
  if token is null then
    raise warning 'Vault secret github_dispatch_token is missing';
    return null;
  end if;
  return net.http_post(
    url := 'https://api.github.com/repos/qureshiayaz29/storerating-bot/actions/workflows/' || workflow || '/dispatches',
    body := '{"ref": "main"}'::jsonb,
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || token,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'storerating-scheduler',
      'Content-Type', 'application/json'));
end $$;

revoke execute on function private.dispatch_workflow(text) from public, anon, authenticated;

-- pg_cron runs in UTC.
select cron.schedule('storerating-daily', '30 23 * * *', $$select private.dispatch_workflow('daily.yml')$$);   -- 05:00 IST
select cron.schedule('storerating-deliver', '*/15 * * * *', $$select private.dispatch_workflow('deliver.yml')$$);
