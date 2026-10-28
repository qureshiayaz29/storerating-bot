// Starts an On-demand Run through GitHub's repository_dispatch API.
// The token is fine-grained: this repository only, Actions read and write.

export function githubDispatcher(token: string, repo: string, fetchFn: typeof fetch = fetch) {
  return async function dispatch(): Promise<void> {
    const res = await fetchFn(`https://api.github.com/repos/${repo}/dispatches`, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${token}`,
        accept: 'application/vnd.github+json',
        'x-github-api-version': '2022-11-28',
        'user-agent': 'storerating-bot',
      },
      body: JSON.stringify({ event_type: 'on-demand' }),
    });
    if (res.status !== 204) throw new Error(`repository_dispatch failed: ${res.status}`);
  };
}
