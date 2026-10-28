import type { APIRoute } from 'astro';
import { CLEAR_COOKIE } from '../../lib/session';

export const GET: APIRoute = () =>
  new Response(null, { status: 302, headers: { location: '/admin/login', 'set-cookie': CLEAR_COOKIE } });
