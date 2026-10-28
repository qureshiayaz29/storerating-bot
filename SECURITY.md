# Security

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report it privately through GitHub: **Security → Report a vulnerability** on this repository. Include what you found, how to reproduce it, and the impact you expect.

You'll get a reply within a few days. Once fixed, you'll be credited in the release notes unless you prefer otherwise.

## Scope

- The Telegram bot `@storeratingbot`
- The admin dashboard and Stats API
- Code in this repository

## How data is protected

- Secrets live in GitHub repository secrets and Vercel environment variables, never in code.
- Every database table has Row Level Security enabled with no policies; the app uses a dedicated least-privilege role.
- Telegram webhook requests are verified with a secret token.
- Admin commands and the dashboard are restricted to a single Telegram account.
- User data is limited to chat ID, timezone, settings and watchlist. `/delete` removes it all.
