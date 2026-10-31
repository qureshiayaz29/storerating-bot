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

// --- App Store -------------------------------------------------------------------

import { appKey, detectStore, isCountryCode, parseAppInput, parseAppKey, storeBadge } from '../src/sources/contract';
import { AppStoreLookup } from '../src/sources/lookup';
import { timezoneCountry } from '../src/lib/timezones';

const APPLE = resolve(here, '../../contracts/fixtures/app_store');
const appleCases = readdirSync(APPLE, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => ({ name: d.name, dir: join(APPLE, d.name), meta: read(join(APPLE, d.name, 'case.json')) }));

function appleFor(dir: string, urls: string[] = []) {
  return new AppStoreLookup(async (url) => {
    urls.push(url);
    return { ok: true, status: 200, json: async () => read(join(dir, 'lookup.json')) };
  });
}

describe('App Store Lookup matches the shared Contract Fixtures', () => {
  for (const c of appleCases.filter((c) => c.name !== 'removed')) {
    it(`${c.name}: same Listing as the Python adapter`, async () => {
      const urls: string[] = [];
      expect(await appleFor(c.dir, urls).getListing(c.meta.package, c.meta.country)).toEqual(read(join(c.dir, 'expected_listing.json')));
      expect(urls[0]).toContain(`country=${c.meta.country}`);
    });
  }
  it('removed: not_found, and exists() is false', async () => {
    const c = appleCases.find((c) => c.name === 'removed')!;
    await expect(appleFor(c.dir).getListing(c.meta.package, 'in')).rejects.toMatchObject({ reason: 'not_found' });
    expect(await appleFor(c.dir).exists(c.meta.package, 'in')).toBe(false);
  });
  it('maps HTTP errors', async () => {
    const failing = (status: number) => new AppStoreLookup(async () => ({ ok: false, status, json: async () => ({}) }));
    await expect(failing(429).getListing('1')).rejects.toMatchObject({ reason: 'rate_limited' });
    await expect(failing(503).getListing('1')).rejects.toMatchObject({ reason: 'unavailable' });
  });
});

describe('Store Detection and Link Country', () => {
  it.each([
    ['https://apps.apple.com/in/app/whatsapp-messenger/id310633997', 'app_store', '310633997', 'in'],
    ['https://apps.apple.com/app/id310633997', 'app_store', '310633997', null],
    ['https://itunes.apple.com/us/app/whatsapp-messenger/id310633997?mt=8', 'app_store', '310633997', 'us'],
    ['id310633997', 'app_store', '310633997', null],
    ['310633997', 'app_store', '310633997', null],
    ['com.whatsapp', 'google_play', 'com.whatsapp', null],
    ['https://play.google.com/store/apps/details?id=com.zerodha.kite3&gl=IN', 'google_play', 'com.zerodha.kite3', 'in'],
    ['https://play.google.com/store/apps/details?id=com.zerodha.kite3&hl=en_IN', 'google_play', 'com.zerodha.kite3', null],
    ['market://details?id=com.whatsapp', 'google_play', 'com.whatsapp', null],
  ])('%s', (input, store, id, country) => {
    expect(detectStore(input)).toBe(store);
    expect(parseAppInput(input)).toEqual({ store, id, country });
  });
  it.each(['whatsapp', 'hello world', '12345', 'https://example.com/app'])('rejects %s', (input) => {
    expect(detectStore(input)).toBeNull();
    expect(parseAppInput(input)).toBeNull();
  });
  it('App Keys, badges, countries and timezones', () => {
    expect(appKey('app_store', 'IN', '310633997')).toBe('ios:in:310633997');
    expect(parseAppKey('play:gb:com.whatsapp')).toEqual({ store: 'google_play', country: 'gb', id: 'com.whatsapp' });
    expect(parseAppKey('com.legacy')).toEqual({ store: 'google_play', country: 'us', id: 'com.legacy' });
    expect(storeBadge('ios:us:1')).toBe('App Store · US');
    expect(isCountryCode('IN') && isCountryCode('gb') && !isCountryCode('xx') && !isCountryCode('india')).toBe(true);
    expect([timezoneCountry('Asia/Kolkata'), timezoneCountry('Asia/Calcutta'), timezoneCountry('Europe/London'), timezoneCountry('UTC')])
      .toEqual(['in', 'in', 'gb', null]);
  });
});
