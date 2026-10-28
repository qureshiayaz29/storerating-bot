// 1. Admin gate (src/lib/admin-gate.ts): every /admin page and Stats API route needs an Admin Session.
// 2. Self-Metering for Vercel Usage: invocations, duration and response size → fn_metrics.

import { defineMiddleware, sequence } from 'astro:middleware';
import { meter, sqlFor } from './bot/repo';
import { adminGate } from './lib/admin-gate';

export const metering = defineMiddleware(async (context, next) => {
  const started = performance.now();
  const response = await next();
  const url = process.env.DATABASE_URL;
  if (url && !context.isPrerendered) {
    const route = context.routePattern || new URL(context.request.url).pathname;
    const bytes = Number(response.headers.get('content-length') ?? 0) || 0;
    try {
      await meter(sqlFor(url), route, performance.now() - started, bytes);
    } catch (e) {
      console.error('Metering failed', (e as Error).message);
    }
  }
  return response;
});

export const onRequest = sequence(metering, defineMiddleware((ctx, next) => adminGate(ctx, next)));
