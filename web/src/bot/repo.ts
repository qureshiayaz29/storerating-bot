// Postgres implementation of Repo. Connects as the App Role through the
// Supabase transaction pooler, so prepared statements are off.

import postgres from 'postgres';
import type { AppRow, BudgetState, Repo, Stats, UserRow, WatchRow } from './types';

export type Sql = postgres.Sql;

let shared: Sql | null = null;

/** One small connection pool per function instance, reused across requests. */
export function sqlFor(databaseUrl: string): Sql {
  shared ??= postgres(databaseUrl, { prepare: false, max: 1, idle_timeout: 20, connect_timeout: 10 });
  return shared;
}

export class PgRepo implements Repo {
  constructor(private readonly sql: Sql) {}

  async touchUser(chatId: number, languageCode?: string) {
    const rows = await this.sql<{ created: boolean }[]>`
      insert into users (chat_id, language_code) values (${chatId}, ${languageCode ?? null})
      on conflict (chat_id) do update
        set last_active_at = now(), blocked = false,
            language_code = coalesce(excluded.language_code, users.language_code)
      returning (xmax = 0) as created`;
    return { created: rows[0]?.created ?? false };
  }

  async getUser(chatId: number): Promise<UserRow | null> {
    const rows = await this.sql<UserRow[]>`
      select chat_id, timezone, to_char(digest_time, 'HH24:MI') as digest_time, paused
      from users where chat_id = ${chatId}`;
    return rows[0] ?? null;
  }

  async setPaused(chatId: number, paused: boolean) {
    await this.sql`update users set paused = ${paused} where chat_id = ${chatId}`;
  }

  async setBlocked(chatId: number, blocked: boolean) {
    await this.sql`update users set blocked = ${blocked} where chat_id = ${chatId}`;
  }

  async setDigest(chatId: number, fields: { timezone?: string; digest_time?: string }) {
    if (fields.timezone) await this.sql`update users set timezone = ${fields.timezone} where chat_id = ${chatId}`;
    if (fields.digest_time) await this.sql`update users set digest_time = ${fields.digest_time}::time where chat_id = ${chatId}`;
  }

  async deleteUser(chatId: number) {
    await this.sql.begin(async (tx) => {
      await tx`delete from events where chat_id = ${chatId}`;
      await tx`delete from users where chat_id = ${chatId}`; // cascades to watchlist and on_demand
    });
  }

  async getConfig(key: string) {
    const rows = await this.sql<{ value: string }[]>`select value from config where key = ${key}`;
    return rows[0]?.value ?? null;
  }

  async setConfig(key: string, value: string) {
    await this.sql`
      insert into config (key, value) values (${key}, ${value})
      on conflict (key) do update set value = excluded.value, updated_at = now()`;
  }

  async getApp(pkg: string): Promise<AppRow | null> {
    const rows = await this.sql<AppRow[]>`select package, name from apps where package = ${pkg} and not removed`;
    return rows[0] ?? null;
  }

  async upsertApp(app: { package: string; name: string; developer: string | null; icon_url: string | null }) {
    await this.sql`
      insert into apps (package, name, developer, icon_url)
      values (${app.package}, ${app.name}, ${app.developer}, ${app.icon_url})
      on conflict (package) do update
        set name = excluded.name, developer = excluded.developer, icon_url = excluded.icon_url, removed = false`;
  }

  async watchlist(chatId: number): Promise<WatchRow[]> {
    return this.sql<WatchRow[]>`
      select w.package, a.name, w.paused
      from watchlist w join apps a using (package)
      where w.chat_id = ${chatId}
      order by w.added_at`;
  }

  async addWatch(chatId: number, pkg: string) {
    const rows = await this.sql`
      insert into watchlist (chat_id, package) values (${chatId}, ${pkg})
      on conflict do nothing returning 1`;
    return rows.length > 0;
  }

  async removeWatch(chatId: number, pkg: string) {
    await this.sql`delete from watchlist where chat_id = ${chatId} and package = ${pkg}`;
  }

  async setWatchPaused(chatId: number, pkg: string, paused: boolean) {
    await this.sql`update watchlist set paused = ${paused} where chat_id = ${chatId} and package = ${pkg}`;
  }

  async countEventsSince(chatId: number, events: string[], since: Date) {
    const rows = await this.sql<{ n: number }[]>`
      select count(*)::int as n from events
      where chat_id = ${chatId} and event = any(${events}) and ts >= ${since}`;
    return rows[0]?.n ?? 0;
  }

  async logEvent(chatId: number | null, event: string, props: Record<string, unknown> = {}) {
    await this.sql`insert into events (chat_id, event, props) values (${chatId}, ${event}, ${this.sql.json(props as postgres.JSONValue)})`;
  }

  async logError(source: 'store_source' | 'telegram' | 'web', pkg: string | null, message: string, stack?: string) {
    await this.sql`
      insert into errors (source, package, message, stack)
      values (${source}, ${pkg}, ${message.slice(0, 2000)}, ${stack?.slice(0, 8000) ?? null})`;
  }

  async budget(now: Date): Promise<BudgetState> {
    const monthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
    const [b] = await this.sql<{ budget: string | null; spent: string }[]>`
      select (select value from config where key = 'monthly_budget_usd') as budget,
             coalesce(sum(cost_usd), 0)::text as spent
      from spend where ts >= ${monthStart}`;
    return { monthlyBudgetUsd: Number(b?.budget ?? 30), spentUsd: Number(b?.spent ?? 0) };
  }

  async stats(now: Date): Promise<Stats> {
    const dayStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    const [s] = await this.sql<Stats[]>`
      select
        (select count(*)::int from users) as users,
        (select count(*)::int from users where last_active_at >= ${dayStart}) as "activeToday",
        (select count(distinct package)::int from watchlist) as "watchedApps",
        (select count(*)::int from users where blocked) as blocked`;
    return s;
  }
}

/** Self-Metering: one row per route per day in fn_metrics. */
export async function meter(sql: Sql, route: string, durationMs: number, bytes: number) {
  await sql`
    insert into fn_metrics (date, route, invocations, duration_ms_total, response_bytes_total)
    values (current_date, ${route}, 1, ${Math.round(durationMs)}, ${bytes})
    on conflict (date, route) do update set
      invocations = fn_metrics.invocations + 1,
      duration_ms_total = fn_metrics.duration_ms_total + excluded.duration_ms_total,
      response_bytes_total = fn_metrics.response_bytes_total + excluded.response_bytes_total`;
}
