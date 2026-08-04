import type { proto, WAMessage } from "@whiskeysockets/baileys";

export type NormalizedMessage = {
  chatJid: string;
  senderJid: string;
  messageId: string;
  timestampMs: number;
  fromMe: boolean;
  isGroup: boolean;
  text: string;
  rawType: string;
};

export type MessageDropReason =
  | "group_chat"
  | "broadcast_or_status_chat"
  | "newsletter_chat"
  | "empty_text"
  | "missing_timestamp"
  | "old_backfill";

export const DEFAULT_AUTO_GREETING_TEXT = "Thank you for contacting the property assistant. Please let us know how we can help you.";

function normalizeComparableText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

export function isDefaultAutoGreeting(text: string): boolean {
  return normalizeComparableText(text) === normalizeComparableText(DEFAULT_AUTO_GREETING_TEXT);
}

function timestampToMs(value: WAMessage["messageTimestamp"]): number {
  if (typeof value === "number") return value * 1000;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed * 1000 : 0;
  }
  if (value && typeof value === "object" && "toNumber" in value && typeof value.toNumber === "function") {
    return value.toNumber() * 1000;
  }
  return 0;
}

function unwrapMessage(message: proto.IMessage | null | undefined): proto.IMessage | null | undefined {
  let current = message;
  for (let i = 0; i < 5; i += 1) {
    if (current?.ephemeralMessage?.message) {
      current = current.ephemeralMessage.message;
      continue;
    }
    if (current?.viewOnceMessage?.message) {
      current = current.viewOnceMessage.message;
      continue;
    }
    if (current?.viewOnceMessageV2?.message) {
      current = current.viewOnceMessageV2.message;
      continue;
    }
    return current;
  }
  return current;
}

function rawMessageType(message: proto.IMessage | null | undefined): string {
  if (!message) return "unknown";
  return Object.keys(message).find((key) => !["messageContextInfo", "senderKeyDistributionMessage"].includes(key)) ?? "unknown";
}

function extractText(message: proto.IMessage | null | undefined): string {
  if (!message) return "";
  if (message.conversation) return message.conversation.trim();
  if (message.extendedTextMessage?.text) return message.extendedTextMessage.text.trim();
  return "";
}

export function normalizeMessage(message: WAMessage): NormalizedMessage | null {
  const chatJid = message.key.remoteJid ?? "";
  const messageId = message.key.id ?? "";
  if (!chatJid || !messageId) return null;

  const unwrapped = unwrapMessage(message.message);
  return {
    chatJid,
    senderJid: message.key.participant ?? message.key.remoteJid ?? "",
    messageId,
    timestampMs: timestampToMs(message.messageTimestamp),
    fromMe: Boolean(message.key.fromMe),
    isGroup: chatJid.endsWith("@g.us"),
    text: extractText(unwrapped),
    rawType: rawMessageType(unwrapped),
  };
}

export function isBroadcastOrStatusJid(chatJid: string): boolean {
  return chatJid === "status@broadcast" || chatJid.endsWith("@broadcast");
}

export function isNewsletterJid(chatJid: string): boolean {
  return chatJid.endsWith("@newsletter");
}

export function dropReasonForMessage(
  message: NormalizedMessage,
  bridgeStartedAtMs: number,
  maxBackfillMs: number,
): MessageDropReason | null {
  if (message.isGroup) return "group_chat";
  if (isBroadcastOrStatusJid(message.chatJid)) return "broadcast_or_status_chat";
  if (isNewsletterJid(message.chatJid)) return "newsletter_chat";
  if (!message.text) return "empty_text";
  if (!message.timestampMs) return "missing_timestamp";

  const backfillWindowMs = Math.max(0, maxBackfillMs);
  if (message.timestampMs < bridgeStartedAtMs - backfillWindowMs) return "old_backfill";

  return null;
}
