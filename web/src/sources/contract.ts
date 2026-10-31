// Store Source contract (TypeScript side). Mirrors job/sources/base.py and
// validates against the same JSON Schemas in contracts/.

import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import listingSchema from '../../../contracts/listing.schema.json';
import countries from '../../../contracts/countries.json';

export type Store = 'google_play' | 'app_store';

export type Reason = 'not_found' | 'rate_limited' | 'blocked' | 'parse_failed' | 'unavailable';

export class SourceError extends Error {
  constructor(
    readonly reason: Reason,
    readonly pkg: string,
    readonly detail = '',
  ) {
    super(`${reason}: ${pkg}${detail ? ` (${detail})` : ''}`);
    this.name = 'SourceError';
  }
}

export interface Listing {
  store: Store;
  package: string;
  name: string;
  developer: string | null;
  icon_url: string | null;
  version: string | null;
  rating: number | null;
  review_count: number | null;
  rating_histogram: [number, number, number, number, number] | null;
  installs_tier: string | null;
  size: string | null;
  updated_at: string | null;
  release_notes: string | null;
}

/** Subset of the Store Source contract the Webhook Function needs for /add. */
export interface LookupSource {
  readonly name: string;
  /** `id` is the package name (Play) or Track ID (App Store); `country` the Store Country. */
  getListing(id: string, country?: string): Promise<Listing>;
  exists(pkg: string): Promise<boolean>;
}

const ajv = new Ajv2020({ allErrors: false, strict: false });
addFormats(ajv);
const validate = ajv.compile<Listing>(listingSchema);

export function validateListing(listing: Listing): Listing {
  const pkg = listing.package;
  if (!validate(listing)) {
    const e = validate.errors?.[0];
    throw new SourceError('parse_failed', pkg, `listing ${e?.instancePath || '(root)'}: ${e?.message}`);
  }
  return listing;
}

/** Play Store package names: letters, digits, underscores; at least two dot-separated parts. */
export const PACKAGE_RE = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/;

/** Accepts a package name, a Play Store link or a market:// link. Returns null if none found. */
export function parsePackage(input: string): string | null {
  const text = input.trim();
  const fromUrl = text.match(/[?&]id=([A-Za-z0-9_.]+)/);
  const candidate = fromUrl ? fromUrl[1] : text.split(/\s+/)[0];
  return candidate && PACKAGE_RE.test(candidate) && candidate.length <= 150 ? candidate : null;
}

// --- Stores, countries and App Keys ------------------------------------------------

const COUNTRY_CODES = new Set<string>(countries.codes);

export function isCountryCode(code: string): boolean {
  return COUNTRY_CODES.has(code.trim().toLowerCase());
}

/** "India" for "in"; the upper-case code when the runtime has no name. */
export function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(['en'], { type: 'region' }).of(code.toUpperCase()) ?? code.toUpperCase();
  } catch {
    return code.toUpperCase();
  }
}

const APP_STORE_LINK = /(?:apps|itunes)\.apple\.com\/(?:([a-z]{2})\/)?(?:app\/)?(?:[^?#\s]*\/)?id(\d{3,12})/i;
const PLAY_LINK = /play\.google\.com|market:\/\//i;

/** Which store an /add input points at. Never asks the User. */
export function detectStore(input: string): Store | null {
  const text = input.trim();
  if (APP_STORE_LINK.test(text) || /^(id)?\d{6,12}$/i.test(text.split(/\s+/)[0] ?? '')) return 'app_store';
  if (PLAY_LINK.test(text) || parsePackage(text)) return 'google_play';
  return null;
}

export interface AppInput {
  store: Store;
  id: string; // package name or Track ID
  country: string | null; // Link Country, lower case
}

/** Store, store ID and Link Country from a link, package name or App Store ID. */
export function parseAppInput(input: string): AppInput | null {
  const text = input.trim();
  const store = detectStore(text);
  if (store === 'app_store') {
    const link = APP_STORE_LINK.exec(text);
    if (link) return { store, id: link[2], country: link[1]?.toLowerCase() ?? null };
    const bare = /^(?:id)?(\d{6,12})$/i.exec(text.split(/\s+/)[0] ?? '');
    return bare ? { store, id: bare[1], country: null } : null;
  }
  if (store === 'google_play') {
    const id = parsePackage(text);
    if (!id) return null;
    const gl = /[?&]gl=([a-z]{2})(?:&|$|#)/i.exec(text);
    return { store, id, country: gl && isCountryCode(gl[1]) ? gl[1].toLowerCase() : null };
  }
  return null;
}

export interface AppKey {
  store: Store;
  country: string;
  id: string;
}

export function appKey(store: Store, country: string, id: string): string {
  return `${store === 'app_store' ? 'ios' : 'play'}:${country.toLowerCase()}:${id}`;
}

/** Legacy keys without a prefix are US Play packages. */
export function parseAppKey(key: string): AppKey {
  const m = /^(play|ios):([a-z]{2}):(.+)$/.exec(key);
  if (m) return { store: m[1] === 'ios' ? 'app_store' : 'google_play', country: m[2], id: m[3] };
  return { store: 'google_play', country: 'us', id: key };
}

export function storeName(store: Store): string {
  return store === 'app_store' ? 'App Store' : 'Google Play';
}

/** "Google Play · IN" */
export function storeBadge(key: string): string {
  const k = parseAppKey(key);
  return `${storeName(k.store)} · ${k.country.toUpperCase()}`;
}
