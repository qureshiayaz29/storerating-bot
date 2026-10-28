// Telegram Login callback. Issues an Admin Session only to ADMIN_CHAT_ID.

import type { APIRoute } from 'astro';
import { sessionCookie, signSession, verifyTelegramLogin } from '../../lib/session';

export const GET: APIRoute = async ({ url }) => {
  const token = process.env.BOT_TOKEN;
  const secret = process.env.SESSION_SECRET;
  const adminId = Number(process.env.ADMIN_CHAT_ID);
  if (!token || !secret || !Number.isSafeInteger(adminId)) return new Response('Dashboard not configured', { status: 503 });

  const result = verifyTelegramLogin(url.searchParams, token);
  if (!result.ok || result.id !== adminId) {
    return new Response(
      '<!doctype html><meta charset="utf-8"><title>Not allowed</title><body style="font-family:system-ui;padding:40px">' +
        '<h1>Not allowed</h1><p>This dashboard is private.</p></body>',
      { status: 403, headers: { 'content-type': 'text/html; charset=utf-8' } },
    );
  }
  return new Response(null, {
    status: 302,
    headers: { location: '/admin', 'set-cookie': sessionCookie(signSession(adminId, secret)) },
  });
};
