// Store Source contract (TypeScript side). Mirrors job/sources/base.py and
// validates against the same JSON Schemas in contracts/.

import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import listingSchema from '../../../contracts/listing.schema.json';

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
  store: 'google_play';
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
  getListing(pkg: string): Promise<Listing>;
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
