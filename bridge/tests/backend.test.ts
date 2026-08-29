import assert from "node:assert/strict";

import type { NormalizedMessage } from "../src/normalize.js";

function message(overrides: Partial<NormalizedMessage> = {}): NormalizedMessage {
  return {
    chatJid: "6599999999@s.whatsapp.net",
    senderJid: "6599999999@s.whatsapp.net",
    messageId: "message-1",
    timestampMs: 1_700_000_000_000,
    fromMe: false,
    isGroup: false,
    text: "Hi is this available?",
    rawType: "conversation",
    ...overrides,
  };
}

const originalFetch = globalThis.fetch;
const originalBridgeToken = process.env.WHATSAPP_PA_BRIDGE_TOKEN;
const originalProsperBridgeToken = process.env.PROSPER_BRIDGE_TOKEN;
const calls: Array<{ url: string; init?: RequestInit }> = [];

delete process.env.WHATSAPP_PA_BRIDGE_TOKEN;
process.env.PROSPER_BRIDGE_TOKEN = "test-secret-token";

globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
  calls.push({ url: String(url), init });
  return new Response("{}", { status: 200 });
}) as typeof fetch;

const { fetchBridgeChatState, postInboundBatch, postInboundMessage } = await import("../src/backend.js");

function headersFor(call: { init?: RequestInit }): Headers {
  return new Headers(call.init?.headers);
}

try {
  await postInboundMessage(
    message({
      fromMe: true,
      senderJid: "me@s.whatsapp.net",
      messageId: "manual-1",
      text: "I replied manually.",
    }),
  );

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8000/api/bridge/inbound");
  assert.equal(headersFor(calls[0]).get("content-type"), "application/json");
  assert.equal(headersFor(calls[0]).get("x-whatsapp-bridge-token"), "test-secret-token");
  const singleBody = JSON.parse(String(calls[0].init?.body));
  assert.equal(singleBody.from_me, true);
  assert.equal(singleBody.sender_jid, "me@s.whatsapp.net");
  assert.equal(singleBody.message_id, "manual-1");
  assert.equal(singleBody.text, "I replied manually.");

  calls.length = 0;
  await postInboundBatch([
    message({ messageId: "tenant-1", fromMe: false, text: "Hi is this available?" }),
    message({
      fromMe: true,
      senderJid: "me@s.whatsapp.net",
      messageId: "manual-2",
      text: "I will handle this manually.",
      timestampMs: 1_700_000_001_000,
    }),
  ]);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8000/api/bridge/inbound-batch");
  assert.equal(headersFor(calls[0]).get("x-whatsapp-bridge-token"), "test-secret-token");
  const batchBody = JSON.parse(String(calls[0].init?.body));
  assert.deepEqual(
    batchBody.messages.map((item: { from_me: boolean; message_id: string }) => ({
      from_me: item.from_me,
      message_id: item.message_id,
    })),
    [
      { from_me: false, message_id: "tenant-1" },
      { from_me: true, message_id: "manual-2" },
    ],
  );

  calls.length = 0;
  let retryAttempts = 0;
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    retryAttempts += 1;
    calls.push({ url: String(url), init });
    if (retryAttempts === 1) {
      return new Response("database is locked", { status: 500 });
    }
    return new Response("{}", { status: 200 });
  }) as typeof fetch;

  await postInboundBatch([message({ messageId: "tenant-retry", fromMe: false })]);

  assert.equal(retryAttempts, 2);
  assert.equal(calls.length, 2);
  assert.equal(headersFor(calls[1]).get("x-whatsapp-bridge-token"), "test-secret-token");
  const retryBody = JSON.parse(String(calls[1].init?.body));
  assert.equal(retryBody.messages[0].message_id, "tenant-retry");

  calls.length = 0;
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify({ burst_mode: "active_conversation" }), { status: 200 });
  }) as typeof fetch;

  const chatState = await fetchBridgeChatState("6599999999@s.whatsapp.net");

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:8000/api/bridge/chat-state?chat_jid=6599999999%40s.whatsapp.net",
  );
  assert.equal(headersFor(calls[0]).get("x-whatsapp-bridge-token"), "test-secret-token");
  assert.equal(chatState.burst_mode, "active_conversation");
} finally {
  globalThis.fetch = originalFetch;
  if (originalBridgeToken === undefined) {
    delete process.env.WHATSAPP_PA_BRIDGE_TOKEN;
  } else {
    process.env.WHATSAPP_PA_BRIDGE_TOKEN = originalBridgeToken;
  }
  if (originalProsperBridgeToken === undefined) {
    delete process.env.PROSPER_BRIDGE_TOKEN;
  } else {
    process.env.PROSPER_BRIDGE_TOKEN = originalProsperBridgeToken;
  }
}

console.log("bridge backend forwarding tests passed");
