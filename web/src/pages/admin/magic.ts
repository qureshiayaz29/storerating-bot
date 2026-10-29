// Login Link from the bot (/login): verifies the 5-minute signed token and issues an Admin Session.

import type { APIRoute } from 'astro';
import { sessionCookie, signSession, verifyLoginToken } from '../../lib/session';

const page = (title: string, body: string, status: number) =>
  new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${title}</title>` +
      `<body style="font-family:system-ui;padding:40px 16px;text-align:center"><h1>${title}</h1><p>${body}</p></body>`,
    { status, headers: { 'content-type': 'text/html; charset=utf-8' } },
  );

export const GET: APIRoute = async ({ url }) => {
  const secret = process.env.SESSION_SECRET;
  const adminId = Number(process.env.ADMIN_CHAT_ID);
  if (!secret || !Number.isSafeInteger(adminId)) return new Response('Dashboard not configured', { status: 503 });
  if (!verifyLoginToken(url.searchParams.get('t'), secret, adminId)) {
    return page('Link expired', 'Send /login to the bot again for a new link.', 403);
  }
  return new Response(null, {
    status: 302,
    headers: { location: '/admin', 'set-cookie': sessionCookie(signSession(adminId, secret)) },
  });
};
