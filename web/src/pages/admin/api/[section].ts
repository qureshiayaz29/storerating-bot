// Stats API: /admin/api/<section>. The Admin gate in middleware runs first.

import type { APIRoute } from 'astro';
import { section, SECTIONS, type Section } from '../../../admin/queries';
import { sqlFor } from '../../../bot/repo';

export const GET: APIRoute = async ({ params }) => {
  const s = params.section as Section;
  if (!SECTIONS.includes(s)) return Response.json({ error: 'unknown section', sections: SECTIONS }, { status: 404 });
  const url = process.env.DATABASE_URL;
  if (!url) return Response.json({ error: 'not configured' }, { status: 503 });
  return Response.json(await section(sqlFor(url), s));
};
