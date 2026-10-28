// Runs every dashboard query and the bot's repo against a real Postgres with
// all migrations applied. Skipped when TEST_DATABASE_URL is not set.

import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import postgres from 'postgres';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { section, SECTIONS } from '../src/admin/queries';
import { PgRepo } from '../src/bot/repo';

const base = process.env.TEST_DATABASE_URL;
const name = `srweb_${Math.random().toString(36).slice(2, 10)}`;
let sql: postgres.Sql;

describe.skipIf(!base)('dashboard queries and repo on Postgres', () => {
  beforeAll(async () => {
    const admin = postgres(base!, { max: 1 });
    await admin.unsafe(`create database ${name}`);
    for (const r of ['anon', 'authenticated']) {
      await admin.unsafe(`do $$ begin create role ${r} nologin; exception when duplicate_object then null; end $$`);
    }
    await admin.end();
    const u = new URL(base!.replace(/^postgresql:/, 'http:'));
    u.pathname = `/${name}`;
    sql = postgres(u.toString().replace(/^http:/, 'postgresql:'), { max: 1, onnotice: () => {} });
    const dir = resolve(__dirname, '../../db');
    for (const f of readdirSync(dir).filter((f) => f.endsWith('.sql')).sort()) {
      await sql.unsafe(readFileSync(resolve(dir, f), 'utf8'));
    }
    await sql`insert into users (chat_id) values (1)`;
    await sql`insert into apps (package, name) values ('a.b', 'App')`;
    await sql`insert into watchlist (chat_id, package) values (1, 'a.b')`;
    await sql`insert into events (chat_id, event, props) values (1, 'add_app_failed', ${sql.json({ reason: 'cap' })})`;
    await sql`insert into spend (source, model, cost_usd) values ('daily', 'm', 0.5)`;
    await sql`insert into job_runs (workflow, finished_at, success) values ('daily', now(), true)`;
    await sql`insert into platform_usage (date, provider, resource, used, "limit", unit, source)
              values (current_date, 'vercel', 'invocations', 10, 1000000, 'invocations', 'self_metered')`;
  });

  afterAll(async () => {
    await sql?.end();
    const admin = postgres(base!, { max: 1 });
    await admin.unsafe(`drop database if exists ${name} with (force)`);
    await admin.end();
  });

  for (const s of SECTIONS) {
    it(`section ${s} runs`, async () => {
      expect(await section(sql, s)).toBeTruthy();
    });
  }

  it('repo methods run', async () => {
    const repo = new PgRepo(sql);
    expect((await repo.touchUser(2, 'en')).created).toBe(true);
    expect((await repo.touchUser(2)).created).toBe(false);
    expect(await repo.watchlist(1)).toEqual([{ package: 'a.b', name: 'App', paused: false }]);
    expect(await repo.countEventsSince(1, ['add_app'], new Date(0))).toBe(0);
    await repo.logEvent(1, 'add_app', { package: 'a.b' });
    expect(await repo.countEventsSince(1, ['add_app'], new Date(0))).toBe(1);
    expect((await repo.budget(new Date())).spentUsd).toBe(0.5);
    expect((await repo.stats(new Date())).users).toBe(2);
    expect(await repo.latestCard('a.b', new Date(0))).toBeNull();
    await repo.insertOnDemand(1, 'a.b');
    expect(await repo.countOnDemand(new Date(0), 1)).toBe(1);
    expect(await repo.hasPendingOnDemand(1, new Date(0))).toBe(true);
    await repo.recordDelivery(1, 'a.b', 'daily', '2026-10-29', 'sent');
    expect(await repo.platformUsage()).toHaveLength(1);
    await repo.setDigest(1, { digest_time: '09:30', timezone: 'Europe/London' });
    expect(await repo.getUser(1)).toMatchObject({ digest_time: '09:30', timezone: 'Europe/London' });
    await repo.deleteUser(1);
    expect(await repo.getUser(1)).toBeNull();
    expect(await repo.countEventsSince(1, ['add_app'], new Date(0))).toBe(0);
  });
});
