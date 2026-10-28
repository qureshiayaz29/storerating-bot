// Lookup Adapter: Store Source exists/getListing for the Webhook Function,
// built on facundoolano's google-play-scraper (Node). Same Listing as the
// Python adapter for the same app — enforced by the shared Contract Fixtures.

import { SourceError, validateListing, type Listing, type LookupSource, type Reason } from './contract';

/** Shape of the fields we read from google-play-scraper's app(). */
export interface NodeApp {
  appId?: string;
  title?: string;
  developer?: string | null;
  icon?: string | null;
  version?: string | null;
  score?: number | null;
  ratings?: number | null;
  histogram?: Record<'1' | '2' | '3' | '4' | '5', number | null> | null;
  installs?: string | null;
  size?: string | null;
  updated?: number | null; // milliseconds
  recentChanges?: string | null;
}

export type AppFn = (opts: { appId: string; lang?: string; country?: string }) => Promise<NodeApp>;

async function defaultAppFn(): Promise<AppFn> {
  const gplay = (await import('google-play-scraper')).default as { app: AppFn };
  return gplay.app;
}

export class NodeScraperLookup implements LookupSource {
  readonly name = 'google_play_scraper_node';

  constructor(
    private readonly appFn?: AppFn,
    private readonly lang = 'en',
    private readonly country = 'us',
  ) {}

  async getListing(pkg: string): Promise<Listing> {
    let raw: NodeApp;
    try {
      const fn = this.appFn ?? (await defaultAppFn());
      raw = await fn({ appId: pkg, lang: this.lang, country: this.country });
    } catch (e) {
      throw new SourceError(reasonFor(e), pkg, String((e as Error)?.message ?? e).slice(0, 300));
    }
    return validateListing(toListing(pkg, raw));
  }

  async exists(pkg: string): Promise<boolean> {
    try {
      await this.getListing(pkg);
      return true;
    } catch (e) {
      if (e instanceof SourceError && e.reason === 'not_found') return false;
      throw e;
    }
  }
}

export function toListing(pkg: string, raw: NodeApp): Listing {
  if (!raw || typeof raw.title !== 'string' || !raw.title) {
    throw new SourceError('parse_failed', pkg, 'missing title');
  }
  return {
    store: 'google_play',
    package: raw.appId || pkg,
    name: raw.title,
    developer: raw.developer ?? null,
    icon_url: raw.icon ?? null,
    version: normaliseVersion(raw.version),
    rating: raw.score == null ? null : Math.round(raw.score * 100) / 100,
    review_count: raw.ratings == null ? null : Math.trunc(raw.ratings),
    rating_histogram: histogram(raw.histogram),
    installs_tier: raw.installs ?? null,
    size: raw.size || null,
    updated_at: raw.updated == null ? null : isoNoMillis(raw.updated),
    release_notes: raw.recentChanges || null,
  };
}

function normaliseVersion(v: string | null | undefined): string | null {
  if (!v) return null;
  return v === 'VARY' ? 'Varies with device' : v;
}

function histogram(h: NodeApp['histogram']): Listing['rating_histogram'] {
  if (!h) return null;
  const keys = ['1', '2', '3', '4', '5'] as const;
  return keys.map((k) => Math.trunc(h[k] ?? 0)) as Listing['rating_histogram'];
}

/** Matches Python's datetime.isoformat() for whole seconds: 2026-10-28T00:00:00+00:00 */
function isoNoMillis(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, '+00:00');
}

export function reasonFor(e: unknown): Reason {
  const err = e as { status?: number; message?: string; name?: string };
  const msg = String(err?.message ?? '');
  if (err?.status === 404 || /not found/i.test(msg)) return 'not_found';
  if (err?.status === 429 || /\b429\b/.test(msg)) return 'rate_limited';
  if (err?.status === 403 || /\b403\b|forbidden/i.test(msg)) return 'blocked';
  if (e instanceof TypeError || e instanceof RangeError) return 'parse_failed';
  return 'unavailable';
}
