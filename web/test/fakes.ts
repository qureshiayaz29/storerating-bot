import type { Listing, LookupSource } from '../src/sources/contract';
import { SourceError } from '../src/sources/contract';
import type { Keyboard, Telegram } from '../src/lib/telegram';
import type { AppRow, BudgetState, Repo, Stats, UserRow, WatchRow } from '../src/bot/types';

export class FakeRepo implements Repo {
  users = new Map<number, UserRow & { blocked: boolean; last_active_at: Date }>();
  apps = new Map<string, AppRow & { developer: string | null; icon_url: string | null }>();
  watch: { chat_id: number; package: string; paused: boolean }[] = [];
  events: { chat_id: number | null; event: string; props: Record<string, unknown>; ts: Date }[] = [];
  errors: { source: string; pkg: string | null; message: string }[] = [];
  config = new Map<string, string>([
    ['monthly_budget_usd', '30'],
    ['watchlist_max_apps', '10'],
    ['action_limit_per_hour', '20'],
  ]);
  spent = 0;
  cards = new Map<string, { file_id: string; caption: string; period_start: string; created_at: Date }>();
  onDemand: { chat_id: number; package: string; requested_at: Date; status: string }[] = [];
  deliveries: { chat_id: number; package: string; kind: string; ref: string; status: string }[] = [];
  now = () => new Date('2026-10-29T06:00:00Z');

  async touchUser(chatId: number) {
    const u = this.users.get(chatId);
    if (u) {
      u.blocked = false;
      u.last_active_at = this.now();
      return { created: false };
    }
    this.users.set(chatId, { chat_id: chatId, timezone: 'Asia/Kolkata', digest_time: '11:00', paused: false, blocked: false, last_active_at: this.now() });
    return { created: true };
  }
  async getUser(chatId: number) {
    return this.users.get(chatId) ?? null;
  }
  async setPaused(chatId: number, paused: boolean) {
    this.users.get(chatId)!.paused = paused;
  }
  async setBlocked(chatId: number, blocked: boolean) {
    const u = this.users.get(chatId);
    if (u) u.blocked = blocked;
  }
  async setDigest(chatId: number, f: { timezone?: string; digest_time?: string }) {
    Object.assign(this.users.get(chatId)!, Object.fromEntries(Object.entries(f).filter(([, v]) => v)));
  }
  async deleteUser(chatId: number) {
    this.users.delete(chatId);
    this.watch = this.watch.filter((w) => w.chat_id !== chatId);
    this.events = this.events.filter((e) => e.chat_id !== chatId);
  }
  async getConfig(key: string) {
    return this.config.get(key) ?? null;
  }
  async setConfig(key: string, value: string) {
    this.config.set(key, value);
  }
  async getApp(pkg: string) {
    const a = this.apps.get(pkg);
    return a ? { package: a.package, name: a.name } : null;
  }
  async upsertApp(app: { package: string; name: string; developer: string | null; icon_url: string | null }) {
    this.apps.set(app.package, app);
  }
  async watchlist(chatId: number): Promise<WatchRow[]> {
    return this.watch
      .filter((w) => w.chat_id === chatId)
      .map((w) => ({ package: w.package, paused: w.paused, name: this.apps.get(w.package)?.name ?? null }));
  }
  async addWatch(chatId: number, pkg: string) {
    if (this.watch.some((w) => w.chat_id === chatId && w.package === pkg)) return false;
    this.watch.push({ chat_id: chatId, package: pkg, paused: false });
    return true;
  }
  async removeWatch(chatId: number, pkg: string) {
    this.watch = this.watch.filter((w) => !(w.chat_id === chatId && w.package === pkg));
  }
  async setWatchPaused(chatId: number, pkg: string, paused: boolean) {
    this.watch.find((w) => w.chat_id === chatId && w.package === pkg)!.paused = paused;
  }
  async countEventsSince(chatId: number, events: string[], since: Date) {
    return this.events.filter((e) => e.chat_id === chatId && events.includes(e.event) && e.ts >= since).length;
  }
  async logEvent(chatId: number | null, event: string, props: Record<string, unknown> = {}) {
    this.events.push({ chat_id: chatId, event, props, ts: this.now() });
  }
  async logError(source: string, pkg: string | null, message: string) {
    this.errors.push({ source, pkg, message });
  }
  async latestCard(pkg: string, since: Date) {
    const c = this.cards.get(pkg);
    return c && c.created_at >= since ? { file_id: c.file_id, caption: c.caption, period_start: c.period_start } : null;
  }
  async countOnDemand(since: Date, chatId?: number) {
    return this.onDemand.filter((o) => o.requested_at >= since && (chatId === undefined || o.chat_id === chatId)).length;
  }
  async hasPendingOnDemand(chatId: number, since: Date) {
    return this.onDemand.some((o) => o.chat_id === chatId && o.status === 'pending' && o.requested_at >= since);
  }
  async insertOnDemand(chatId: number, pkg: string) {
    this.onDemand.push({ chat_id: chatId, package: pkg, requested_at: this.now(), status: 'pending' });
  }
  async recordDelivery(chatId: number, pkg: string, kind: string, ref: string, status: string) {
    this.deliveries.push({ chat_id: chatId, package: pkg, kind, ref, status });
  }
  async budget(): Promise<BudgetState> {
    return { monthlyBudgetUsd: Number(this.config.get('monthly_budget_usd')), spentUsd: this.spent };
  }
  async stats(): Promise<Stats> {
    return { users: this.users.size, activeToday: this.users.size, watchedApps: new Set(this.watch.map((w) => w.package)).size, blocked: 0 };
  }
  eventNames(chatId?: number) {
    return this.events.filter((e) => chatId === undefined || e.chat_id === chatId).map((e) => e.event);
  }
}

export interface Sent {
  kind: 'send' | 'edit' | 'answer' | 'photo';
  fileId?: string;
  chatId?: number;
  text?: string;
  keyboard?: Keyboard;
}

export class FakeTelegram implements Telegram {
  sent: Sent[] = [];
  async sendMessage(chatId: number, text: string, keyboard?: Keyboard) {
    this.sent.push({ kind: 'send', chatId, text, keyboard });
  }
  async editMessage(chatId: number, _id: number, text: string, keyboard?: Keyboard) {
    this.sent.push({ kind: 'edit', chatId, text, keyboard });
  }
  async sendPhoto(chatId: number, fileId: string, caption: string) {
    this.sent.push({ kind: 'photo', chatId, fileId, text: caption });
  }
  async answerCallback(_id: string, text?: string) {
    this.sent.push({ kind: 'answer', text });
  }
  last() {
    return [...this.sent].reverse().find((s) => s.kind !== 'answer')!;
  }
}

export class FakeLookup implements LookupSource {
  readonly name = 'fake';
  calls: string[] = [];
  constructor(private readonly behaviour: (pkg: string) => Listing | SourceError = listingFor) {}
  async getListing(pkg: string) {
    this.calls.push(pkg);
    const r = this.behaviour(pkg);
    if (r instanceof SourceError) throw r;
    return r;
  }
  async exists(pkg: string) {
    return !(this.behaviour(pkg) instanceof SourceError);
  }
}

export function listingFor(pkg: string): Listing {
  return {
    store: 'google_play',
    package: pkg,
    name: `App ${pkg.split('.').pop()}`,
    developer: 'Dev',
    icon_url: null,
    version: '1.2.3',
    rating: 4.3,
    review_count: 100,
    rating_histogram: [1, 2, 3, 4, 90],
    installs_tier: '1,000+',
    size: null,
    updated_at: null,
    release_notes: null,
  };
}
