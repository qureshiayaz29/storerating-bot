// Bot logic. Pure functions of (update, deps) so they can be tested with fakes.

import { createHash } from 'node:crypto';
import { parsePackage, SourceError, type LookupSource } from '../sources/contract';
import { escapeHtml, type Keyboard, type Telegram } from '../lib/telegram';
import type { Repo, TgCallbackQuery, TgMessage, TgUpdate, UserRow, WatchRow } from './types';

export interface Deps {
  repo: Repo;
  tg: Telegram;
  lookup: LookupSource;
  adminChatId: number;
  now?: () => Date;
  /** Starts an On-demand Run (GitHub repository_dispatch). Absent in tests that do not need it. */
  dispatch?: () => Promise<void>;
  /** Builds a 5-minute dashboard Login Link. Absent when SESSION_SECRET is not set. */
  loginLink?: () => string;
}

const DEFAULTS = { watchlistMax: 10, actionLimitPerHour: 20, newAppQuota: 5, onDemandBudget: 200 };
const DEBOUNCE_MS = 60_000;
const CARD_FRESH_MS = 30 * 60 * 60 * 1000;
const ADMIN_COMMANDS = new Set(['/stats', '/budget', '/setbudget', '/login']);

export const TEXT = {
  welcome:
    '👋 <b>StoreRating</b> watches Google Play apps and tells you what changed: rating moves, new releases, top complaints and 1★ spikes.\n\n' +
    'Send /add with a Play Store link or package name to start, e.g. <code>/add com.whatsapp</code>\n\n' +
    'You get one update per app each day at 11:00, only when something changed.\n\n' +
    'We store your chat ID, timezone, settings and watchlist. Nothing else. /delete removes it all.',
  help: "I don't know that command. Try /add, /apps, /pause, /resume, /settings or /delete.",
  addUsage: 'Send a Play Store link or package name, e.g. <code>/add com.whatsapp</code>',
  addInvalid: "That doesn't look like a Play Store link or package name. Example: <code>/add com.whatsapp</code>",
  notFound: (pkg: string) => `Couldn't find <code>${escapeHtml(pkg)}</code> on Google Play. Check the link and try again.`,
  storeDown: "Couldn't reach Google Play right now. Please try again in a few minutes.",
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

  // A pasted Play Store link without a command is an /add.
  if (!cmd.startsWith('/') && /play\.google\.com|market:\/\//.test(text)) return addApp(chatId, text, deps);
  await repo.logEvent(chatId, 'unknown_command', { command: cmd.startsWith('/') ? cmd : '(text)' });
  await tg.sendMessage(chatId, TEXT.help);
}

// --- /add ---------------------------------------------------------------

async function addApp(chatId: number, input: string, deps: Deps): Promise<void> {
  const { repo, tg, lookup } = deps;
  const now = deps.now?.() ?? new Date();
  const pkg = parsePackage(input);
  if (!pkg) {
    await repo.logEvent(chatId, 'add_app_failed', { reason: 'invalid_input' });
    return tg.sendMessage(chatId, TEXT.addInvalid);
  }
  if (await actionLimited(chatId, deps, now)) return;

  const list = await repo.watchlist(chatId);
  const existing = list.find((w) => w.package === pkg);
  if (existing) return tg.sendMessage(chatId, TEXT.alreadyWatching(existing.name ?? pkg));

  const max = await configInt(repo, 'watchlist_max_apps', DEFAULTS.watchlistMax);
  if (list.length >= max) {
    await repo.logEvent(chatId, 'add_app_failed', { reason: 'cap', package: pkg });
    return tg.sendMessage(chatId, TEXT.limitReached(max), removeKeyboard(list));
  }

  let app = await repo.getApp(pkg);
  let details = '';
  if (!app) {
    try {
      const listing = await lookup.getListing(pkg);
      await repo.upsertApp({
        package: listing.package,
        name: listing.name,
        developer: listing.developer,
        icon_url: listing.icon_url,
      });
      app = { package: listing.package, name: listing.name };
      details = listingLine(listing);
    } catch (e) {
      if (e instanceof SourceError && e.reason === 'not_found') {
        await repo.logEvent(chatId, 'add_app_failed', { reason: 'not_found', package: pkg });
        return tg.sendMessage(chatId, TEXT.notFound(pkg));
      }
      const err = e as Error;
      await repo.logError('store_source', pkg, err.message ?? String(e), err.stack);
      await repo.logEvent(chatId, 'add_app_failed', {
        reason: e instanceof SourceError ? e.reason : 'error',
        package: pkg,
      });
      return tg.sendMessage(chatId, TEXT.storeDown);
    }
  }

  await repo.addWatch(chatId, app.package);
  await repo.logEvent(chatId, 'add_app', { package: app.package });
  const user = await repo.getUser(chatId);
  const when = user ? `${user.digest_time} (${user.timezone})` : '11:00';
  const head = `✅ Watching <b>${escapeHtml(app.name ?? app.package)}</b>${details ? `\n${details}` : ''}`;

  // Today's Card already exists (someone else watches this App): send it now, at no cost.
  const card = await repo.latestCard(app.package, new Date(now.getTime() - CARD_FRESH_MS));
  if (card) {
    await tg.sendMessage(chatId, `${head}\n\nHere's today's update. Next one at ${escapeHtml(when)}. Manage with /apps.`);
    await tg.sendPhoto(chatId, card.file_id, card.caption);
    await repo.recordDelivery(chatId, app.package, 'daily', card.period_start, 'sent');
    await repo.logEvent(chatId, 'update_sent', { package: app.package, instant: true });
    return;
  }

  const startedRun = await startOnDemand(chatId, app.package, deps, now);
  await tg.sendMessage(
    chatId,
    startedRun
      ? `${head}\n\n⏳ Full report coming in a few minutes. After that, daily at ${escapeHtml(when)}. Manage with /apps.`
      : `${head}\n\nFirst update at ${escapeHtml(when)}. Manage with /apps.`,
  );
}

/** Queue an On-demand Run within the New-App Quota and On-demand Budget. Returns false when skipped. */
async function startOnDemand(chatId: number, pkg: string, deps: Deps, now: Date): Promise<boolean> {
  const { repo } = deps;
  if (!deps.dispatch) return false;
  const day = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  const quota = await configInt(repo, 'new_app_quota_per_user_per_day', DEFAULTS.newAppQuota);
  if ((await repo.countOnDemand(day, chatId)) >= quota) {
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
  const lines = list.map((w) => `${w.paused ? '🔕' : '🔔'} ${escapeHtml(w.name ?? w.package)}`);
  const keyboard: Keyboard = list.map((w) => [
    w.paused
      ? { text: `▶️ ${short(w.name ?? w.package)}`, callback_data: `r:${pkgKey(w.package)}` }
      : { text: `⏸ ${short(w.name ?? w.package)}`, callback_data: `p:${pkgKey(w.package)}` },
    { text: '🗑 Remove', callback_data: `x:${pkgKey(w.package)}` },
  ]);
  keyboard.push([user?.paused ? { text: '▶️ Resume all', callback_data: 'ra' } : { text: '⏸ Pause all', callback_data: 'pa' }]);
  return { text: `${header}\n\n${lines.join('\n')}`, keyboard };
}

function removeKeyboard(list: WatchRow[]): Keyboard {
  return list.map((w) => [{ text: `🗑 ${short(w.name ?? w.package)}`, callback_data: `x:${pkgKey(w.package)}` }]);
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
  const name = target.name ?? target.package;

  switch (action) {
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
      `Timezone: ${escapeHtml(user.timezone)}\n` +
      `Notifications: ${user.paused ? 'paused' : 'on'}\n\n` +
      `Change with <code>/settings time 09:30</code> or <code>/settings tz Europe/London</code>`,
  );
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
