// Telegram Login verification and the signed Admin Session cookie.
// See PLAN.md → Admin Session, Dashboard data.

import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

export const SESSION_COOKIE = 'sr_admin';
export const SESSION_HOURS = 12;
export const LOGIN_MAX_AGE_S = 24 * 60 * 60;

export interface TelegramLogin {
  id: number;
  auth_date: number;
  hash: string;
  [k: string]: string | number;
}

/**
 * Verify Telegram Login Widget data: HMAC-SHA-256 of the sorted "key=value"
 * lines (without hash), keyed by SHA-256 of the bot token. Rejects logins older than 1 day.
 */
export function verifyTelegramLogin(params: URLSearchParams, botToken: string, nowS = Math.floor(Date.now() / 1000)):
  | { ok: true; id: number }
  | { ok: false; reason: string } {
  const hash = params.get('hash');
  const id = Number(params.get('id'));
  const authDate = Number(params.get('auth_date'));
  if (!hash || !Number.isSafeInteger(id) || !Number.isFinite(authDate)) return { ok: false, reason: 'missing fields' };
  const check = [...params.entries()]
    .filter(([k]) => k !== 'hash')
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join('\n');
  const secret = createHash('sha256').update(botToken).digest();
  const expected = createHmac('sha256', secret).update(check).digest('hex');
  if (!equalHex(expected, hash)) return { ok: false, reason: 'bad hash' };
  if (nowS - authDate > LOGIN_MAX_AGE_S) return { ok: false, reason: 'login too old' };
  return { ok: true, id };
}

/** Cookie value: "<id>.<expires>.<hmac>" signed with SESSION_SECRET. */
export function signSession(id: number, secret: string, nowMs = Date.now()): string {
  const exp = nowMs + SESSION_HOURS * 3_600_000;
  return `${id}.${exp}.${createHmac('sha256', secret).update(`${id}.${exp}`).digest('hex')}`;
}

export function readSession(value: string | undefined, secret: string, adminId: number, nowMs = Date.now()): boolean {
  if (!value) return false;
  const [id, exp, sig] = value.split('.');
  if (!id || !exp || !sig) return false;
  const expected = createHmac('sha256', secret).update(`${id}.${exp}`).digest('hex');
  return equalHex(expected, sig) && Number(id) === adminId && Number(exp) > nowMs;
}

function equalHex(a: string, b: string): boolean {
  if (a.length !== b.length || !/^[0-9a-f]+$/i.test(b)) return false;
  return timingSafeEqual(Buffer.from(a, 'hex'), Buffer.from(b, 'hex'));
}

export const LOGIN_LINK_MINUTES = 5;

/** Login Link token: "<id>.<expires>.<hmac>", HMAC over "login.<id>.<expires>" so it can never pass as a session. */
export function signLoginToken(id: number, secret: string, nowMs = Date.now()): string {
  const exp = nowMs + LOGIN_LINK_MINUTES * 60_000;
  return `${id}.${exp}.${createHmac('sha256', secret).update(`login.${id}.${exp}`).digest('hex')}`;
}

export function verifyLoginToken(token: string | null, secret: string, adminId: number, nowMs = Date.now()): boolean {
  if (!token) return false;
  const [id, exp, sig] = token.split('.');
  if (!id || !exp || !sig) return false;
  const expected = createHmac('sha256', secret).update(`login.${id}.${exp}`).digest('hex');
  return equalHex(expected, sig) && Number(id) === adminId && Number(exp) > nowMs;
}

export function sessionCookie(value: string): string {
  return `${SESSION_COOKIE}=${value}; Path=/admin; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_HOURS * 3600}`;
}

export const CLEAR_COOKIE = `${SESSION_COOKIE}=; Path=/admin; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
