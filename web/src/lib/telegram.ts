// Minimal Telegram Bot API client. The token never appears in errors or logs.

export interface InlineButton {
  text: string;
  callback_data: string;
}
export type Keyboard = InlineButton[][];

export interface Telegram {
  sendMessage(chatId: number, text: string, keyboard?: Keyboard): Promise<void>;
  editMessage(chatId: number, messageId: number, text: string, keyboard?: Keyboard): Promise<void>;
  answerCallback(callbackId: string, text?: string): Promise<void>;
  sendPhoto(chatId: number, fileId: string, caption: string): Promise<void>;
}

export class TelegramError extends Error {
  constructor(
    readonly method: string,
    readonly status: number,
    readonly description: string,
  ) {
    super(`Telegram ${method} failed: ${status} ${description}`);
    this.name = 'TelegramError';
  }
}

export class TelegramApi implements Telegram {
  constructor(
    private readonly token: string,
    private readonly fetchFn: typeof fetch = fetch,
  ) {}

  private async call(method: string, body: Record<string, unknown>): Promise<void> {
    const res = await this.fetchFn(`https://api.telegram.org/bot${this.token}/${method}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = (await res.json().catch(() => ({}))) as { description?: string };
      // "message is not modified" is harmless when a button is tapped twice.
      if (res.status === 400 && /not modified/i.test(data.description ?? '')) return;
      throw new TelegramError(method, res.status, data.description ?? 'unknown');
    }
  }

  sendMessage(chatId: number, text: string, keyboard?: Keyboard) {
    return this.call('sendMessage', {
      chat_id: chatId,
      text,
      parse_mode: 'HTML',
      link_preview_options: { is_disabled: true },
      ...(keyboard ? { reply_markup: { inline_keyboard: keyboard } } : {}),
    });
  }

  editMessage(chatId: number, messageId: number, text: string, keyboard?: Keyboard) {
    return this.call('editMessageText', {
      chat_id: chatId,
      message_id: messageId,
      text,
      parse_mode: 'HTML',
      link_preview_options: { is_disabled: true },
      reply_markup: { inline_keyboard: keyboard ?? [] },
    });
  }

  sendPhoto(chatId: number, fileId: string, caption: string) {
    return this.call('sendPhoto', { chat_id: chatId, photo: fileId, caption, parse_mode: 'HTML' });
  }

  answerCallback(callbackId: string, text?: string) {
    return this.call('answerCallbackQuery', { callback_query_id: callbackId, ...(text ? { text } : {}) });
  }
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
