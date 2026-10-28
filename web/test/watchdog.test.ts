import { describe, expect, it } from 'vitest';
import { runWatchdog, githubWorkflows } from '../src/lib/watchdog';

const NOW = new Date('2026-10-29T01:30:00Z');
const hoursAgo = (h: number) => new Date(NOW.getTime() - h * 3_600_000);

function deps(last: Date | null, workflows: { id: number; name: string; state: string }[] = []) {
  const alerts: string[] = [];
  const enabled: number[] = [];
  return {
    alerts,
    enabled,
    d: {
      now: () => NOW,
      lastSuccess: async () => last,
      alert: async (t: string) => {
        alerts.push(t);
      },
      github: {
        list: async () => workflows,
        enable: async (id: number) => {
          enabled.push(id);
        },
      },
    },
  };
}

describe('Watchdog', () => {
  it('stays quiet when the daily job ran recently and workflows are active', async () => {
    const { d, alerts } = deps(hoursAgo(20), [{ id: 1, name: 'Daily job', state: 'active' }]);
    expect((await runWatchdog(d)).problems).toEqual([]);
    expect(alerts).toEqual([]);
  });

  it('alerts when the daily job is older than 26 hours', async () => {
    const { d, alerts } = deps(hoursAgo(27));
    await runWatchdog(d);
    expect(alerts[0]).toContain('has not succeeded for 27 hours');
  });

  it('alerts when the daily job never succeeded', async () => {
    const { d, alerts } = deps(null);
    await runWatchdog(d);
    expect(alerts[0]).toContain('never succeeded');
  });

  it('re-enables workflows disabled for inactivity and says so', async () => {
    const { d, alerts, enabled } = deps(hoursAgo(1), [
      { id: 1, name: 'Daily job', state: 'disabled_inactivity' },
      { id: 2, name: 'Deliver', state: 'active' },
      { id: 3, name: 'Old', state: 'disabled_manually' },
    ]);
    await runWatchdog(d);
    expect(enabled).toEqual([1]);
    expect(alerts[0]).toContain('Re-enabled workflows GitHub disabled for inactivity: Daily job');
  });

  it('reports a GitHub API failure instead of crashing', async () => {
    const { d, alerts } = deps(hoursAgo(1));
    d.github.list = async () => {
      throw new Error('list workflows: 401');
    };
    await runWatchdog(d);
    expect(alerts[0]).toContain('401');
  });

  it('GitHub client calls the right endpoints', async () => {
    const calls: [string, string][] = [];
    const fake = (async (url: string, init?: RequestInit) => {
      calls.push([init?.method ?? 'GET', url]);
      return url.endsWith('/enable')
        ? new Response(null, { status: 204 })
        : Response.json({ workflows: [{ id: 9, name: 'x', state: 'active' }] });
    }) as typeof fetch;
    const gh = githubWorkflows('t', 'o/r', fake);
    expect(await gh.list()).toHaveLength(1);
    await gh.enable(9);
    expect(calls).toEqual([
      ['GET', 'https://api.github.com/repos/o/r/actions/workflows?per_page=100'],
      ['PUT', 'https://api.github.com/repos/o/r/actions/workflows/9/enable'],
    ]);
  });
});
