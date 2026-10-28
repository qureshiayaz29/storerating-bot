import { afterEach, describe, expect, it } from 'vitest';
import { GET, POST } from '../src/pages/api/telegram';
import { readEnv, safeEqual } from '../src/lib/env';

const call = (headers: Record<string, string> = {}, body = '{}') =>
  // The route only reads `request`; the rest of the Astro context is unused.
  POST({ request: new Request('https://x/api/telegram', { method: 'POST', headers, body }) } as never);

const saved = { ...process.env };
afterEach(() => {
  process.env = { ...saved };
});

function configure() {
  process.env.BOT_TOKEN = 'token';
  process.env.WEBHOOK_SECRET = 'correct-secret';
  process.env.ADMIN_CHAT_ID = '999';
  process.env.DATABASE_URL = 'postgres://user:pass@127.0.0.1:1/db';
}

describe('Webhook Function', () => {
  it('returns 503 until the environment is configured', async () => {
    for (const k of ['BOT_TOKEN', 'WEBHOOK_SECRET', 'ADMIN_CHAT_ID', 'DATABASE_URL']) delete process.env[k];
    expect((await call()).status).toBe(503);
  });

  it('returns 401 without the secret header', async () => {
    configure();
    expect((await call()).status).toBe(401);
  });

  it('returns 401 with a wrong secret', async () => {
    configure();
    expect((await call({ 'x-telegram-bot-api-secret-token': 'wrong-secret!!' })).status).toBe(401);
  });

  it('returns 400 for a body that is not JSON', async () => {
    configure();
    expect((await call({ 'x-telegram-bot-api-secret-token': 'correct-secret' }, 'nope')).status).toBe(400);
  });

  it('GET is not allowed', async () => {
    expect((await GET({} as never)).status).toBe(405);
  });
});

describe('env helpers', () => {
  it('reports missing variables by name', () => {
    expect(readEnv({})).toEqual({ missing: ['BOT_TOKEN', 'WEBHOOK_SECRET', 'ADMIN_CHAT_ID', 'DATABASE_URL'] });
  });

  it('rejects a non-numeric ADMIN_CHAT_ID', () => {
    const env = readEnv({ BOT_TOKEN: 't', WEBHOOK_SECRET: 's', ADMIN_CHAT_ID: 'me', DATABASE_URL: 'd' });
    expect(env).toEqual({ missing: ['ADMIN_CHAT_ID (not a number)'] });
  });

  it('safeEqual compares exactly', () => {
    expect(safeEqual('abc', 'abc')).toBe(true);
    expect(safeEqual('abd', 'abc')).toBe(false);
    expect(safeEqual('ab', 'abc')).toBe(false);
    expect(safeEqual(null, 'abc')).toBe(false);
  });
});
