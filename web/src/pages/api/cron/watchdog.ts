// Vercel cron: daily Watchdog. Vercel sends "Authorization: Bearer <CRON_SECRET>".

import type { APIRoute } from 'astro';
import { sqlFor } from '../../../bot/repo';
import { readEnv, safeEqual } from '../../../lib/env';
import { TelegramApi } from '../../../lib/telegram';
import { githubWorkflows, runWatchdog } from '../../../lib/watchdog';

export const GET: APIRoute = async ({ request }) => {
  const secret = process.env.CRON_SECRET;
  const env = readEnv();
  if (!secret || 'missing' in env) return new Response('Not configured', { status: 503 });
  if (!safeEqual(request.headers.get('authorization'), `Bearer ${secret}`)) {
    return new Response('Unauthorized', { status: 401 });
  }
  const sql = sqlFor(env.databaseUrl);
  const tg = new TelegramApi(env.botToken);
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  const result = await runWatchdog({
    async lastSuccess(workflow) {
      const rows = await sql<{ at: Date }[]>`
        select max(finished_at) as at from job_runs where workflow = ${workflow} and success`;
      return rows[0]?.at ?? null;
    },
    github: token ? githubWorkflows(token, process.env.GITHUB_REPO || 'qureshiayaz29/storerating-bot') : undefined,
    alert: (text) => tg.sendMessage(env.adminChatId, text),
  });
  return Response.json(result);
};
