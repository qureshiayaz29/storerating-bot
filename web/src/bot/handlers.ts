// Bot logic. Pure functions of (update, deps) so they can be tested with fakes.

import { createHash } from 'node:crypto';
import {
  appKey,
  countryName,
  detectStore,
  isCountryCode,
  parseAppInput,
  parseAppKey,
  SourceError,
  storeBadge,
  storeName,
  type AppInput,
  type LookupSource,
  type Store,
} from '../sources/contract';
import { timezoneCountry } from '../lib/timezones';
import { escapeHtml, type Keyboard, type Telegram } from '../lib/telegram';
import type { PendingReply, Repo, TgCallbackQuery, TgMessage, TgUpdate, UserRow, WatchRow } from './types';

export interface Deps {
  repo: Repo;
  tg: Telegram;
  /** Google Play lookup. */
  lookup: LookupSource;
  /** App Store lookup. Absent in tests that only use Google Play. */
  appStoreLookup?: LookupSource;
  adminChatId: number;
  now?: () => Date;
  /** Starts an On-demand Run (GitHub repository_dispatch). Absent in tests that do not need it. */
  dispatch?: () => Promise<void>;
  /** Builds a 5-minute dashboard Login Link. Absent when SESSION_SECRET is not set. */
  loginLink?: () => string;
}

const DEFAULTS = { watchlistMax: 10, actionLimitPerHour: 20, newAppQuota: 10, onDemandBudget: 200 };
const DEBOUNCE_MS = 60_000;
const CARD_FRESH_MS = 30 * 60 * 60 * 1000;
const PENDING_MS = 10 * 60 * 1000;
const AVAILABILITY_DEFAULT = 'us,gb,in';
const ADMIN_COMMANDS = new Set(['/stats', '/budget', '/setbudget', '/login']);

export const TEXT = {
  welcome:
    '👋 <b>StoreRating</b> watches Google Play apps and tells you what changed: rating moves, new releases, top complaints and 1★ spikes.\n\n' +
    'Send /add with a Play Store or App Store link to start, e.g. <code>/add com.whatsapp</code>\n\n' +
    'You get one update per app each day at 11:00, only when something changed.\n\n' +
    'We store your chat ID, timezone, settings and watchlist. Nothing else. /delete removes it all.',
  help: "I don't know that command. Try /add, /apps, /pause, /resume, /settings or /delete.",
  addUsage:
    'Send a Play Store or App Store link, a package name like <code>/add com.whatsapp</code>, or an App Store ID like <code>/add 310633997</code>',
  addInvalid:
    "That doesn't look like an app link or ID. Send a Play Store or App Store link, a package name like <code>com.whatsapp</code>, or an App Store ID like <code>310633997</code>.",
  notFound: (id: string, store: Store = 'google_play') =>
    `Couldn't find <code>${escapeHtml(id)}</code> on ${storeName(store)}. Check the link and try again.`,
  storeDown: "Couldn't reach the store right now. Please try again in a few minutes.",
  countryQuestion: (store: Store) =>
    `🌍 Which country's ${storeName(store)} should I track this app in? Send a two-letter code, like IN, US or GB.`,
  badCode: (code: string) =>
    `<b>${escapeHtml(code.toUpperCase())}</b> isn't a country code I know. Send two letters, like IN, US or GB.`,
  notInCountry: (name: string, store: Store, country: string, found: string[]) =>
    `<b>${escapeHtml(name)}</b> isn't on ${storeName(store)} in ${escapeHtml(countryName(country))}. ` +
    `It's available in ${found.map((c) => c.toUpperCase()).join(', ')}. Send one of these codes, or another country code.`,
  tooMany: 'Too many changes, try again in a few minutes.',
  limitReached: (max: number) => `You're watching ${max} apps, the maximum. Remove one to add another:`,
  alreadyWatching: (name: string) => `You're already watching <b>${escapeHtml(name)}</b>.`,
  empty: 'You are not watching any apps yet. Send /add with a Play Store link to start.',
  paused: '⏸ All notifications paused. Data is still tracked, so nothing is lost. /resume to turn them back on.',
  resumed: '▶️ Notifications are back on.',
  deleteAsk: 'Delete all your data? This removes your watchlist and settings and cannot be undone.',
  deleted: '🗑 Done. All your data is deleted. Send /start any time to come back.',
  deleteCancelled: 'Cancelled. Nothing was deleted.',
  groupsOnly: 'I only work in private chats. Message me directly.',
};

export async function handleUpdate(update: TgUpdate, deps: Deps): Promise<void> {
  if (update.message) return handleMessage(update.message, deps);
  if (update.callback_query) return handleCallback(update.callback_query, deps);
  if (update.my_chat_member && update.my_chat_member.chat.type === 'private') {
    const chatId = update.my_chat_member.chat.id;
    const status = update.my_chat_member.new_chat_member.status;
    if (status === 'kicked') {
      await deps.repo.setBlocked(chatId, true);
      await deps.repo.logEvent(chatId, 'bot_blocked');
    } else if (status === 'member') {
      await deps.repo.setBlocked(chatId, false);
      await deps.repo.logEvent(chatId, 'bot_unblocked');
    }
  }
}

/** Admin Check: the sender is the Admin and the chat is private. */
export function isAdmin(msg: { from?: { id: number }; chat: { type: string } }, adminChatId: number): boolean {
  return msg.from?.id === adminChatId && msg.chat.type === 'private';
}

async function handleMessage(msg: TgMessage, deps: Deps): Promise<void> {
  const { repo, tg } = deps;
  const chatId = msg.chat.id;
  const text = (msg.text ?? '').trim();
  if (msg.chat.type !== 'private') {
    if (text.startsWith('/')) await tg.sendMessage(chatId, TEXT.groupsOnly);
    return;
  }
  if (!text) return;

  const { created } = await repo.touchUser(chatId, msg.from?.language_code);

  // A Pending Reply (country code) is answered by plain text; any command or expiry clears it.
  const user = await repo.getUser(chatId);
  const now = deps.now?.() ?? new Date();
  const pending = user?.pending && new Date(user.pending.expires_at) > now ? user.pending : null;
  if (user?.pending && (!pending || text.startsWith('/'))) await repo.setPending(chatId, null);
  if (pending && !text.startsWith('/')) return answerPending(chatId, text, pending, deps);

  const [rawCmd, ...rest] = text.split(/\s+/);
  const cmd = rawCmd.toLowerCase().replace(/@\w+$/, ''); // "/add@storeratingbot" → "/add"
  const arg = rest.join(' ');

  // The login page links to t.me/<bot>?start=login: the Admin gets a Login Link, anyone else the normal welcome.
  if (cmd === '/start' && arg === 'login' && isAdmin(msg, deps.adminChatId)) return handleAdmin('/login', '', chatId, deps);

  if (ADMIN_COMMANDS.has(cmd)) {
    if (!isAdmin(msg, deps.adminChatId)) {
      await repo.logEvent(chatId, 'admin_denied', { command: cmd });
      await tg.sendMessage(chatId, TEXT.help);
      return;
    }
    return handleAdmin(cmd, arg, chatId, deps);
  }

  switch (cmd) {
    case '/start':
      await repo.logEvent(chatId, 'start', { new: created });
      return tg.sendMessage(chatId, TEXT.welcome);
    case '/help':
      return tg.sendMessage(chatId, TEXT.welcome);
    case '/add':
      if (!arg) return tg.sendMessage(chatId, TEXT.addUsage);
      return addApp(chatId, arg, deps);
    case '/apps': {
      await repo.logEvent(chatId, 'view_apps');
      const view = await appsView(chatId, deps);
      return tg.sendMessage(chatId, view.text, view.keyboard);
    }
    case '/pause':
      await repo.setPaused(chatId, true);
      await repo.logEvent(chatId, 'pause_all');
      return tg.sendMessage(chatId, TEXT.paused);
    case '/resume':
      await repo.setPaused(chatId, false);
      await repo.logEvent(chatId, 'resume_all');
      return tg.sendMessage(chatId, TEXT.resumed);
    case '/settings':
      return settings(chatId, arg, deps);
    case '/delete':
      return tg.sendMessage(chatId, TEXT.deleteAsk, [
        [
          { text: '🗑 Delete everything', callback_data: 'dy' },
          { text: 'Cancel', callback_data: 'dn' },
        ],
      ]);
  }

  // A pasted store link without a command is an /add.
  if (!cmd.startsWith('/') && /play\.google\.com|market:\/\/|apps\.apple\.com|itunes\.apple\.com/.test(text)) {
    return addApp(chatId, text, deps);
  }
  await repo.logEvent(chatId, 'unknown_command', { command: cmd.startsWith('/') ? cmd : '(text)' });
  await tg.sendMessage(chatId, TEXT.help);
}

// --- /add ---------------------------------------------------------------

function lookupFor(store: Store, deps: Deps): LookupSource {
  if (store === 'app_store') {
    if (!deps.appStoreLookup) throw new SourceError('unavailable', '', 'App Store lookup not configured');
    return deps.appStoreLookup;
  }
  return deps.lookup;
}

function expiresAt(deps: Deps): string {
  return new Date((deps.now?.() ?? new Date()).getTime() + PENDING_MS).toISOString();
}

/** Country rules: the link's country, then the timezone the User set, then their saved country. */
function defaultCountry(parsed: AppInput, user: UserRow | null): string | null {
  return parsed.country ?? (user?.timezone_set ? timezoneCountry(user.timezone) : null) ?? user?.saved_country ?? null;
}

async function addApp(chatId: number, input: string, deps: Deps, opts: { country?: string } = {}): Promise<void> {
  const { repo, tg } = deps;
  const now = deps.now?.() ?? new Date();
  const parsed = parseAppInput(input);
  if (!parsed) {
    await repo.logEvent(chatId, 'add_app_failed', { reason: 'invalid_input' });
    return tg.sendMessage(chatId, TEXT.addInvalid);
  }
  if (await actionLimited(chatId, deps, now)) return;

  const user = await repo.getUser(chatId);
  const country = opts.country ?? defaultCountry(parsed, user);
  if (!country) {
    await repo.setPending(chatId, { kind: 'add_country', input, save: true, expires_at: expiresAt(deps) });
    await repo.logEvent(chatId, 'country_asked', { store: parsed.store });
    return tg.sendMessage(chatId, TEXT.countryQuestion(parsed.store));
  }
  const key = appKey(parsed.store, country, parsed.id);

  const list = await repo.watchlist(chatId);
  const existing = list.find((w) => w.package === key);
  if (existing) return tg.sendMessage(chatId, TEXT.alreadyWatching(existing.name ?? parsed.id));

  const max = await configInt(repo, 'watchlist_max_apps', DEFAULTS.watchlistMax);
  if (list.length >= max) {
    await repo.logEvent(chatId, 'add_app_failed', { reason: 'cap', package: key });
    return tg.sendMessage(chatId, TEXT.limitReached(max), removeKeyboard(list));
  }

  let app = await repo.getApp(key);
  let details = '';
  if (!app) {
    try {
      const listing = await lookupFor(parsed.store, deps).getListing(parsed.id, country);
      await repo.upsertApp({ package: key, store: parsed.store, name: listing.name, developer: listing.developer, icon_url: listing.icon_url });
      app = { package: key, name: listing.name };
      details = listingLine(listing);
    } catch (e) {
      if (e instanceof SourceError && e.reason === 'not_found') {
        return offerCountries(chatId, parsed, country, { kind: 'add_country', input, save: false }, deps);
      }
      return storeFailed(chatId, key, e, deps);
    }
  }

  await repo.addWatch(chatId, app.package);
  await repo.logEvent(chatId, 'add_app', { package: app.package });
  const head = `✅ Added <b>${escapeHtml(app.name ?? parsed.id)}</b> · ${storeBadge(key)}${details ? `\n${details}` : ''}`;
  return sendFirstUpdate(chatId, app.package, head, deps, now);
}

/** After an add or a Country Change: send today's Card if one exists, else start an On-demand Run. */
async function sendFirstUpdate(chatId: number, key: string, head: string, deps: Deps, now: Date): Promise<void> {
  const { repo, tg } = deps;
  const user = await repo.getUser(chatId);
  const when = user ? `${user.digest_time} (${user.timezone})` : '11:00';
  const keyboard: Keyboard = [[{ text: '🌍 Change country', callback_data: `c:${pkgKey(key)}` }]];

  // Today's Card already exists (someone else watches this App): send it now, at no cost.
  const card = await repo.latestCard(key, new Date(now.getTime() - CARD_FRESH_MS));
  if (card) {
    await tg.sendMessage(chatId, `${head}\n\nHere's today's update. Next one at ${escapeHtml(when)}. Manage with /apps.`, keyboard);
    await tg.sendPhoto(chatId, card.file_id, card.caption);
    await repo.recordDelivery(chatId, key, 'daily', card.period_start, 'sent');
    await repo.logEvent(chatId, 'update_sent', { package: key, instant: true });
    return;
  }
  const startedRun = await startOnDemand(chatId, key, deps, now);
  await tg.sendMessage(
    chatId,
    startedRun
      ? `${head}\n\n⏳ Your first update arrives in a few minutes. After that, daily at ${escapeHtml(when)}. Manage with /apps.`
      : `${head}\n\nFirst update at ${escapeHtml(when)}. Manage with /apps.`,
    keyboard,
  );
}

async function storeFailed(chatId: number, key: string, e: unknown, deps: Deps): Promise<void> {
  const err = e as Error;
  await deps.repo.logError('store_source', key, err?.message ?? String(e), err?.stack);
  await deps.repo.logEvent(chatId, 'add_app_failed', { reason: e instanceof SourceError ? e.reason : 'error', package: key });
  return deps.tg.sendMessage(chatId, TEXT.storeDown);
}

/** Availability Check: the App is missing in `country`. Offer the countries where it exists, or say not found. */
async function offerCountries(
  chatId: number,
  parsed: { store: Store; id: string },
  country: string,
  next: { kind: 'add_country'; input: string; save: boolean } | { kind: 'change_country'; app_key: string },
  deps: Deps,
  skip: string[] = [],
): Promise<void> {
  const { repo, tg } = deps;
  const user = await repo.getUser(chatId);
  const configured = ((await repo.getConfig('availability_countries')) ?? AVAILABILITY_DEFAULT).split(',');
  const tz = user?.timezone_set ? timezoneCountry(user.timezone) : null;
  const candidates = [...new Set([...configured, ...(tz ? [tz] : [])].map((c) => c.trim().toLowerCase()))].filter(
    (c) => isCountryCode(c) && c !== country && !skip.includes(c),
  );
  const found: string[] = [];
  let name: string | null = null;
  for (const c of candidates) {
    try {
      const l = await lookupFor(parsed.store, deps).getListing(parsed.id, c);
      found.push(c);
      name ??= l.name;
    } catch (e) {
      if (!(e instanceof SourceError && e.reason === 'not_found')) break; // store trouble: stop checking
    }
  }
  if (!found.length) {
    await repo.logEvent(chatId, 'add_app_failed', { reason: 'not_found', package: appKey(parsed.store, country, parsed.id) });
    return tg.sendMessage(chatId, TEXT.notFound(parsed.id, parsed.store));
  }
  await repo.setPending(chatId, { ...next, expires_at: expiresAt(deps) } as PendingReply);
  await repo.logEvent(chatId, 'not_in_country', { store: parsed.store, country, found });
  return tg.sendMessage(chatId, TEXT.notInCountry(name ?? parsed.id, parsed.store, country, found));
}

/** A plain-text reply to the Country Question or a Country Change. */
async function answerPending(chatId: number, text: string, pending: PendingReply, deps: Deps): Promise<void> {
  const { repo, tg } = deps;
  const code = text.trim().toLowerCase();
  if (!isCountryCode(code)) {
    if (code.length > 2 && detectStore(text)) {
      await repo.setPending(chatId, null); // they sent a new app instead
      return addApp(chatId, text, deps);
    }
    await repo.logEvent(chatId, 'country_invalid', { kind: pending.kind });
    return tg.sendMessage(chatId, TEXT.badCode(text.trim().slice(0, 12)));
  }
  await repo.setPending(chatId, null);
  if (pending.kind === 'add_country') {
    if (pending.save) await repo.setSavedCountry(chatId, code);
    return addApp(chatId, pending.input, deps, { country: code });
  }
  return changeCountry(chatId, pending.app_key, code, deps);
}

// --- Country Change ------------------------------------------------------

async function askCountryChange(chatId: number, target: WatchRow, deps: Deps): Promise<void> {
  const k = parseAppKey(target.package);
  await deps.repo.setPending(chatId, { kind: 'change_country', app_key: target.package, expires_at: expiresAt(deps) });
  await deps.tg.sendMessage(
    chatId,
    `🌍 Which country should I track <b>${escapeHtml(target.name ?? k.id)}</b> in? It's ${escapeHtml(countryName(k.country))} now. ` +
      'Send a two-letter code, like IN, US or GB.',
  );
}

async function changeCountry(chatId: number, oldKey: string, code: string, deps: Deps): Promise<void> {
  const { repo, tg } = deps;
  const now = deps.now?.() ?? new Date();
  const list = await repo.watchlist(chatId);
  const target = list.find((w) => w.package === oldKey);
  if (!target) return tg.sendMessage(chatId, 'That app is no longer in your list.');
  const k = parseAppKey(oldKey);
  const name = target.name ?? k.id;
  if (k.country === code) return tg.sendMessage(chatId, `<b>${escapeHtml(name)}</b> is already tracked in ${escapeHtml(countryName(code))}.`);
  const newKey = appKey(k.store, code, k.id);
  if (list.some((w) => w.package === newKey)) {
    return tg.sendMessage(chatId, `You already watch <b>${escapeHtml(name)}</b> in ${escapeHtml(countryName(code))}.`);
  }
  if (!(await repo.getApp(newKey))) {
    try {
      const listing = await lookupFor(k.store, deps).getListing(k.id, code);
      await repo.upsertApp({ package: newKey, store: k.store, name: listing.name, developer: listing.developer, icon_url: listing.icon_url });
    } catch (e) {
      if (e instanceof SourceError && e.reason === 'not_found') {
        return offerCountries(chatId, { store: k.store, id: k.id }, code, { kind: 'change_country', app_key: oldKey }, deps, [k.country]);
      }
      return storeFailed(chatId, newKey, e, deps);
    }
  }
  await repo.moveWatch(chatId, oldKey, newKey);
  await repo.logEvent(chatId, 'change_country', { from: oldKey, to: newKey });
  const head =
    `🌍 <b>${escapeHtml(name)}</b> moved from ${storeBadge(oldKey)} to ${storeBadge(newKey)}. ` +
    `Ratings, reviews and trends now come from ${escapeHtml(countryName(code))}; the trend starts fresh today.`;
  return sendFirstUpdate(chatId, newKey, head, deps, now);
}

/** Queue an On-demand Run within the New-App Quota and On-demand Budget. Returns false when skipped. */
async function startOnDemand(chatId: number, pkg: string, deps: Deps, now: Date): Promise<boolean> {
  const { repo } = deps;
  if (!deps.dispatch) return false;
  const day = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  const quota = await configInt(repo, 'new_app_quota_per_user_per_day', DEFAULTS.newAppQuota);
  // The Admin is exempt: testing, country moves and re-runs must not wait a day.
  if (chatId !== deps.adminChatId && (await repo.countOnDemand(day, chatId)) >= quota) {
    await repo.logEvent(chatId, 'on_demand_skipped', { reason: 'user_quota', package: pkg });
    return false;
  }
  const budget = await configInt(repo, 'on_demand_budget_per_day', DEFAULTS.onDemandBudget);
  if ((await repo.countOnDemand(day)) >= budget) {
    await repo.logEvent(chatId, 'on_demand_skipped', { reason: 'global_budget', package: pkg });
    const flag = `on_demand_budget_alert:${now.toISOString().slice(0, 10)}`;
    if (!(await repo.getConfig(flag))) {
      await repo.setConfig(flag, '1');
      await deps.tg.sendMessage(deps.adminChatId, `⚠️ On-demand Budget reached: ${budget} new apps in 24 hours. New apps now wait for the next digest.`);
    }
    return false;
  }
  // Adds within 60 seconds share one run: only the first one dispatches.
  const recent = await repo.hasPendingOnDemand(chatId, new Date(now.getTime() - DEBOUNCE_MS));
  await repo.insertOnDemand(chatId, pkg);
  if (recent) return true;
  try {
    await deps.dispatch();
    await repo.logEvent(chatId, 'on_demand_started', { package: pkg });
    return true;
  } catch (e) {
    const err = e as Error;
    await repo.logError('web', pkg, `on-demand dispatch failed: ${err.message}`, err.stack);
    return false;
  }
}

function listingLine(l: { rating: number | null; version: string | null; installs_tier: string | null }): string {
  const parts = [
    l.rating != null ? `${l.rating.toFixed(1)}★` : null,
    l.version ? `v${escapeHtml(l.version)}` : null,
    l.installs_tier ? `${escapeHtml(l.installs_tier)} installs` : null,
  ].filter(Boolean);
  return parts.join(' · ');
}

async function actionLimited(chatId: number, deps: Deps, now: Date): Promise<boolean> {
  const limit = await configInt(deps.repo, 'action_limit_per_hour', DEFAULTS.actionLimitPerHour);
  const since = new Date(now.getTime() - 60 * 60 * 1000);
  const count = await deps.repo.countEventsSince(chatId, ['add_app', 'remove_app'], since);
  if (count < limit) return false;
  await deps.repo.logEvent(chatId, 'action_limited');
  await deps.tg.sendMessage(chatId, TEXT.tooMany);
  return true;
}

// --- /apps and buttons ---------------------------------------------------

/** Short, stable key for a package, so callback_data stays under Telegram's 64-byte limit. */
export function pkgKey(pkg: string): string {
  return createHash('sha256').update(pkg).digest('hex').slice(0, 10);
}

async function appsView(chatId: number, deps: Deps): Promise<{ text: string; keyboard?: Keyboard }> {
  const [list, user, max] = await Promise.all([
    deps.repo.watchlist(chatId),
    deps.repo.getUser(chatId),
    configInt(deps.repo, 'watchlist_max_apps', DEFAULTS.watchlistMax),
  ]);
  if (!list.length) return { text: TEXT.empty };
  const header = `📱 <b>Your apps (${list.length}/${max})</b> · ${user?.paused ? '🔕 Notifications paused' : '🔔 Notifications on'}`;
  const label = (w: WatchRow) => w.name ?? parseAppKey(w.package).id;
  const lines = list.map((w) => `${w.paused ? '🔕' : '🔔'} ${escapeHtml(label(w))} · ${storeBadge(w.package)}`);
  const keyboard: Keyboard = list.map((w) => [
    w.paused
      ? { text: `▶️ ${short(label(w), 20)}`, callback_data: `r:${pkgKey(w.package)}` }
      : { text: `⏸ ${short(label(w), 20)}`, callback_data: `p:${pkgKey(w.package)}` },
    { text: `🌍 ${parseAppKey(w.package).country.toUpperCase()}`, callback_data: `c:${pkgKey(w.package)}` },
    { text: '🗑', callback_data: `x:${pkgKey(w.package)}` },
  ]);
  keyboard.push([user?.paused ? { text: '▶️ Resume all', callback_data: 'ra' } : { text: '⏸ Pause all', callback_data: 'pa' }]);
  return { text: `${header}\n\n${lines.join('\n')}`, keyboard };
}

function removeKeyboard(list: WatchRow[]): Keyboard {
  return list.map((w) => [
    { text: `🗑 ${short(w.name ?? parseAppKey(w.package).id)} · ${storeBadge(w.package)}`, callback_data: `x:${pkgKey(w.package)}` },
  ]);
}

function short(s: string, max = 24): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

async function handleCallback(q: TgCallbackQuery, deps: Deps): Promise<void> {
  const { repo, tg } = deps;
  const msg = q.message;
  const data = q.data ?? '';
  if (!msg || msg.chat.type !== 'private') return tg.answerCallback(q.id);
  const chatId = msg.chat.id;
  await repo.touchUser(chatId, q.from.language_code);
  await repo.logEvent(chatId, 'button_tap', { action: data.split(':')[0] });

  if (data === 'dy') {
    await repo.deleteUser(chatId);
    await tg.editMessage(chatId, msg.message_id, TEXT.deleted);
    return tg.answerCallback(q.id);
  }
  if (data === 'dn') {
    await tg.editMessage(chatId, msg.message_id, TEXT.deleteCancelled);
    return tg.answerCallback(q.id);
  }
  if (data === 'pa' || data === 'ra') {
    await repo.setPaused(chatId, data === 'pa');
    await repo.logEvent(chatId, data === 'pa' ? 'pause_all' : 'resume_all');
    return refresh(chatId, msg.message_id, q.id, deps, data === 'pa' ? 'All paused' : 'Resumed');
  }
  if (data === 'xn') return refresh(chatId, msg.message_id, q.id, deps);

  const [action, key] = data.split(':');
  const list = await repo.watchlist(chatId);
  const target = list.find((w) => pkgKey(w.package) === key);
  if (!target) return refresh(chatId, msg.message_id, q.id, deps, 'That app is no longer in your list');
  const name = target.name ?? parseAppKey(target.package).id;

  switch (action) {
    case 'c':
      await askCountryChange(chatId, target, deps);
      return tg.answerCallback(q.id);
    case 'p':
    case 'r':
      await repo.setWatchPaused(chatId, target.package, action === 'p');
      await repo.logEvent(chatId, action === 'p' ? 'pause_app' : 'resume_app', { package: target.package });
      return refresh(chatId, msg.message_id, q.id, deps, action === 'p' ? `Paused ${short(name)}` : `Resumed ${short(name)}`);
    case 'x':
      await tg.editMessage(chatId, msg.message_id, `Remove <b>${escapeHtml(name)}</b> from your apps?`, [
        [
          { text: '🗑 Yes, remove', callback_data: `xy:${key}` },
          { text: 'Cancel', callback_data: 'xn' },
        ],
      ]);
      return tg.answerCallback(q.id);
    case 'xy':
      if (await actionLimited(chatId, deps, deps.now?.() ?? new Date())) return tg.answerCallback(q.id);
      await repo.removeWatch(chatId, target.package);
      await repo.logEvent(chatId, 'remove_app', { package: target.package });
      return refresh(chatId, msg.message_id, q.id, deps, `Removed ${short(name)}`);
    default:
      return tg.answerCallback(q.id);
  }
}

async function refresh(chatId: number, messageId: number, callbackId: string, deps: Deps, toast?: string) {
  const view = await appsView(chatId, deps);
  await deps.tg.editMessage(chatId, messageId, view.text, view.keyboard);
  await deps.tg.answerCallback(callbackId, toast);
}

// --- /settings ----------------------------------------------------------

async function settings(chatId: number, arg: string, deps: Deps): Promise<void> {
  const { repo, tg } = deps;
  const [field, value] = arg.split(/\s+/);
  if (field === 'time') {
    const m = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(value ?? '');
    if (!m) return tg.sendMessage(chatId, 'Use 24-hour time, e.g. <code>/settings time 09:30</code>');
    const time = `${m[1].padStart(2, '0')}:${m[2]}`;
    await repo.setDigest(chatId, { digest_time: time });
    await repo.logEvent(chatId, 'settings_changed', { field: 'digest_time' });
    return tg.sendMessage(chatId, `⏰ Daily update time set to ${time}.`);
  }
  if (field === 'tz') {
    if (!value || !isTimeZone(value)) {
      return tg.sendMessage(chatId, 'Use a timezone like <code>/settings tz Asia/Kolkata</code> or <code>/settings tz Europe/London</code>');
    }
    await repo.setDigest(chatId, { timezone: value });
    await repo.logEvent(chatId, 'settings_changed', { field: 'timezone' });
    return tg.sendMessage(chatId, `🌍 Timezone set to ${escapeHtml(value)}.`);
  }
  const user = (await repo.getUser(chatId)) as UserRow;
  return tg.sendMessage(
    chatId,
    `⚙️ <b>Settings</b>\n` +
      `Daily update: ${user.digest_time}\n` +
      `Timezone: ${escapeHtml(user.timezone)}${user.timezone_set ? '' : ' (default)'}\n` +
      `Country for new apps: ${escapeHtml(newAppCountry(user))}\n` +
      `Notifications: ${user.paused ? 'paused' : 'on'}\n\n` +
      `Change with <code>/settings time 09:30</code> or <code>/settings tz Europe/London</code>`,
  );
}

function newAppCountry(user: UserRow): string {
  const tz = user.timezone_set ? timezoneCountry(user.timezone) : null;
  if (tz) return `${countryName(tz)} (from your timezone)`;
  if (user.saved_country) return countryName(user.saved_country);
  return "taken from each app's link, or I'll ask";
}

export function isTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return /\//.test(tz) || tz === 'UTC';
  } catch {
    return false;
  }
}

// --- Admin Commands --------------------------------------------------------

async function handleAdmin(cmd: string, arg: string, chatId: number, deps: Deps): Promise<void> {
  const { repo, tg } = deps;
  const now = deps.now?.() ?? new Date();
  await repo.logEvent(chatId, 'admin_command', { command: cmd });
  const reminder = 'To spend more: top up, raise the Console limit, then /setbudget &lt;amount&gt;';

  if (cmd === '/login') {
    if (!deps.loginLink) return tg.sendMessage(chatId, 'Dashboard login is not configured: set SESSION_SECRET in Vercel.');
    return tg.sendMessage(chatId, `🔐 <a href="${escapeHtml(deps.loginLink())}">Open the admin dashboard</a>\nThe link works for 5 minutes. Don't forward it.`);
  }
  if (cmd === '/stats') {
    const s = await repo.stats(now);
    return tg.sendMessage(
      chatId,
      `📊 <b>StoreRating stats</b>\nUsers: ${s.users}\nActive today: ${s.activeToday}\nApps watched: ${s.watchedApps}\nBlocked the bot: ${s.blocked}`,
    );
  }
  if (cmd === '/budget') {
    const b = await repo.budget(now);
    const days = daysInMonth(now);
    const elapsed = now.getUTCDate();
    const projected = elapsed ? (b.spentUsd / elapsed) * days : 0;
    const pct = b.monthlyBudgetUsd ? Math.round((b.spentUsd / b.monthlyBudgetUsd) * 100) : 0;
    const usage = await repo.platformUsage();
    const platform = usage.length
      ? '\n\n<b>Platform limits</b>\n' +
        usage
          .map((u) => {
            const p = u.limit ? Math.round((u.used / u.limit) * 100) : 0;
            const icon = p >= 90 ? '🔴' : p >= 70 ? '🟠' : '🟢';
            return `${icon} ${u.provider} ${u.resource.replace('_', ' ')}: ${fmt(u.used)} of ${fmt(u.limit)} ${u.unit} (${p}%)`;
          })
          .join('\n')
      : '';
    return tg.sendMessage(
      chatId,
      `💰 <b>Claude budget at ${pct}%</b>\n` +
        `Spent $${b.spentUsd.toFixed(2)} of $${b.monthlyBudgetUsd.toFixed(2)} · $${Math.max(0, b.monthlyBudgetUsd - b.spentUsd).toFixed(2)} left\n` +
        `${days - elapsed} days left · projected $${projected.toFixed(2)} by month end\n` +
        `Top up: https://console.anthropic.com/settings/billing\n${reminder}${platform}`,
    );
  }
  if (cmd === '/setbudget') {
    const amount = Number(arg);
    if (!Number.isFinite(amount) || amount < 1 || amount > 10000) {
      return tg.sendMessage(chatId, 'Usage: <code>/setbudget 40</code> (USD per month, 1–10000)');
    }
    await repo.setConfig('monthly_budget_usd', String(amount));
    const daily = amount / daysInMonth(now);
    return tg.sendMessage(
      chatId,
      `✅ Monthly budget set to $${amount.toFixed(2)} (≈ $${daily.toFixed(2)}/day this month).\n` +
        `Check that the Anthropic Console spend limit is at least $${amount.toFixed(2)}.`,
    );
  }
}

function fmt(v: number): string {
  return v >= 100 ? Math.round(v).toLocaleString('en-US') : v.toFixed(2);
}

function daysInMonth(d: Date): number {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
}

async function configInt(repo: Repo, key: string, fallback: number): Promise<number> {
  const v = Number(await repo.getConfig(key));
  return Number.isFinite(v) && v > 0 ? v : fallback;
}
