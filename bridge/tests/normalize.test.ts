import assert from "node:assert/strict";

import { dropReasonForMessage, isDefaultAutoGreeting, type NormalizedMessage } from "../src/normalize.js";

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

const startedAtMs = 1_700_000_001_000;
const maxBackfillMs = 5 * 60 * 1000;

assert.equal(dropReasonForMessage(message(), startedAtMs, maxBackfillMs), null);
assert.equal(dropReasonForMessage(message({ isGroup: true, chatJid: "12345@g.us" }), startedAtMs, maxBackfillMs), "group_chat");
assert.equal(dropReasonForMessage(message({ chatJid: "status@broadcast" }), startedAtMs, maxBackfillMs), "broadcast_or_status_chat");
assert.equal(dropReasonForMessage(message({ chatJid: "12345@broadcast" }), startedAtMs, maxBackfillMs), "broadcast_or_status_chat");
assert.equal(dropReasonForMessage(message({ chatJid: "12345@newsletter" }), startedAtMs, maxBackfillMs), "newsletter_chat");
assert.equal(dropReasonForMessage(message({ text: "" }), startedAtMs, maxBackfillMs), "empty_text");
assert.equal(dropReasonForMessage(message({ timestampMs: 0 }), startedAtMs, maxBackfillMs), "missing_timestamp");
assert.equal(
  dropReasonForMessage(message({ timestampMs: startedAtMs - maxBackfillMs - 1 }), startedAtMs, maxBackfillMs),
  "old_backfill",
);
assert.equal(dropReasonForMessage(message({ timestampMs: startedAtMs - maxBackfillMs }), startedAtMs, maxBackfillMs), null);
assert.equal(
  isDefaultAutoGreeting("Thank you for contacting the property assistant. Please let us know how we can help you."),
  true,
);
assert.equal(isDefaultAutoGreeting("I will handle this one manually."), false);

console.log("bridge normalize tests passed");
