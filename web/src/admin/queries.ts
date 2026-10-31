// Dashboard data. History comes from daily_stats; today's numbers are live.
// Every query runs as the App Role over the server-side connection only.

import type { Sql } from '../bot/repo';

export type Section = 'overview' | 'retention' | 'funnel' | 'apps' | 'jobs' | 'errors' | 'budget' | 'limits';
export const SECTIONS: Section[] = ['overview', 'retention', 'funnel', 'apps', 'jobs', 'errors', 'budget', 'limits'];

const num = (v: unknown) => (v == null ? 0 : Number(v));

export async function overview(sql: Sql) {
  const [live] = await sql`
    select (select count(*) from users)::int as users,
           (select count(*) from users where created_at >= now() - interval '1 day')::int as new_24h,
           (select count(distinct chat_id) from events where ts >= now() - interval '1 day'
              and event not in ('update_sent','alert_sent','bot_blocked','bot_unblocked','admin_denied'))::int as active_24h,
           (select count(distinct chat_id) from events where ts >= now() - interval '1 day'
              and event in ('update_sent','alert_sent'))::int as reached_24h,
           (select count(*) from users where blocked)::int as blocked,
           (select count(distinct package) from watchlist)::int as apps,
           (select count(distinct package) from watchlist where package like 'ios:%')::int as apps_ios,
           (select coalesce(sum(cost_usd),0)::float from spend where ts >= date_trunc('day', now())) as spend_today`;
  const history = await sql`
    select date::text as date, dau_active, dau_reached, users_new, users_total, blocked_new, updates_sent
    from daily_stats where date >= current_date - 30 order by date`;
  const blockRate = live.users ? num(live.blocked) / num(live.users) : 0;
  return { live: { ...live, block_rate: Math.round(blockRate * 1000) / 10 }, history };
}

export async function retention(sql: Sql) {
  return sql`select date::text as date, d1_retention, d7_retention, d30_retention, users_new
             from daily_stats where date >= current_date - 60 order by date desc`;
}

export async function funnel(sql: Sql) {
  const [f] = await sql`
    with cohort as (select chat_id, created_at, blocked, paused from users
                    where created_at >= now() - interval '30 days')
    select count(*)::int as started,
           count(*) filter (where exists (select 1 from watchlist w where w.chat_id = c.chat_id)
                            or exists (select 1 from events e where e.chat_id = c.chat_id and e.event = 'add_app'))::int as added_app,
           count(*) filter (where exists (select 1 from events e where e.chat_id = c.chat_id
                                          and e.event = 'update_sent'))::int as got_update,
           count(*) filter (where created_at <= now() - interval '7 days')::int as old_enough,
           count(*) filter (where created_at <= now() - interval '7 days' and not blocked and not paused)::int as day7
    from cohort c`;
  return f;
}

export async function apps(sql: Sql) {
  const top = await sql`
    select w.package, coalesce(a.name, w.package) as name, count(*)::int as watchers,
           case when w.package like 'ios:%' then 'App Store' else 'Google Play' end as store,
           upper(coalesce(substring(w.package from '^(?:play|ios):([a-z]{2}):'), 'us')) as country,
           (select rating::float from app_snapshots s where s.package = w.package order by date desc limit 1) as rating
    from watchlist w join apps a using (package) group by w.package, a.name order by watchers desc limit 20`;
  const failedAdds = await sql`
    select coalesce(props->>'reason', 'unknown') as reason, count(*)::int as n
    from events where event = 'add_app_failed' and ts >= now() - interval '30 days' group by 1 order by n desc`;
  const sourceErrors = await sql`
    select coalesce(package, '(none)') as package, count(*)::int as n, max(ts)::text as last
    from errors where source = 'store_source' and ts >= now() - interval '7 days' group by 1 order by n desc limit 20`;
  return { top, failedAdds, sourceErrors };
}

export async function jobs(sql: Sql) {
  return sql`
    select workflow, started_at::text, round(extract(epoch from (finished_at - started_at)))::int as seconds,
           success, apps_fetched, apps_failed, reviews_processed, claude_calls, cards_rendered, sent, failed, rate_limited
    from job_runs order by started_at desc limit 40`;
}

export async function errors(sql: Sql) {
  return sql`select source, package, message, stack, ts::text from errors order by ts desc limit 50`;
}

export async function budgetSection(sql: Sql) {
  const [b] = await sql`
    select (select value from config where key = 'monthly_budget_usd')::float as budget,
           coalesce(sum(cost_usd), 0)::float as spent
    from spend where ts >= date_trunc('month', now())`;
  const byDay = await sql`
    select date_trunc('day', ts)::date::text as day, sum(cost_usd)::float as usd
    from spend where ts >= date_trunc('month', now()) group by 1 order by 1`;
  const bySource = await sql`
    select source, batch, sum(cost_usd)::float as usd, sum(tokens_in)::bigint as tokens_in, sum(tokens_out)::bigint as tokens_out
    from spend where ts >= date_trunc('month', now()) group by 1, 2 order by usd desc`;
  const byApp = await sql`
    select coalesce(package, '(none)') as package, sum(cost_usd)::float as usd
    from spend where ts >= date_trunc('month', now()) group by 1 order by usd desc limit 15`;
  const [users] = await sql`select count(distinct chat_id)::int as n from events
                            where ts >= date_trunc('month', now()) and event in ('update_sent','alert_sent')`;
  const flagged = await sql`
    select chat_id, date_trunc('day', ts)::date::text as day, count(*)::int as hits
    from events where event = 'action_limited' and ts >= now() - interval '7 days'
    group by 1, 2 having count(*) >= 3 order by day desc`;
  const now = new Date();
  const days = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 0)).getUTCDate();
  const projected = (num(b.spent) / now.getUTCDate()) * days;
  return {
    budget: num(b.budget), spent: num(b.spent), projected, byDay, bySource, byApp, flagged,
    costPerUser: users.n ? num(b.spent) / users.n : null,
  };
}

export async function limits(sql: Sql) {
  const platform = await sql`
    select provider, resource, used::float as used, "limit"::float as limit, unit, source, date::text as date
    from platform_usage where date = (select max(date) from platform_usage) order by provider, resource`;
  const workflows = await sql`
    select workflow, max(finished_at) filter (where success)::text as last_success,
           count(*) filter (where success = false and started_at >= now() - interval '7 days')::int as failures_7d
    from job_runs group by workflow order by workflow`;
  const b = await budgetSection(sql);
  return { platform, workflows, claude: { used: b.spent, limit: b.budget, projected: b.projected } };
}

export async function section(sql: Sql, s: Section) {
  switch (s) {
    case 'overview': return overview(sql);
    case 'retention': return retention(sql);
    case 'funnel': return funnel(sql);
    case 'apps': return apps(sql);
    case 'jobs': return jobs(sql);
    case 'errors': return errors(sql);
    case 'budget': return budgetSection(sql);
    case 'limits': return limits(sql);
  }
}
