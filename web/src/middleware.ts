// Self-Metering for Vercel Usage (PLAN.md → Self-Metering): every request adds
// one invocation, its wall-clock duration and its response size to fn_metrics.

import { defineMiddleware } from 'astro:middleware';
import { meter, sqlFor } from './bot/repo';

export const onRequest = defineMiddleware(async (context, next) => {
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
