import { BACKEND_BASE_URL, WHATSAPP_ACCOUNT_ID, WHATSAPP_BRIDGE_TOKEN } from "./config.js";
import type { NormalizedMessage } from "./normalize.js";

const RETRY_DELAYS_MS = [500, 1_000, 2_000, 4_000, 8_000];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function shouldRetry(status: number): boolean {
  return status === 408 || status === 409 || status === 423 || status === 429 || status >= 500;
}

function bridgeAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (WHATSAPP_ACCOUNT_ID) {
    headers["x-whatsapp-account-id"] = WHATSAPP_ACCOUNT_ID;
  }
  if (WHATSAPP_BRIDGE_TOKEN) {
    headers["x-whatsapp-bridge-token"] = WHATSAPP_BRIDGE_TOKEN;
  }
  return headers;
}

async function postJsonWithRetry(path: string, body: unknown, label: string): Promise<void> {
  let lastError = "";
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
    try {
      const response = await fetch(`${BACKEND_BASE_URL}${path}`, {
        method: "POST",
        headers: { "content-type": "application/json", ...bridgeAuthHeaders() },
        body: JSON.stringify(body),
      });

      if (response.ok) return;

      const text = await response.text();
      lastError = `${response.status} ${text}`;
      if (!shouldRetry(response.status) || attempt === RETRY_DELAYS_MS.length) {
        throw new Error(`Backend rejected ${label}: ${lastError}`);
      }
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
      if (attempt === RETRY_DELAYS_MS.length) {
        throw new Error(`Backend rejected ${label}: ${lastError}`);
      }
    }
    await sleep(RETRY_DELAYS_MS[attempt]);
  }
}

export async function postInboundMessage(message: NormalizedMessage): Promise<void> {
  await postJsonWithRetry(
    "/api/bridge/inbound",
    {
      chat_jid: message.chatJid,
      sender_jid: message.senderJid,
      message_id: message.messageId,
      timestamp_ms: message.timestampMs,
      from_me: message.fromMe,
      text: message.text,
      raw_type: message.rawType,
    },
    "inbound message",
  );
}

export async function postInboundBatch(messages: NormalizedMessage[]): Promise<void> {
  await postJsonWithRetry(
    "/api/bridge/inbound-batch",
    {
      messages: messages.map((message) => ({
        chat_jid: message.chatJid,
        sender_jid: message.senderJid,
        message_id: message.messageId,
        timestamp_ms: message.timestampMs,
        from_me: message.fromMe,
        text: message.text,
        raw_type: message.rawType,
      })),
    },
    "inbound message batch",
  );
}

export async function fetchBridgeChatState(chatJid: string): Promise<{ burst_mode: string; wait_ms?: number }> {
  const url = `${BACKEND_BASE_URL}/api/bridge/chat-state?chat_jid=${encodeURIComponent(chatJid)}`;
  const response = await fetch(url, { headers: bridgeAuthHeaders() });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Backend rejected chat state lookup: ${response.status} ${text}`);
  }
  return (await response.json()) as { burst_mode: string; wait_ms?: number };
}
