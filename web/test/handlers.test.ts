import { beforeEach, describe, expect, it } from 'vitest';
import { handleUpdate, isAdmin, isTimeZone, pkgKey, TEXT, type Deps } from '../src/bot/handlers';
import { SourceError } from '../src/sources/contract';
import type { TgUpdate } from '../src/bot/types';
import { FakeLookup, FakeRepo, FakeTelegram, listingFor } from './fakes';

const USER = 111;
const OTHER = 222;
const ADMIN = 999;
let uid = 0;

function msg(text: string, from = USER, chatType: 'private' | 'group' = 'private'): TgUpdate {
  return { update_id: ++uid, message: { message_id: uid, from: { id: from }, chat: { id: chatType === 'private' ? from : -100, type: chatType }, text } };
}
function tap(data: string, from = USER): TgUpdate {
  return {
    update_id: ++uid,
    callback_query: { id: `cb${uid}`, from: { id: from }, data, message: { message_id: 1, chat: { id: from, type: 'private' } } },
  };
}

let repo: FakeRepo;
let tg: FakeTelegram;
let lookup: FakeLookup;
let deps: Deps;
const send = (u: TgUpdate) => handleUpdate(u, deps);

beforeEach(() => {
  repo = new FakeRepo();
  tg = new FakeTelegram();
  lookup = new FakeLookup();
  deps = { repo, tg, lookup, adminChatId: ADMIN, now: repo.now };
});

describe('/start', () => {
  it('creates the user and welcomes them', async () => {
    await send(msg('/start'));
    expect(repo.users.has(USER)).toBe(true);
    expect(tg.last().text).toContain('StoreRating');
    expect(repo.events[0]).toMatchObject({ event: 'start', props: { new: true } });
  });
});

describe('/add', () => {
  it('looks up a new app, stores it and adds it to the watchlist', async () => {
    await send(msg('/add https://play.google.com/store/apps/details?id=com.example.notes&hl=en'));
    expect(lookup.calls).toEqual(['com.example.notes']);
    expect(repo.apps.get('com.example.notes')?.name).toBe('App notes');
    expect(await repo.watchlist(USER)).toHaveLength(1);
    expect(tg.last().text).toContain('Watching <b>App notes</b>');
    expect(tg.last().text).toContain('4.3★');
    expect(repo.eventNames()).toContain('add_app');
  });

  it('accepts a pasted Play Store link without a command', async () => {
    await send(msg('https://play.google.com/store/apps/details?id=com.example.notes'));
    expect(await repo.watchlist(USER)).toHaveLength(1);
  });

  it('skips the store lookup when the app is already known', async () => {
    await send(msg('/add com.example.notes', OTHER));
    lookup.calls = [];
    await send(msg('/add com.example.notes'));
    expect(lookup.calls).toEqual([]);
    expect(await repo.watchlist(USER)).toHaveLength(1);
  });

  it('rejects invalid input', async () => {
    await send(msg('/add whatsapp'));
    expect(tg.last().text).toBe(TEXT.addInvalid);
    expect(repo.events.at(-1)).toMatchObject({ event: 'add_app_failed', props: { reason: 'invalid_input' } });
  });

  it('reports apps that are not on Google Play', async () => {
    lookup = new FakeLookup((p) => new SourceError('not_found', p));
    deps.lookup = lookup;
    await send(msg('/add com.example.gone'));
    expect(tg.last().text).toContain("Couldn't find");
    expect(await repo.watchlist(USER)).toHaveLength(0);
  });

  it('logs an error and apologises when the store is blocked', async () => {
    deps.lookup = new FakeLookup((p) => new SourceError('blocked', p, '403'));
    await send(msg('/add com.example.app'));
    expect(tg.last().text).toBe(TEXT.storeDown);
    expect(repo.errors[0]).toMatchObject({ source: 'store_source', pkg: 'com.example.app' });
  });

  it('does not add the same app twice', async () => {
    await send(msg('/add com.example.notes'));
    await send(msg('/add com.example.notes'));
    expect(await repo.watchlist(USER)).toHaveLength(1);
    expect(tg.last().text).toContain('already watching');
  });

  it('caps the watchlist at 10 and shows remove buttons on the 11th', async () => {
    for (let i = 1; i <= 10; i++) await send(msg(`/add com.example.app${i}`));
    await send(msg('/add com.example.app11'));
    expect(await repo.watchlist(USER)).toHaveLength(10);
    expect(tg.last().text).toBe(TEXT.limitReached(10));
    expect(tg.last().keyboard).toHaveLength(10);
    expect(tg.last().keyboard![0][0].callback_data).toBe(`x:${pkgKey('com.example.app1')}`);
  });

  it('enforces the Action Limit of 20 adds and removes per hour', async () => {
    repo.config.set('watchlist_max_apps', '100');
    for (let i = 1; i <= 20; i++) await send(msg(`/add com.example.app${i}`));
    await send(msg('/add com.example.app21'));
    expect(await repo.watchlist(USER)).toHaveLength(20);
    expect(tg.last().text).toBe(TEXT.tooMany);
    expect(repo.eventNames()).toContain('action_limited');
  });
});

describe('/apps and buttons', () => {
  beforeEach(async () => {
    await send(msg('/add com.example.notes'));
    await send(msg('/add com.example.maps'));
  });

  it('lists apps with pause and remove buttons', async () => {
    await send(msg('/apps'));
    const { text, keyboard } = tg.last();
    expect(text).toContain('Your apps (2/10)');
    expect(keyboard![0].map((b) => b.callback_data)).toEqual([`p:${pkgKey('com.example.notes')}`, `x:${pkgKey('com.example.notes')}`]);
    expect(keyboard!.at(-1)![0].callback_data).toBe('pa');
  });

  it('keeps every callback_data within 64 bytes, even for long package names', async () => {
    const long = `com.${'a'.repeat(140)}.app`;
    await send(msg(`/add ${long}`));
    await send(msg('/apps'));
    for (const row of tg.last().keyboard!) for (const b of row) expect(Buffer.byteLength(b.callback_data)).toBeLessThanOrEqual(64);
  });

  it('pauses and resumes one app', async () => {
    await send(tap(`p:${pkgKey('com.example.notes')}`));
    expect((await repo.watchlist(USER))[0].paused).toBe(true);
    expect(tg.last()).toMatchObject({ kind: 'edit' });
    await send(tap(`r:${pkgKey('com.example.notes')}`));
    expect((await repo.watchlist(USER))[0].paused).toBe(false);
  });

  it('asks before removing, then removes', async () => {
    await send(tap(`x:${pkgKey('com.example.maps')}`));
    expect(tg.last().text).toContain('Remove <b>App maps</b>');
    expect(await repo.watchlist(USER)).toHaveLength(2);
    await send(tap(`xy:${pkgKey('com.example.maps')}`));
    expect((await repo.watchlist(USER)).map((w) => w.package)).toEqual(['com.example.notes']);
    expect(repo.eventNames()).toContain('remove_app');
  });

  it('cancel keeps the app', async () => {
    await send(tap(`x:${pkgKey('com.example.maps')}`));
    await send(tap('xn'));
    expect(await repo.watchlist(USER)).toHaveLength(2);
  });

  it('pause all and resume all from buttons', async () => {
    await send(tap('pa'));
    expect(repo.users.get(USER)!.paused).toBe(true);
    await send(tap('ra'));
    expect(repo.users.get(USER)!.paused).toBe(false);
  });

  it('a stale button does nothing harmful', async () => {
    await send(tap(`xy:${pkgKey('com.example.unknown')}`));
    expect(await repo.watchlist(USER)).toHaveLength(2);
    expect(tg.sent.at(-1)).toMatchObject({ kind: 'answer', text: 'That app is no longer in your list' });
  });
});

describe('/pause, /resume, /settings', () => {
  it('pauses and resumes everything', async () => {
    await send(msg('/pause'));
    expect(repo.users.get(USER)!.paused).toBe(true);
    await send(msg('/resume'));
    expect(repo.users.get(USER)!.paused).toBe(false);
  });

  it('shows and changes settings', async () => {
    await send(msg('/settings'));
    expect(tg.last().text).toContain('Daily update: 11:00');
    await send(msg('/settings time 9:30'));
    expect(repo.users.get(USER)!.digest_time).toBe('09:30');
    await send(msg('/settings tz Europe/London'));
    expect(repo.users.get(USER)!.timezone).toBe('Europe/London');
    await send(msg('/settings tz Mars/Base'));
    expect(repo.users.get(USER)!.timezone).toBe('Europe/London');
    await send(msg('/settings time 25:00'));
    expect(repo.users.get(USER)!.digest_time).toBe('09:30');
  });

  it('validates timezones', () => {
    expect(isTimeZone('Asia/Kolkata')).toBe(true);
    expect(isTimeZone('UTC')).toBe(true);
    expect(isTimeZone('Nowhere/City')).toBe(false);
  });
});

describe('/delete', () => {
  it('leaves zero rows for the chat after confirming', async () => {
    await send(msg('/add com.example.notes'));
    await send(msg('/delete'));
    expect(repo.users.has(USER)).toBe(true);
    await send(tap('dy'));
    expect(repo.users.has(USER)).toBe(false);
    expect(await repo.watchlist(USER)).toHaveLength(0);
    expect(repo.events.filter((e) => e.chat_id === USER)).toHaveLength(0);
    expect(tg.last().text).toBe(TEXT.deleted);
  });

  it('cancel deletes nothing', async () => {
    await send(msg('/add com.example.notes'));
    await send(msg('/delete'));
    await send(tap('dn'));
    expect(await repo.watchlist(USER)).toHaveLength(1);
  });
});

describe('Admin Commands', () => {
  it('Admin Check needs the Admin sender in a private chat', () => {
    expect(isAdmin({ from: { id: ADMIN }, chat: { type: 'private' } }, ADMIN)).toBe(true);
    expect(isAdmin({ from: { id: ADMIN }, chat: { type: 'group' } }, ADMIN)).toBe(false);
    expect(isAdmin({ from: { id: USER }, chat: { type: 'private' } }, ADMIN)).toBe(false);
  });

  it.each(['/stats', '/budget', '/setbudget 40'])('%s from anyone else looks like an unknown command', async (cmd) => {
    await send(msg(cmd, USER));
    expect(tg.last().text).toBe(TEXT.help);
    expect(repo.events.at(-1)).toMatchObject({ event: 'admin_denied' });
    expect(repo.config.get('monthly_budget_usd')).toBe('30');
  });

  it('/stats from a group gets no Admin reply, even from the Admin', async () => {
    await send(msg('/stats', ADMIN, 'group'));
    expect(tg.last().text).toBe(TEXT.groupsOnly);
  });

  it('/stats works for the Admin', async () => {
    await send(msg('/stats', ADMIN));
    expect(tg.last().text).toContain('StoreRating stats');
  });

  it('/budget shows spend, projection and the top-up reminder', async () => {
    repo.spent = 21;
    await send(msg('/budget', ADMIN));
    const text = tg.last().text!;
    expect(text).toContain('Claude budget at 70%');
    expect(text).toContain('Spent $21.00 of $30.00');
    expect(text).toContain('/setbudget');
  });

  it('/setbudget 40 updates the budget and reminds about the Console limit', async () => {
    await send(msg('/setbudget 40', ADMIN));
    expect(repo.config.get('monthly_budget_usd')).toBe('40');
    expect(tg.last().text).toContain('Anthropic Console spend limit is at least $40.00');
    await send(msg('/setbudget lots', ADMIN));
    expect(repo.config.get('monthly_budget_usd')).toBe('40');
  });
});

describe('other updates', () => {
  it('unknown commands get help', async () => {
    await send(msg('/foo'));
    expect(tg.last().text).toBe(TEXT.help);
  });

  it('blocking and unblocking the bot is recorded', async () => {
    await send(msg('/start'));
    await send({ update_id: ++uid, my_chat_member: { chat: { id: USER, type: 'private' }, new_chat_member: { status: 'kicked' } } });
    expect(repo.users.get(USER)!.blocked).toBe(true);
    await send({ update_id: ++uid, my_chat_member: { chat: { id: USER, type: 'private' }, new_chat_member: { status: 'member' } } });
    expect(repo.users.get(USER)!.blocked).toBe(false);
    expect(repo.eventNames(USER)).toEqual(expect.arrayContaining(['bot_blocked', 'bot_unblocked']));
  });

  it('group chats get a private-chat notice and nothing is stored', async () => {
    await send(msg('/add com.example.notes', USER, 'group'));
    expect(tg.last().text).toBe(TEXT.groupsOnly);
    expect(repo.users.size).toBe(0);
  });

  it('a listing from the fake matches the contract', () => {
    expect(listingFor('com.example.app').store).toBe('google_play');
  });
});

describe('On-demand Runs after /add', () => {
  let dispatches = 0;
  beforeEach(() => {
    dispatches = 0;
    deps.dispatch = async () => {
      dispatches++;
    };
  });

  it("sends today's Card at once when it exists, with no run and no cost", async () => {
    repo.cards.set('com.example.notes', { file_id: 'F1', caption: 'cap', period_start: '2026-10-29', created_at: repo.now() });
    await send(msg('/add com.example.notes'));
    expect(tg.sent.find((s) => s.kind === 'photo')).toMatchObject({ chatId: USER, fileId: 'F1', text: 'cap' });
    expect(repo.deliveries).toEqual([{ chat_id: USER, package: 'com.example.notes', kind: 'daily', ref: '2026-10-29', status: 'sent' }]);
    expect(dispatches).toBe(0);
    expect(repo.onDemand).toHaveLength(0);
  });

  it('ignores a Card older than 30 hours', async () => {
    repo.cards.set('com.example.notes', { file_id: 'F1', caption: 'c', period_start: '2026-10-27', created_at: new Date('2026-10-27T00:00:00Z') });
    await send(msg('/add com.example.notes'));
    expect(tg.sent.some((s) => s.kind === 'photo')).toBe(false);
    expect(dispatches).toBe(1);
  });

  it('starts one run for adds within 60 seconds', async () => {
    for (const p of ['a', 'b', 'c', 'd', 'e']) await send(msg(`/add com.example.${p}`));
    expect(repo.onDemand).toHaveLength(5);
    expect(dispatches).toBe(1);
    expect(tg.last().text).toContain('Full report coming in a few minutes');
  });

  it('the 6th new app in a day waits for the digest (New-App Quota)', async () => {
    for (const p of ['a', 'b', 'c', 'd', 'e', 'f']) await send(msg(`/add com.example.${p}`));
    expect(repo.onDemand).toHaveLength(5);
    expect(tg.last().text).toContain('First update at 11:00');
    expect(repo.events.at(-1)).toMatchObject({ event: 'on_demand_skipped', props: { reason: 'user_quota' } });
  });

  it('the On-demand Budget stops runs for everyone and alerts the Admin once', async () => {
    repo.config.set('on_demand_budget_per_day', '2');
    await send(msg('/add com.example.a', 1));
    await send(msg('/add com.example.b', 2));
    await send(msg('/add com.example.c', 3));
    await send(msg('/add com.example.d', 4));
    expect(repo.onDemand).toHaveLength(2);
    expect(tg.sent.filter((s) => s.chatId === ADMIN && s.text?.includes('On-demand Budget'))).toHaveLength(1);
  });

  it('without a dispatcher the first update waits for the digest', async () => {
    deps.dispatch = undefined;
    await send(msg('/add com.example.notes'));
    expect(repo.onDemand).toHaveLength(0);
    expect(tg.last().text).toContain('First update at');
  });

  it('a failed dispatch is logged and the user is told about the digest', async () => {
    deps.dispatch = async () => {
      throw new Error('repository_dispatch failed: 401');
    };
    await send(msg('/add com.example.notes'));
    expect(repo.errors[0].message).toContain('401');
    expect(tg.last().text).toContain('First update at');
  });
});

describe('/budget platform limits', () => {
  it('lists Vercel and database usage with colour by threshold', async () => {
    repo.usage = [
      { provider: 'vercel', resource: 'invocations', used: 720000, limit: 1000000, unit: 'invocations' },
      { provider: 'vercel', resource: 'active_cpu', used: 3.7, limit: 4, unit: 'CPU-hours' },
      { provider: 'supabase', resource: 'db_size', used: 12.4, limit: 500, unit: 'MB' },
    ];
    await send(msg('/budget', ADMIN));
    const text = tg.last().text!;
    expect(text).toContain('🟠 vercel invocations: 720,000 of 1,000,000 invocations (72%)');
    expect(text).toContain('🔴 vercel active cpu: 3.70 of 4.00 CPU-hours (93%)');
    expect(text).toContain('🟢 supabase db size: 12.40 of 500 MB (2%)');
  });
});
