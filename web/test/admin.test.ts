import { createHash, createHmac } from 'node:crypto';
import { afterEach, describe, expect, it } from 'vitest';
import { adminGate } from '../src/lib/admin-gate';
import { GET as auth } from '../src/pages/admin/auth';
import { readSession, SESSION_COOKIE, signSession, verifyTelegramLogin } from '../src/lib/session';

const TOKEN = '123456:TEST-token';
const ADMIN = 999;
const NOW_S = 1_793_200_000;

function login(fields: Record<string, string | number>, token = TOKEN): URLSearchParams {
  const check = Object.keys(fields).sort().map((k) => `${k}=${fields[k]}`).join('\n');
  const secret = createHash('sha256').update(token).digest();
  const hash = createHmac('sha256', secret).update(check).digest('hex');
  return new URLSearchParams({ ...Object.fromEntries(Object.entries(fields).map(([k, v]) => [k, String(v)])), hash });
}

describe('Telegram Login verification', () => {
  it('accepts a valid login', () => {
    expect(verifyTelegramLogin(login({ id: ADMIN, first_name: 'Ayaz', auth_date: NOW_S - 60 }), TOKEN, NOW_S))
      .toEqual({ ok: true, id: ADMIN });
  });
  it('rejects a tampered field', () => {
    const p = login({ id: ADMIN, auth_date: NOW_S });
    p.set('id', '1');
    expect(verifyTelegramLogin(p, TOKEN, NOW_S)).toMatchObject({ ok: false, reason: 'bad hash' });
  });
  it('rejects a login signed for another bot', () => {
    expect(verifyTelegramLogin(login({ id: ADMIN, auth_date: NOW_S }, 'other:token'), TOKEN, NOW_S).ok).toBe(false);
  });
  it('rejects logins older than one day', () => {
    expect(verifyTelegramLogin(login({ id: ADMIN, auth_date: NOW_S - 86_401 }), TOKEN, NOW_S))
      .toMatchObject({ ok: false, reason: 'login too old' });
  });
  it('rejects missing fields', () => {
    expect(verifyTelegramLogin(new URLSearchParams({ id: '1' }), TOKEN, NOW_S).ok).toBe(false);
  });
});

describe('Admin Session cookie', () => {
  const secret = 's3cret';
  it('round-trips for the Admin', () => {
    expect(readSession(signSession(ADMIN, secret, 0), secret, ADMIN, 1000)).toBe(true);
  });
  it('expires after 12 hours', () => {
    expect(readSession(signSession(ADMIN, secret, 0), secret, ADMIN, 12 * 3_600_000 + 1)).toBe(false);
  });
  it('rejects tampering, other users and other secrets', () => {
    const v = signSession(ADMIN, secret, 0);
    const [, exp, sig] = v.split('.');
    expect(readSession(`1.${exp}.${sig}`, secret, ADMIN, 1)).toBe(false);
    expect(readSession(signSession(1, secret, 0), secret, ADMIN, 1)).toBe(false);
    expect(readSession(v, 'other', ADMIN, 1)).toBe(false);
    expect(readSession('garbage', secret, ADMIN, 1)).toBe(false);
    expect(readSession(undefined, secret, ADMIN, 1)).toBe(false);
  });
});

const saved = { ...process.env };
afterEach(() => {
  process.env = { ...saved };
});

function configure() {
  process.env.BOT_TOKEN = TOKEN;
  process.env.SESSION_SECRET = 'session-secret';
  process.env.ADMIN_CHAT_ID = String(ADMIN);
}

describe('/admin/auth', () => {
  it('sets the cookie only for the Admin', async () => {
    configure();
    const now = Math.floor(Date.now() / 1000);
    const ok = await auth({ url: new URL(`https://x/admin/auth?${login({ id: ADMIN, auth_date: now })}`) } as never);
    expect(ok.status).toBe(302);
    expect(ok.headers.get('set-cookie')).toMatch(/^sr_admin=999\.\d+\.[0-9a-f]+; Path=\/admin; HttpOnly; Secure/);
    const other = await auth({ url: new URL(`https://x/admin/auth?${login({ id: 5, auth_date: now })}`) } as never);
    expect(other.status).toBe(403);
    expect(other.headers.get('set-cookie')).toBeNull();
  });
});

function ctx(path: string, cookie?: string) {
  return {
    request: new Request(`https://x${path}`),
    cookies: { get: (n: string) => (n === SESSION_COOKIE && cookie ? { value: cookie } : undefined) },
    redirect: (to: string, status: number) => new Response(null, { status, headers: { location: to } }),
  };
}
const ok = async () => new Response('page');

describe('Admin gate', () => {
  it('redirects pages and 401s the API without a session', async () => {
    configure();
    const page = (await adminGate(ctx('/admin') as never, ok)) as Response;
    expect(page.status).toBe(302);
    expect(page.headers.get('location')).toBe('/admin/login');
    expect(page.headers.get('x-robots-tag')).toContain('noindex');
    const api = (await adminGate(ctx('/admin/api/overview') as never, ok)) as Response;
    expect(api.status).toBe(401);
  });
  it('lets the Admin through', async () => {
    configure();
    const cookie = signSession(ADMIN, 'session-secret');
    const res = (await adminGate(ctx('/admin', cookie) as never, ok)) as Response;
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
  });
  it('leaves login open and other paths alone', async () => {
    configure();
    expect(((await adminGate(ctx('/admin/login') as never, ok)) as Response).status).toBe(200);
    expect(((await adminGate(ctx('/api/telegram') as never, ok)) as Response).headers.get('x-robots-tag')).toBeNull();
  });
  it('is closed when not configured', async () => {
    delete process.env.SESSION_SECRET;
    expect(((await adminGate(ctx('/admin') as never, ok)) as Response).status).toBe(503);
  });
});
