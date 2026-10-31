// Webhook Function: receives Telegram updates.
// Always answers 200 for authentic requests, so Telegram does not retry
// an update the bot already handled or cannot handle.

import type { APIRoute } from 'astro';
import { handleUpdate } from '../../bot/handlers';
import { PgRepo, sqlFor } from '../../bot/repo';
import type { TgUpdate } from '../../bot/types';
import { readEnv, safeEqual } from '../../lib/env';
import { TelegramApi } from '../../lib/telegram';
import { githubDispatcher } from '../../lib/dispatch';
import { signLoginToken } from '../../lib/session';
import { AppStoreLookup, NodeScraperLookup } from '../../sources/lookup';

export const POST: APIRoute = async ({ request }) => {
  const env = readEnv();
  if ('missing' in env) {
    console.error(`Webhook not configured; missing: ${env.missing.join(', ')}`);
    return new Response('Not configured', { status: 503 });
  }
  if (!safeEqual(request.headers.get('x-telegram-bot-api-secret-token'), env.webhookSecret)) {
    return new Response('Unauthorized', { status: 401 });
  }

  let update: TgUpdate;
  try {
    update = (await request.json()) as TgUpdate;
  } catch {
    return new Response('Bad request', { status: 400 });
  }

  const repo = new PgRepo(sqlFor(env.databaseUrl));
  try {
    await handleUpdate(update, {
      repo,
      tg: new TelegramApi(env.botToken),
      lookup: new NodeScraperLookup(),
      appStoreLookup: new AppStoreLookup(),
      adminChatId: env.adminChatId,
      loginLink: process.env.SESSION_SECRET
        ? () => `${process.env.PUBLIC_URL || new URL(request.url).origin}/admin/magic?t=${signLoginToken(env.adminChatId, process.env.SESSION_SECRET!)}`
        : undefined,
      dispatch: process.env.GITHUB_DISPATCH_TOKEN
        ? githubDispatcher(process.env.GITHUB_DISPATCH_TOKEN, process.env.GITHUB_REPO || 'qureshiayaz29/storerating-bot')
        : undefined,
    });
  } catch (e) {
    const err = e as Error;
    console.error('Update failed', update.update_id, err.message);
    await repo
      .logError(err.name === 'TelegramError' ? 'telegram' : 'web', null, `update ${update.update_id}: ${err.message}`, err.stack)
      .catch(() => {});
  }
  return new Response('ok');
};

export const GET: APIRoute = () => new Response('Method not allowed', { status: 405, headers: { allow: 'POST' } });
