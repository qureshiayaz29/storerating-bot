import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { SourceError, parsePackage, validateListing, type Listing } from '../src/sources/contract';
import { NodeScraperLookup, reasonFor, type NodeApp } from '../src/sources/lookup';

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURES = resolve(here, '../../contracts/fixtures/google_play_scraper_node');
const read = (p: string) => JSON.parse(readFileSync(p, 'utf8'));
const cases = readdirSync(FIXTURES, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => ({ name: d.name, dir: join(FIXTURES, d.name), meta: read(join(FIXTURES, d.name, 'case.json')) }));

function adapterFor(dir: string, meta: { raises?: { message: string; status?: number } }) {
  return new NodeScraperLookup(async () => {
    if (meta.raises) throw Object.assign(new Error(meta.raises.message), { status: meta.raises.status });
    return read(join(dir, 'app.json')) as NodeApp;
  });
}

describe('Lookup Adapter matches the shared Contract Fixtures', () => {
  for (const c of cases.filter((c) => !c.meta.raises)) {
    it(`${c.name}: same Listing as the Python adapter`, async () => {
      const listing = await adapterFor(c.dir, c.meta).getListing(c.meta.package);
      expect(listing).toEqual(read(join(c.dir, c.meta.expected)));
      expect(await adapterFor(c.dir, c.meta).exists(c.meta.package)).toBe(true);
    });
  }
  for (const c of cases.filter((c) => c.meta.raises)) {
    it(`${c.name}: raises ${c.meta.expected_reason}`, async () => {
      const adapter = adapterFor(c.dir, c.meta);
      await expect(adapter.getListing(c.meta.package)).rejects.toMatchObject({ reason: c.meta.expected_reason });
      expect(await adapter.exists(c.meta.package)).toBe(false);
    });
  }
});

describe('error mapping', () => {
  it.each<[{ message: string; status?: number }, string]>([
    [{ message: 'App not found (404)', status: 404 }, 'not_found'],
    [{ message: 'Error requesting Google Play:Response code 429', status: 429 }, 'rate_limited'],
    [{ message: 'Error requesting Google Play:Forbidden', status: 403 }, 'blocked'],
    [{ message: 'socket hang up' }, 'unavailable'],
  ])('%o → %s', (err, reason) => {
    expect(reasonFor(Object.assign(new Error(err.message), { status: err.status }))).toBe(reason);
  });

  it('TypeError from a page change → parse_failed', () => {
    expect(reasonFor(new TypeError("Cannot read properties of undefined (reading '1')"))).toBe('parse_failed');
  });

  it('missing title → parse_failed', async () => {
    const adapter = new NodeScraperLookup(async () => ({ appId: 'com.example.app' }));
    await expect(adapter.getListing('com.example.app')).rejects.toMatchObject({ reason: 'parse_failed' });
  });

  it('schema rejects an out-of-range rating', () => {
    const good = read(join(FIXTURES, '../google_play_scraper/large/expected_listing.json')) as Listing;
    expect(() => validateListing({ ...good, rating: 7 })).toThrow(SourceError);
  });
});

describe('parsePackage', () => {
  it.each([
    ['com.whatsapp', 'com.whatsapp'],
    ['  com.whatsapp  ', 'com.whatsapp'],
    ['https://play.google.com/store/apps/details?id=com.whatsapp&hl=en_IN', 'com.whatsapp'],
    ['https://play.google.com/store/apps/details?hl=en&id=in.upstox.app', 'in.upstox.app'],
    ['market://details?id=org.fdroid.fdroid', 'org.fdroid.fdroid'],
    ['Check this https://play.google.com/store/apps/details?id=com.example.app_2', 'com.example.app_2'],
    ['whatsapp', null],
    ['https://example.com', null],
    ['com..bad', null],
    ['1com.bad', null],
  ])('%s → %s', (input, expected) => {
    expect(parsePackage(input)).toBe(expected);
  });
});
