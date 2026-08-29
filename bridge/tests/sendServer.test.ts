import assert from "node:assert/strict";

import { assertBridgeStartupSafe, bridgeTokenMatches, handleSendPayload, type SendSocket } from "../src/sendServer.js";

type SendCall = {
  chatJid: string;
  payload: unknown;
};

type PresenceCall = {
  type: "composing" | "paused";
  chatJid: string;
};

function fakeSocket(calls: SendCall[], presenceCalls: PresenceCall[] = []): SendSocket {
  return {
    async sendMessage(chatJid, payload) {
      calls.push({ chatJid, payload });
      return { key: { id: `msg-${calls.length}` } };
    },
    async sendPresenceUpdate(type, chatJid) {
      presenceCalls.push({ type, chatJid });
    },
  };
}

{
  const calls: SendCall[] = [];
  const presenceCalls: PresenceCall[] = [];
  const result = await handleSendPayload(fakeSocket(calls, presenceCalls), {
    chat_jid: " tenant@s.whatsapp.net ",
    text: " Hi yes available ",
  });

  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body, { ok: true, message_id: "msg-1" });
  assert.deepEqual(calls, [{ chatJid: "tenant@s.whatsapp.net", payload: { text: "Hi yes available" } }]);
  assert.deepEqual(presenceCalls, [
    { type: "composing", chatJid: "tenant@s.whatsapp.net" },
    { type: "paused", chatJid: "tenant@s.whatsapp.net" },
  ]);
}

{
  const calls: SendCall[] = [];
  const result = await handleSendPayload(fakeSocket(calls), {
    chat_jid: "tenant@s.whatsapp.net",
    media_type: "photo",
    file_path: "/Users/example/living-room.jpg",
    caption: "Living room",
  });

  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body, { ok: true, message_id: "msg-1" });
  assert.deepEqual(calls, [
    {
      chatJid: "tenant@s.whatsapp.net",
      payload: { image: { url: "/Users/example/living-room.jpg" } },
    },
  ]);
}

{
  const calls: SendCall[] = [];
  const result = await handleSendPayload(
    {
      async sendMessage(chatJid, payload) {
        calls.push({ chatJid, payload });
        return { key: { id: "msg-no-presence" } };
      },
    },
    {
      chat_jid: "tenant@s.whatsapp.net",
      text: "No presence socket",
    },
  );

  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body, { ok: true, message_id: "msg-no-presence" });
  assert.deepEqual(calls, [{ chatJid: "tenant@s.whatsapp.net", payload: { text: "No presence socket" } }]);
}

{
  const calls: SendCall[] = [];
  const result = await handleSendPayload(fakeSocket(calls), {
    chat_jid: "tenant@s.whatsapp.net",
    media_type: "video",
    file_path: "/Users/example/walkthrough.mp4",
    caption: "Walkthrough",
  });

  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body, { ok: true, message_id: "msg-1" });
  assert.deepEqual(calls, [
    {
      chatJid: "tenant@s.whatsapp.net",
      payload: { video: { url: "/Users/example/walkthrough.mp4" } },
    },
  ]);
}

{
  const calls: SendCall[] = [];
  const result = await handleSendPayload(fakeSocket(calls), {
    text: "Missing chat",
  });

  assert.equal(result.statusCode, 400);
  assert.deepEqual(result.body, { error: "chat_jid is required" });
  assert.deepEqual(calls, []);
}

{
  const calls: SendCall[] = [];
  const result = await handleSendPayload(fakeSocket(calls), {
    chat_jid: "tenant@s.whatsapp.net",
    media_type: "document",
    file_path: "/Users/example/file.pdf",
  });

  assert.equal(result.statusCode, 400);
  assert.deepEqual(result.body, { error: "text or valid media_type/file_path is required" });
  assert.deepEqual(calls, []);
}

{
  assert.equal(bridgeTokenMatches("secret-token", "secret-token"), true);
  assert.equal(bridgeTokenMatches("", "secret-token"), false);
  assert.equal(bridgeTokenMatches("wrong-token", "secret-token"), false);
  assert.equal(bridgeTokenMatches("short", "secret-token"), false);
  assert.doesNotThrow(() => assertBridgeStartupSafe("127.0.0.1", ""));
  assert.doesNotThrow(() => assertBridgeStartupSafe("0.0.0.0", "configured-token"));
  assert.throws(
    () => assertBridgeStartupSafe("0.0.0.0", ""),
    /PROSPER_BRIDGE_TOKEN is required/,
  );
}

console.log("bridge send server tests passed");
