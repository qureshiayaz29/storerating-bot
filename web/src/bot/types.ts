// Telegram update shapes we use, and the Repo the handlers depend on.

export interface TgUser {
  id: number;
  is_bot?: boolean;
  language_code?: string;
}
export interface TgChat {
  id: number;
  type: 'private' | 'group' | 'supergroup' | 'channel';
}
export interface TgMessage {
  message_id: number;
  from?: TgUser;
  chat: TgChat;
  text?: string;
}
export interface TgCallbackQuery {
  id: string;
  from: TgUser;
  message?: TgMessage;
  data?: string;
}
export interface TgUpdate {
  update_id: number;
  message?: TgMessage;
  callback_query?: TgCallbackQuery;
  my_chat_member?: { chat: TgChat; new_chat_member: { status: string } };
}

export interface UserRow {
  chat_id: number;
  timezone: string;
  digest_time: string; // HH:MM
  paused: boolean;
}

export interface WatchRow {
  package: string;
  name: string | null;
  paused: boolean;
}

export interface AppRow {
  package: string;
  name: string | null;
}

export interface BudgetState {
  monthlyBudgetUsd: number;
  spentUsd: number;
}

export interface Stats {
  users: number;
  activeToday: number;
  watchedApps: number;
  blocked: number;
}

/** Everything the handlers need from the database. PgRepo implements it; tests use a fake. */
export interface Repo {
  touchUser(chatId: number, languageCode?: string): Promise<{ created: boolean }>;
  getUser(chatId: number): Promise<UserRow | null>;
  setPaused(chatId: number, paused: boolean): Promise<void>;
  setBlocked(chatId: number, blocked: boolean): Promise<void>;
  setDigest(chatId: number, fields: { timezone?: string; digest_time?: string }): Promise<void>;
  deleteUser(chatId: number): Promise<void>;

  getConfig(key: string): Promise<string | null>;
  setConfig(key: string, value: string): Promise<void>;

  getApp(pkg: string): Promise<AppRow | null>;
  upsertApp(app: { package: string; name: string; developer: string | null; icon_url: string | null }): Promise<void>;

  watchlist(chatId: number): Promise<WatchRow[]>;
  addWatch(chatId: number, pkg: string): Promise<boolean>; // false if already watching
  removeWatch(chatId: number, pkg: string): Promise<void>;
  setWatchPaused(chatId: number, pkg: string, paused: boolean): Promise<void>;

  countEventsSince(chatId: number, events: string[], since: Date): Promise<number>;
  logEvent(chatId: number | null, event: string, props?: Record<string, unknown>): Promise<void>;
  logError(source: 'store_source' | 'telegram' | 'web', pkg: string | null, message: string, stack?: string): Promise<void>;

  /** Newest daily Card for the App created at or after `since`, if any. */
  latestCard(pkg: string, since: Date): Promise<{ file_id: string; caption: string; period_start: string } | null>;
  countOnDemand(since: Date, chatId?: number): Promise<number>;
  hasPendingOnDemand(chatId: number, since: Date): Promise<boolean>;
  insertOnDemand(chatId: number, pkg: string): Promise<void>;
  recordDelivery(chatId: number, pkg: string, kind: 'daily' | 'alert', ref: string, status: 'sent' | 'failed'): Promise<void>;

  budget(now: Date): Promise<BudgetState>;
  /** Latest Platform Limits rows (Vercel self-metered usage, database size). */
  platformUsage(): Promise<{ provider: string; resource: string; used: number; limit: number; unit: string }[]>;
  stats(now: Date): Promise<Stats>;
}
