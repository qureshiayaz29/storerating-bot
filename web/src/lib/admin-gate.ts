// Admin gate: every /admin page and Stats API route needs an Admin Session.
// Plain function so tests can call it without Astro's runtime.

import { readSession, SESSION_COOKIE } from './session';

interface GateContext {
  request: Request;
  cookies: { get(name: string): { value: string } | undefined };
  redirect(path: string, status?: 301 | 302 | 303 | 307 | 308 | 300 | 304): Response;
}

const OPEN_ADMIN = new Set(['/admin/login', '/admin/auth', '/admin/logout', '/admin/magic']);

export async function adminGate(context: GateContext, next: () => Promise<Response>): Promise<Response> {
  const path = new URL(context.request.url).pathname.replace(/\/$/, '') || '/';
  if (!path.startsWith('/admin')) return next();

  let response: Response;
  if (OPEN_ADMIN.has(path)) {
    response = await next();
  } else {
    const secret = process.env.SESSION_SECRET;
    const adminId = Number(process.env.ADMIN_CHAT_ID);
    if (!secret || !Number.isSafeInteger(adminId)) {
      response = new Response('Dashboard not configured', { status: 503 });
    } else if (!readSession(context.cookies.get(SESSION_COOKIE)?.value, secret, adminId)) {
      response = path.startsWith('/admin/api')
        ? Response.json({ error: 'unauthorized' }, { status: 401 })
        : context.redirect('/admin/login', 302);
    } else {
      response = await next();
    }
  }
  const headers = new Headers(response.headers);
  headers.set('x-robots-tag', 'noindex, nofollow');
  headers.set('cache-control', 'no-store');
  headers.set('x-frame-options', 'DENY');
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}
