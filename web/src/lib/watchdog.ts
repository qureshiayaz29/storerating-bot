// Watchdog (PLAN.md → Watchdog): runs daily from a Vercel cron.
// Alerts the Admin when the Daily Job has not succeeded for 26 hours, and
// re-enables any workflow GitHub disabled for inactivity.

export interface GitHubWorkflows {
  list(): Promise<{ id: number; name: string; state: string }[]>;
  enable(id: number): Promise<void>;
}

export interface WatchdogDeps {
  lastSuccess(workflow: string): Promise<Date | null>;
  github?: GitHubWorkflows;
  alert(text: string): Promise<void>;
  now?: () => Date;
}

export const STALE_HOURS = 26;

export async function runWatchdog(deps: WatchdogDeps) {
  const now = deps.now?.() ?? new Date();
  const problems: string[] = [];

  const last = await deps.lastSuccess('daily');
  const ageHours = last ? (now.getTime() - last.getTime()) / 3_600_000 : Infinity;
  if (ageHours > STALE_HOURS) {
    problems.push(
      last
        ? `🐕 Daily job has not succeeded for ${Math.floor(ageHours)} hours (last: ${last.toISOString().slice(0, 16)} UTC).`
        : '🐕 Daily job has never succeeded yet.',
    );
  }

  const reenabled: string[] = [];
  if (deps.github) {
    try {
      for (const wf of await deps.github.list()) {
        if (wf.state === 'disabled_inactivity') {
          await deps.github.enable(wf.id);
          reenabled.push(wf.name);
        }
      }
    } catch (e) {
      problems.push(`🐕 Could not check GitHub workflows: ${(e as Error).message}`);
    }
  }
  if (reenabled.length) problems.push(`🐕 Re-enabled workflows GitHub disabled for inactivity: ${reenabled.join(', ')}`);

  if (problems.length) await deps.alert(problems.join('\n'));
  return { dailyAgeHours: Number.isFinite(ageHours) ? Math.round(ageHours * 10) / 10 : null, reenabled, problems };
}

export function githubWorkflows(token: string, repo: string, fetchFn: typeof fetch = fetch): GitHubWorkflows {
  const headers = {
    authorization: `Bearer ${token}`,
    accept: 'application/vnd.github+json',
    'x-github-api-version': '2022-11-28',
    'user-agent': 'storerating-bot',
  };
  return {
    async list() {
      const res = await fetchFn(`https://api.github.com/repos/${repo}/actions/workflows?per_page=100`, { headers });
      if (!res.ok) throw new Error(`list workflows: ${res.status}`);
      const data = (await res.json()) as { workflows: { id: number; name: string; state: string }[] };
      return data.workflows;
    },
    async enable(id: number) {
      const res = await fetchFn(`https://api.github.com/repos/${repo}/actions/workflows/${id}/enable`, { method: 'PUT', headers });
      if (res.status !== 204) throw new Error(`enable workflow ${id}: ${res.status}`);
    },
  };
}
