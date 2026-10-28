// Server-side environment. Values come from Vercel environment variables.

export interface Env {
  botToken: string;
  webhookSecret: string;
  adminChatId: number;
  databaseUrl: string;
}

/** Returns the env, or the list of missing variable names. */
export function readEnv(source: Record<string, string | undefined> = process.env): Env | { missing: string[] } {
  const names = ['BOT_TOKEN', 'WEBHOOK_SECRET', 'ADMIN_CHAT_ID', 'DATABASE_URL'] as const;
  const missing = names.filter((n) => !source[n]);
  if (missing.length) return { missing };
  const adminChatId = Number(source.ADMIN_CHAT_ID);
  if (!Number.isSafeInteger(adminChatId)) return { missing: ['ADMIN_CHAT_ID (not a number)'] };
  return {
    botToken: source.BOT_TOKEN!,
    webhookSecret: source.WEBHOOK_SECRET!,
    adminChatId,
    databaseUrl: source.DATABASE_URL!,
  };
}

/** Constant-time string comparison for secrets. */
export function safeEqual(a: string | null | undefined, b: string): boolean {
  if (typeof a !== 'string' || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
