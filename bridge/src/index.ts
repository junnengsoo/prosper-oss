import makeWASocket, {
  makeCacheableSignalKeyStore,
  DisconnectReason,
  fetchLatestBaileysVersion,
  type BaileysEventMap,
  type ConnectionState,
  type proto,
  useMultiFileAuthState,
  type WAMessage,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import fs from "node:fs/promises";
import http from "node:http";
import P from "pino";
import qrcode from "qrcode-terminal";

import {
  AUTH_DIR,
  BACKEND_BASE_URL,
  BRIDGE_HOST,
  BRIDGE_PORT,
  WHATSAPP_ACTIVE_BURST_WAIT_MS,
  WHATSAPP_BRIDGE_TOKEN,
  WHATSAPP_HISTORY_SYNC_ONBOARDING,
  WHATSAPP_MAX_BACKFILL_MS,
  WHATSAPP_PAIRING_PHONE_NUMBER,
  WHATSAPP_TRIAGE_BURST_WAIT_MS,
  RUNTIME_DIR,
} from "./config.js";
import { fetchBridgeChatState, postInboundBatch } from "./backend.js";
import {
  dropReasonForMessage,
  isDefaultAutoGreeting,
  normalizeMessage,
  type MessageDropReason,
  type NormalizedMessage,
} from "./normalize.js";
import { pairingStatus, renderQrDataUrl } from "./pairing.js";
import { handleSendPayload, readJsonBody, sendJson } from "./sendServer.js";

type ConsoleMethod = (...args: unknown[]) => void;

function singaporeTimestamp(): string {
  return new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().replace("Z", "+08:00");
}

function withTimestamp(method: ConsoleMethod): ConsoleMethod {
  return (...args: unknown[]) => method(`[${singaporeTimestamp()}]`, ...args);
}

console.log = withTimestamp(console.log.bind(console)) as typeof console.log;
console.warn = withTimestamp(console.warn.bind(console)) as typeof console.warn;
console.error = withTimestamp(console.error.bind(console)) as typeof console.error;

const logger = P({ level: "warn", timestamp: () => `,"time":"${singaporeTimestamp()}"` });
let keepAliveTimer: NodeJS.Timeout | null = null;
let sendServer: http.Server | null = null;
let currentSock: ReturnType<typeof makeWASocket> | null = null;
let socketGeneration = 0;
let reconnectTimer: NodeJS.Timeout | null = null;
let connectionStatus: ConnectionState["connection"] | "starting" = "starting";
let lastConnectionEventAt = new Date().toISOString();
let lastDisconnectStatusCode: number | null = null;
let lastDisconnectCategory: string | null = null;
let lastDisconnectReason: string | null = null;
let lastDisconnectRequiresReauth = false;
let lastForwardedMessageAt: string | null = null;
let lastMessageUpsertAt: string | null = null;
let lastNormalizedMessageAt: string | null = null;
let lastDroppedMessageAt: string | null = null;
let lastReceivedMessageSummary: Record<string, unknown> | null = null;
let lastBackendForwardError: string | null = null;
let lastSendRequestAt: string | null = null;
let lastSendSuccessAt: string | null = null;
let lastSendFailureAt: string | null = null;
let lastSendError: string | null = null;
let lastSendSummary: Record<string, unknown> | null = null;
let lastAuthScanAt: string | null = null;
let lastAuthScanError: string | null = null;
let lastAuthStateSummary: Record<string, unknown> | null = null;
let lastCredsUpdateAt: string | null = null;
let lastCredsSaveSuccessAt: string | null = null;
let lastCredsSaveFailureAt: string | null = null;
let lastCredsSaveError: string | null = null;
let lastBadSessionAt: string | null = null;
let lastBadSessionAction: string | null = null;
let badSessionRetryCountSinceOpen = 0;
let latestQr: string | null = null;
let latestQrGeneratedAtMs: number | null = null;
let latestQrGeneration = 0;
const bridgeStartedAtMs = Date.now();
const inboundBuffers = new Map<string, { messages: NormalizedMessage[]; timer: NodeJS.Timeout; waitMs: number; burstMode: string }>();
const droppedMessageCounts: Partial<Record<MessageDropReason | "normalize_failed", number>> = {};
const disconnectCounts: Record<string, number> = {};
const recentMessageStore = new Map<string, proto.IMessage>();
const recentMessageStoreKeys: string[] = [];
const eventCleanupBySocket = new WeakMap<ReturnType<typeof makeWASocket>, () => void>();
let saveCurrentCreds: (() => Promise<void>) | null = null;
let shuttingDown = false;
let socketsCreatedCount = 0;
let socketsDisposedCount = 0;
let reconnectScheduledCount = 0;
let reconnectStartedCount = 0;
let reconnectAttemptCount = 0;
let staleConnectionEventIgnoredCount = 0;
let staleMessageEventIgnoredCount = 0;
let messagesUpsertEventCount = 0;
let messagesNotifyEventCount = 0;
let messagesAppendEventCount = 0;
let normalizedMessageCount = 0;
let forwardedBatchCount = 0;
let forwardedImmediateCount = 0;
let backendForwardFailureCount = 0;
let sendRequestCount = 0;
let sendSuccessCount = 0;
let sendFailureCount = 0;
let credsUpdateCount = 0;
let credsSaveSuccessCount = 0;
let credsSaveFailureCount = 0;
let badSessionSoftReconnectCount = 0;
let badSessionRequiresReauthCount = 0;

const RECENT_MESSAGE_STORE_LIMIT = 500;
const MAX_BAD_SESSION_RECONNECTS_BEFORE_OPEN = 1;

function textPreview(text: string, limit = 120): string {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length <= limit) return compact;
  return `${compact.slice(0, limit - 1)}…`;
}

function recordDroppedMessage(reason: MessageDropReason | "normalize_failed", message?: NormalizedMessage): void {
  lastDroppedMessageAt = new Date().toISOString();
  droppedMessageCounts[reason] = (droppedMessageCounts[reason] ?? 0) + 1;
  if (message) {
    console.log(
      `[bridge] dropped inbound message reason=${reason} chat=${message.chatJid} fromMe=${message.fromMe} type=${message.rawType} text_preview=${JSON.stringify(textPreview(message.text))}`,
    );
  }
}

function bridgeStatus(): Record<string, unknown> {
  const bufferedMessageCount = Array.from(inboundBuffers.values()).reduce((total, buffer) => total + buffer.messages.length, 0);
  return {
    ok: true,
    bridge: "whatsapp-pa-bridge",
    runtime_dir: RUNTIME_DIR,
    backend_base_url: BACKEND_BASE_URL,
    backend_auth_configured: Boolean(WHATSAPP_BRIDGE_TOKEN),
    connection: connectionStatus,
    last_connection_event_at: lastConnectionEventAt,
    last_disconnect_status_code: lastDisconnectStatusCode,
    last_disconnect_category: lastDisconnectCategory,
    last_disconnect_reason: lastDisconnectReason,
    last_disconnect_requires_reauth: lastDisconnectRequiresReauth,
    last_message_upsert_at: lastMessageUpsertAt,
    last_normalized_message_at: lastNormalizedMessageAt,
    last_forwarded_message_at: lastForwardedMessageAt,
    last_dropped_message_at: lastDroppedMessageAt,
    burst_wait_ms: WHATSAPP_TRIAGE_BURST_WAIT_MS,
    triage_burst_wait_ms: WHATSAPP_TRIAGE_BURST_WAIT_MS,
    active_burst_wait_ms: WHATSAPP_ACTIVE_BURST_WAIT_MS,
    max_backfill_ms: WHATSAPP_MAX_BACKFILL_MS,
    buffered_chat_count: inboundBuffers.size,
    buffered_message_count: bufferedMessageCount,
    dropped_message_counts: droppedMessageCounts,
    pairing_phone_configured: Boolean(WHATSAPP_PAIRING_PHONE_NUMBER),
    pairing: pairingStatus({
      qr: latestQr,
      generatedAtMs: latestQrGeneratedAtMs,
      generation: latestQrGeneration,
    }),
    history_sync_onboarding: WHATSAPP_HISTORY_SYNC_ONBOARDING,
    reconnect_mode: "guide_fixed",
    socket_generation: socketGeneration,
    current_socket_present: Boolean(currentSock),
    reconnect_timer_active: Boolean(reconnectTimer),
    process_uptime_seconds: Math.round((Date.now() - bridgeStartedAtMs) / 1000),
    diagnostics: {
      sockets_created_count: socketsCreatedCount,
      sockets_disposed_count: socketsDisposedCount,
      reconnect_scheduled_count: reconnectScheduledCount,
      reconnect_started_count: reconnectStartedCount,
      reconnect_attempt_count: reconnectAttemptCount,
      stale_connection_event_ignored_count: staleConnectionEventIgnoredCount,
      stale_message_event_ignored_count: staleMessageEventIgnoredCount,
      disconnect_counts: disconnectCounts,
      recent_message_store_size: recentMessageStore.size,
      messages_upsert_event_count: messagesUpsertEventCount,
      messages_notify_event_count: messagesNotifyEventCount,
      messages_append_event_count: messagesAppendEventCount,
      normalized_message_count: normalizedMessageCount,
      forwarded_batch_count: forwardedBatchCount,
      forwarded_immediate_count: forwardedImmediateCount,
      backend_forward_failure_count: backendForwardFailureCount,
      last_received_message_summary: lastReceivedMessageSummary,
      last_backend_forward_error: lastBackendForwardError,
      send_request_count: sendRequestCount,
      send_success_count: sendSuccessCount,
      send_failure_count: sendFailureCount,
      last_send_request_at: lastSendRequestAt,
      last_send_success_at: lastSendSuccessAt,
      last_send_failure_at: lastSendFailureAt,
      last_send_error: lastSendError,
      last_send_summary: lastSendSummary,
      creds_update_count: credsUpdateCount,
      creds_save_success_count: credsSaveSuccessCount,
      creds_save_failure_count: credsSaveFailureCount,
      last_creds_update_at: lastCredsUpdateAt,
      last_creds_save_success_at: lastCredsSaveSuccessAt,
      last_creds_save_failure_at: lastCredsSaveFailureAt,
      last_creds_save_error: lastCredsSaveError,
      bad_session_retry_count_since_open: badSessionRetryCountSinceOpen,
      bad_session_soft_reconnect_count: badSessionSoftReconnectCount,
      bad_session_requires_reauth_count: badSessionRequiresReauthCount,
      last_bad_session_at: lastBadSessionAt,
      last_bad_session_action: lastBadSessionAction,
      auth_dir: AUTH_DIR,
      last_auth_scan_at: lastAuthScanAt,
      last_auth_scan_error: lastAuthScanError,
      auth_state: lastAuthStateSummary,
    },
  };
}

function clearPairingQr(): void {
  latestQr = null;
  latestQrGeneratedAtMs = null;
}

function recordPairingQr(qr: string): void {
  latestQr = qr;
  latestQrGeneratedAtMs = Date.now();
  latestQrGeneration += 1;
}

async function pairingQrPayload(): Promise<Record<string, unknown>> {
  const status = pairingStatus({
    qr: latestQr,
    generatedAtMs: latestQrGeneratedAtMs,
    generation: latestQrGeneration,
  });
  if (!latestQr || status.qr_expired) {
    return {
      ok: false,
      status: status.qr_expired ? "qr_expired" : "qr_unavailable",
      ...status,
    };
  }
  return {
    ok: true,
    status: "qr_available",
    qr: latestQr,
    qr_data_url: await renderQrDataUrl(latestQr),
    ...status,
  };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function messageStoreKey(key: proto.IMessageKey): string {
  return [key.remoteJid ?? "", key.id ?? "", key.fromMe ? "1" : "0", key.participant ?? ""].join("|");
}

function storeRecentMessage(message: WAMessage): void {
  if (!message.key?.id || !message.message) return;
  const key = messageStoreKey(message.key);
  if (!recentMessageStore.has(key)) {
    recentMessageStoreKeys.push(key);
  }
  recentMessageStore.set(key, message.message);
  while (recentMessageStoreKeys.length > RECENT_MESSAGE_STORE_LIMIT) {
    const oldest = recentMessageStoreKeys.shift();
    if (oldest) recentMessageStore.delete(oldest);
  }
}

async function getRecentMessage(key: proto.IMessageKey): Promise<proto.IMessage | undefined> {
  return recentMessageStore.get(messageStoreKey(key));
}

function disconnectStatusCode(error: unknown): number | null {
  if (!error) return null;
  const maybeBoom = error as Partial<Boom>;
  if (typeof maybeBoom.output?.statusCode === "number") {
    return maybeBoom.output.statusCode;
  }
  return new Boom(error instanceof Error ? error : String(error)).output.statusCode;
}

type DisconnectDecision = {
  category: string;
  shouldReconnect: boolean;
  requiresReauth: boolean;
  baseDelayMs: number;
  reason: string;
};

type AuthFileSummary = {
  auth_dir_exists: boolean;
  file_count: number;
  newest_file: string | null;
  newest_mtime: string | null;
  total_bytes: number;
};

function classifyDisconnect(statusCode: number | null): DisconnectDecision {
  switch (statusCode) {
    case DisconnectReason.loggedOut:
      return {
        category: "logged_out",
        shouldReconnect: false,
        requiresReauth: true,
        baseDelayMs: 0,
        reason: "WhatsApp reported loggedOut; reconnecting would reuse an invalid session.",
      };
    case DisconnectReason.badSession:
      return {
        category: "bad_session",
        shouldReconnect: false,
        requiresReauth: true,
        baseDelayMs: 0,
        reason: "WhatsApp reported badSession; auth state may need to be cleared and paired again.",
      };
    case DisconnectReason.forbidden:
      return {
        category: "forbidden",
        shouldReconnect: false,
        requiresReauth: true,
        baseDelayMs: 0,
        reason: "WhatsApp denied access; credentials or account state need operator attention.",
      };
    case DisconnectReason.multideviceMismatch:
      return {
        category: "multidevice_mismatch",
        shouldReconnect: false,
        requiresReauth: true,
        baseDelayMs: 0,
        reason: "WhatsApp reported a multi-device protocol mismatch; update/re-auth is safer than looping.",
      };
    case DisconnectReason.restartRequired:
      return {
        category: "restart_required",
        shouldReconnect: true,
        requiresReauth: false,
        baseDelayMs: 500,
        reason: "WhatsApp requested a socket restart.",
      };
    case DisconnectReason.connectionLost:
      return {
        category: "connection_lost_or_timeout",
        shouldReconnect: true,
        requiresReauth: false,
        baseDelayMs: 2_000,
        reason: "Connection was lost or timed out; reconnect with backoff.",
      };
    case DisconnectReason.connectionClosed:
      return {
        category: "connection_closed",
        shouldReconnect: true,
        requiresReauth: false,
        baseDelayMs: 2_000,
        reason: "Socket closed normally; reconnect with backoff.",
      };
    case DisconnectReason.connectionReplaced:
      return {
        category: "connection_replaced",
        shouldReconnect: true,
        requiresReauth: false,
        baseDelayMs: 5_000,
        reason: "Another session may have replaced this socket; reconnect carefully with backoff.",
      };
    case DisconnectReason.unavailableService:
      return {
        category: "unavailable_service",
        shouldReconnect: true,
        requiresReauth: false,
        baseDelayMs: 10_000,
        reason: "WhatsApp service is temporarily unavailable; reconnect slowly.",
      };
    default:
      return {
        category: "unknown",
        shouldReconnect: true,
        requiresReauth: false,
        baseDelayMs: 5_000,
        reason: "Unknown disconnect; reconnect with backoff.",
      };
  }
}

function reconnectDelayMs(baseDelayMs: number): number {
  const multiplier = Math.min(2 ** reconnectAttemptCount, 16);
  return Math.min(baseDelayMs * multiplier, 60_000);
}

async function collectAuthFileSummary(dir: string): Promise<AuthFileSummary> {
  const summary: AuthFileSummary = {
    auth_dir_exists: false,
    file_count: 0,
    newest_file: null,
    newest_mtime: null,
    total_bytes: 0,
  };

  async function walk(currentDir: string): Promise<void> {
    const entries = await fs.readdir(currentDir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = `${currentDir}/${entry.name}`;
      if (entry.isDirectory()) {
        await walk(fullPath);
        continue;
      }
      if (!entry.isFile()) continue;
      const stat = await fs.stat(fullPath);
      summary.file_count += 1;
      summary.total_bytes += stat.size;
      const mtime = stat.mtime.toISOString();
      if (!summary.newest_mtime || mtime > summary.newest_mtime) {
        summary.newest_mtime = mtime;
        summary.newest_file = fullPath.replace(`${dir}/`, "");
      }
    }
  }

  try {
    const stat = await fs.stat(dir);
    summary.auth_dir_exists = stat.isDirectory();
    if (summary.auth_dir_exists) await walk(dir);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  return summary;
}

async function refreshAuthDiagnostics(reason: string): Promise<void> {
  lastAuthScanAt = new Date().toISOString();
  try {
    const summary = await collectAuthFileSummary(AUTH_DIR);
    lastAuthStateSummary = { ...summary, reason, socket_generation: socketGeneration };
    lastAuthScanError = null;
    console.log(
      `[bridge] auth diagnostics reason=${reason} dir=${AUTH_DIR} exists=${summary.auth_dir_exists} files=${summary.file_count} newest=${summary.newest_file ?? ""} newest_mtime=${summary.newest_mtime ?? ""} bytes=${summary.total_bytes}`,
    );
  } catch (error) {
    lastAuthScanError = error instanceof Error ? error.message : String(error);
    console.error(`[bridge] failed to scan auth diagnostics reason=${reason}:`, error);
  }
}

async function saveCredsWithDiagnostics(saveCreds: () => Promise<void>, reason: string): Promise<void> {
  lastCredsUpdateAt = new Date().toISOString();
  credsUpdateCount += 1;
  try {
    await saveCreds();
    lastCredsSaveSuccessAt = new Date().toISOString();
    lastCredsSaveError = null;
    credsSaveSuccessCount += 1;
    console.log(`[bridge] creds saved reason=${reason} socket_generation=${socketGeneration} count=${credsSaveSuccessCount}`);
    void refreshAuthDiagnostics(`creds_saved:${reason}`);
  } catch (error) {
    lastCredsSaveFailureAt = new Date().toISOString();
    lastCredsSaveError = error instanceof Error ? error.message : String(error);
    credsSaveFailureCount += 1;
    console.error(`[bridge] failed to save creds reason=${reason}:`, error);
    void refreshAuthDiagnostics(`creds_save_failed:${reason}`);
    throw error;
  }
}

function maybeRetryBadSession(decision: DisconnectDecision): DisconnectDecision {
  if (decision.category !== "bad_session") return decision;

  lastBadSessionAt = new Date().toISOString();
  if (badSessionRetryCountSinceOpen < MAX_BAD_SESSION_RECONNECTS_BEFORE_OPEN) {
    badSessionRetryCountSinceOpen += 1;
    badSessionSoftReconnectCount += 1;
    lastBadSessionAction = "soft_reconnect";
    return {
      category: "bad_session_soft_reconnect",
      shouldReconnect: true,
      requiresReauth: false,
      baseDelayMs: 2_000,
      reason:
        "WhatsApp reported badSession; trying one clean socket reconnect before requiring re-auth.",
    };
  }

  badSessionRequiresReauthCount += 1;
  lastBadSessionAction = "requires_reauth";
  return decision;
}

function installProcessHandlers(): void {
  if (keepAliveTimer) return;
  keepAliveTimer = setInterval(() => undefined, 60_000);

  const shutdown = (signal: NodeJS.Signals) => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`[bridge] Received ${signal}, shutting down.`);
    void gracefulShutdown().finally(() => process.exit(0));
  };

  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}

async function gracefulShutdown(): Promise<void> {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  for (const [chatJid, buffer] of inboundBuffers) {
    clearTimeout(buffer.timer);
    await flushInboundBuffer(chatJid);
  }
  if (sendServer) {
    await new Promise<void>((resolve) => {
      sendServer?.close(() => resolve());
    });
    sendServer = null;
  }
  disposeSocket(currentSock);
  currentSock = null;
  if (keepAliveTimer) {
    clearInterval(keepAliveTimer);
    keepAliveTimer = null;
  }
  await saveCurrentCreds?.();
}

async function reconnectSocket(clearAuth = false): Promise<void> {
  if (shuttingDown) {
    throw new Error("Bridge is shutting down");
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  clearPairingQr();
  disposeSocket(currentSock);
  currentSock = null;
  if (clearAuth) {
    saveCurrentCreds = null;
    console.log(`[bridge] Clearing auth directory for reconnect: ${AUTH_DIR}`);
    await fs.rm(AUTH_DIR, { recursive: true, force: true });
    await refreshAuthDiagnostics("auth_cleared_for_reconnect");
  } else {
    await saveCurrentCreds?.();
  }
  await connectSocket();
}

function startSendServer(): void {
  if (sendServer) return;

  sendServer = http.createServer((request, response) => {
    if (request.method === "GET" && (request.url === "/health" || request.url === "/status")) {
      sendJson(response, 200, bridgeStatus());
      return;
    }

    if (request.method === "GET" && request.url === "/pairing/qr") {
      void pairingQrPayload()
        .then((payload) => sendJson(response, payload.ok ? 200 : 404, payload))
        .catch((error) => sendJson(response, 500, { ok: false, error: error instanceof Error ? error.message : String(error) }));
      return;
    }

    if (request.method === "POST" && request.url === "/pairing/reconnect") {
      void readJsonBody(request)
        .then(async (body) => {
          await reconnectSocket(body.clear_auth === true);
          sendJson(response, 202, {
            ok: true,
            status: "reconnecting",
            clear_auth: body.clear_auth === true,
            socket_generation: socketGeneration,
          });
        })
        .catch((error) => {
          sendJson(response, 500, { ok: false, error: error instanceof Error ? error.message : String(error) });
        });
      return;
    }

    if (request.method !== "POST" || request.url !== "/send") {
      sendJson(response, 404, { error: "not_found" });
      return;
    }

    void readJsonBody(request)
      .then(async (body) => {
        sendRequestCount += 1;
        lastSendRequestAt = new Date().toISOString();
        lastSendSummary = {
          chat_jid: typeof body.chat_jid === "string" ? body.chat_jid : "",
          has_text: typeof body.text === "string" && body.text.trim().length > 0,
          media_type: typeof body.media_type === "string" ? body.media_type : "",
          socket_generation: socketGeneration,
          connection: connectionStatus,
        };
        if (!currentSock) {
          sendFailureCount += 1;
          lastSendFailureAt = new Date().toISOString();
          lastSendError = "not_connected";
          sendJson(response, 503, { error: "not_connected", connection: connectionStatus });
          return;
        }
        const result = await handleSendPayload(currentSock, body);
        if (result.statusCode >= 200 && result.statusCode < 300) {
          sendSuccessCount += 1;
          lastSendSuccessAt = new Date().toISOString();
          lastSendError = null;
        } else {
          sendFailureCount += 1;
          lastSendFailureAt = new Date().toISOString();
          lastSendError = JSON.stringify(result.body);
        }
        sendJson(response, result.statusCode, result.body);
      })
      .catch((error) => {
        const errorName = error instanceof Error ? error.name : "Error";
        const errorMessage = error instanceof Error ? error.message : String(error);
        const errorStack = error instanceof Error ? (error.stack ?? "") : "";
        sendFailureCount += 1;
        lastSendFailureAt = new Date().toISOString();
        lastSendError = `${errorName}: ${errorMessage}`;
        console.error("[bridge] failed to send outbound payload:", error);
        sendJson(response, 500, {
          error: "send_failed",
          error_name: errorName,
          error_message: errorMessage,
          stack: errorStack.split("\n").slice(0, 6).join("\n"),
        });
      });
  });

  sendServer.listen(BRIDGE_PORT, BRIDGE_HOST, () => {
    console.log(`[bridge] Send server listening on http://${BRIDGE_HOST}:${BRIDGE_PORT}`);
  });
}

function disposeSocket(sock: ReturnType<typeof makeWASocket> | null): void {
  if (!sock) return;
  socketsDisposedCount += 1;
  try {
    eventCleanupBySocket.get(sock)?.();
    eventCleanupBySocket.delete(sock);
    sock.ev.removeAllListeners("connection.update");
    sock.ev.removeAllListeners("messages.upsert");
    sock.ev.removeAllListeners("creds.update");
  } catch {
    // Best-effort cleanup. Reconnect should continue even if a stale socket is already closed.
  }
  try {
    const maybeSocket = sock as ReturnType<typeof makeWASocket> & {
      end?: (error?: Error) => void;
      ws?: { close?: () => void };
    };
    maybeSocket.end?.(undefined);
    maybeSocket.ws?.close?.();
  } catch {
    // Ignore stale socket shutdown failures.
  }
}

function scheduleReconnect(baseDelayMs: number): void {
  if (shuttingDown) return;
  if (reconnectTimer) return;
  reconnectScheduledCount += 1;
  const delayMs = reconnectDelayMs(baseDelayMs);
  reconnectAttemptCount += 1;
  console.log(`[bridge] Reconnecting in ${delayMs}ms attempt=${reconnectAttemptCount}`);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectStartedCount += 1;
    void connectSocket();
  }, delayMs);
}

async function flushInboundBuffer(chatJid: string): Promise<void> {
  const buffer = inboundBuffers.get(chatJid);
  if (!buffer) return;
  inboundBuffers.delete(chatJid);

  const messages = buffer.messages.sort((a, b) => a.timestampMs - b.timestampMs);
  try {
    await postInboundBatch(messages);
    lastForwardedMessageAt = new Date().toISOString();
    lastBackendForwardError = null;
    forwardedBatchCount += 1;
    console.log(
      `[bridge] forwarded batch chat=${chatJid} count=${messages.length} text_preview=${JSON.stringify(textPreview(messages.map((message) => message.text).join(" | ")))}`,
    );
  } catch (error) {
    backendForwardFailureCount += 1;
    lastBackendForwardError = error instanceof Error ? error.message : String(error);
    console.error("[bridge] failed to forward message batch:", error);
  }
}

async function forwardImmediate(message: NormalizedMessage): Promise<void> {
  try {
    await postInboundBatch([message]);
    lastForwardedMessageAt = new Date().toISOString();
    lastBackendForwardError = null;
    forwardedImmediateCount += 1;
    console.log(
      `[bridge] forwarded immediate chat=${message.chatJid} fromMe=${message.fromMe} id=${message.messageId} text_preview=${JSON.stringify(textPreview(message.text))}`,
    );
  } catch (error) {
    backendForwardFailureCount += 1;
    lastBackendForwardError = error instanceof Error ? error.message : String(error);
    console.error("[bridge] failed to forward immediate message:", error);
  }
}

async function burstWaitForChat(chatJid: string): Promise<{ waitMs: number; burstMode: string }> {
  try {
    const state = await fetchBridgeChatState(chatJid);
    if (state.burst_mode === "active_conversation") {
      return { waitMs: WHATSAPP_ACTIVE_BURST_WAIT_MS, burstMode: state.burst_mode };
    }
    if (typeof state.wait_ms === "number" && Number.isFinite(state.wait_ms)) {
      return { waitMs: state.wait_ms, burstMode: state.burst_mode || "backend" };
    }
    return { waitMs: WHATSAPP_TRIAGE_BURST_WAIT_MS, burstMode: state.burst_mode || "triage" };
  } catch (error) {
    console.error("[bridge] failed to fetch chat burst state, using triage wait:", error);
  }
  return { waitMs: WHATSAPP_TRIAGE_BURST_WAIT_MS, burstMode: "triage_fallback" };
}

async function bufferMessage(message: WAMessage): Promise<void> {
  const normalized = normalizeMessage(message);
  if (!normalized) {
    recordDroppedMessage("normalize_failed");
    return;
  }
  lastNormalizedMessageAt = new Date().toISOString();
  normalizedMessageCount += 1;
  lastReceivedMessageSummary = {
    chat_jid: normalized.chatJid,
    from_me: normalized.fromMe,
    raw_type: normalized.rawType,
    text_len: normalized.text.length,
    timestamp_ms: normalized.timestampMs,
    message_id: normalized.messageId,
  };
  console.log(
    `[bridge] received message chat=${normalized.chatJid} fromMe=${normalized.fromMe} type=${normalized.rawType} text_len=${normalized.text.length} text_preview=${JSON.stringify(textPreview(normalized.text))}`,
  );
  const dropReason = dropReasonForMessage(normalized, bridgeStartedAtMs, WHATSAPP_MAX_BACKFILL_MS);
  if (dropReason) {
    recordDroppedMessage(dropReason, normalized);
    return;
  }

  if (normalized.fromMe && isDefaultAutoGreeting(normalized.text)) {
    console.log(`[bridge] ignored WhatsApp auto-greeting chat=${normalized.chatJid} id=${normalized.messageId}`);
    return;
  }

  if (normalized.fromMe) {
    void flushInboundBuffer(normalized.chatJid).then(() => forwardImmediate(normalized));
    return;
  }

  const existing = inboundBuffers.get(normalized.chatJid);
  if (existing) {
    clearTimeout(existing.timer);
    existing.messages.push(normalized);
  }

  const messages = existing?.messages ?? [normalized];
  const waitState = existing
    ? { waitMs: existing.waitMs, burstMode: existing.burstMode }
    : await burstWaitForChat(normalized.chatJid);
  const timer = setTimeout(() => {
    void flushInboundBuffer(normalized.chatJid);
  }, Math.max(0, waitState.waitMs));
  inboundBuffers.set(normalized.chatJid, { messages, timer, ...waitState });

  if (waitState.waitMs <= 0) {
    clearTimeout(timer);
    void flushInboundBuffer(normalized.chatJid);
  }
}

async function connectSocket(): Promise<void> {
  if (shuttingDown) return;
  const generation = ++socketGeneration;
  socketsCreatedCount += 1;
  if (currentSock) disposeSocket(currentSock);
  currentSock = null;
  connectionStatus = "starting";
  lastConnectionEventAt = new Date().toISOString();
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  saveCurrentCreds = () => saveCredsWithDiagnostics(saveCreds, "manual_or_reconnect");
  void refreshAuthDiagnostics("socket_start");
  const { version, isLatest } = await fetchLatestBaileysVersion();
  console.log(`[bridge] WhatsApp Web version ${version.join(".")} latest=${isLatest}`);
  console.log(
    `[bridge] Auth state loaded registered=${state.creds.registered} socket_generation=${generation} auth_dir=${AUTH_DIR}`,
  );

  // Keep the socket config close to the Baileys quickstart. Product behavior
  // such as buffering, filtering, and backend forwarding happens outside this.
  const sock = makeWASocket({
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    getMessage: getRecentMessage,
    logger,
    markOnlineOnConnect: false,
    printQRInTerminal: false,
    syncFullHistory: WHATSAPP_HISTORY_SYNC_ONBOARDING,
    version,
  });
  currentSock = sock;
  startSendServer();

  let pairingCodeRequested = false;

  const processConnectionUpdate = (update: Partial<ConnectionState>) => {
    if (update.connection) {
      connectionStatus = update.connection;
      lastConnectionEventAt = new Date().toISOString();
    }

    if (update.qr) {
      recordPairingQr(update.qr);
    }

    if (update.qr && WHATSAPP_PAIRING_PHONE_NUMBER && !state.creds.registered && !pairingCodeRequested) {
      pairingCodeRequested = true;
      void sock
        .waitForSocketOpen()
        .then(() => delay(3_000))
        .then(() => sock.requestPairingCode(WHATSAPP_PAIRING_PHONE_NUMBER))
        .then((code) => {
          console.log(`[bridge] Pairing code for ${WHATSAPP_PAIRING_PHONE_NUMBER}: ${code}`);
        })
        .catch((error) => {
          pairingCodeRequested = false;
          console.error("[bridge] Failed to request pairing code:", error);
        });
      return;
    }

    if (update.qr && !WHATSAPP_PAIRING_PHONE_NUMBER) {
      console.log("[bridge] Scan this QR code with WhatsApp:");
      qrcode.generate(update.qr, { small: true });
    }

    if (update.connection === "open") {
      clearPairingQr();
      lastDisconnectStatusCode = null;
      lastDisconnectCategory = null;
      lastDisconnectReason = null;
      lastDisconnectRequiresReauth = false;
      reconnectAttemptCount = 0;
      badSessionRetryCountSinceOpen = 0;
      void refreshAuthDiagnostics("connection_open");
      console.log("[bridge] Connected.");
    }

    if (update.connection === "close") {
      const statusCode = disconnectStatusCode(update.lastDisconnect?.error);
      const decision = maybeRetryBadSession(classifyDisconnect(statusCode));
      lastDisconnectStatusCode = statusCode;
      lastDisconnectCategory = decision.category;
      lastDisconnectReason = decision.reason;
      lastDisconnectRequiresReauth = decision.requiresReauth;
      disconnectCounts[String(statusCode ?? "unknown")] = (disconnectCounts[String(statusCode ?? "unknown")] ?? 0) + 1;
      console.log(
        `[bridge] Connection closed. status=${statusCode ?? "unknown"} category=${decision.category} shouldReconnect=${decision.shouldReconnect} requiresReauth=${decision.requiresReauth} reason=${JSON.stringify(decision.reason)}`,
      );
      if (update.lastDisconnect?.error) {
        console.error("[bridge] lastDisconnect.error:", update.lastDisconnect.error);
      }
      void refreshAuthDiagnostics(`connection_close:${decision.category}`);
      if (currentSock === sock) currentSock = null;
      disposeSocket(sock);
      if (decision.shouldReconnect) scheduleReconnect(decision.baseDelayMs);
    }
  };

  const processMessagesUpsert = (event: BaileysEventMap["messages.upsert"]) => {
    lastMessageUpsertAt = new Date().toISOString();
    messagesUpsertEventCount += 1;
    if (event.type === "notify") messagesNotifyEventCount += 1;
    if (event.type === "append") messagesAppendEventCount += 1;
    console.log(`[bridge] messages.upsert type=${event.type} count=${event.messages.length}`);
    for (const message of event.messages) {
      storeRecentMessage(message);
    }
    if (event.type !== "notify") return;
    for (const message of event.messages) {
      void bufferMessage(message);
    }
  };

  const cleanup = sock.ev.process(async (events) => {
    if (generation !== socketGeneration) {
      if (events["connection.update"]) staleConnectionEventIgnoredCount += 1;
      if (events["messages.upsert"]) staleMessageEventIgnoredCount += 1;
      return;
    }

    if (events["creds.update"]) {
      await saveCredsWithDiagnostics(saveCreds, "creds.update");
    }
    if (events["connection.update"]) {
      processConnectionUpdate(events["connection.update"]);
    }
    if (events["messages.upsert"]) {
      processMessagesUpsert(events["messages.upsert"]);
    }
  });
  eventCleanupBySocket.set(sock, cleanup);
}

async function start(): Promise<void> {
  installProcessHandlers();
  console.log(`[bridge] Backend: ${BACKEND_BASE_URL}`);
  console.log(`[bridge] Runtime: ${RUNTIME_DIR}`);
  console.log(`[bridge] Inbound triage burst wait: ${WHATSAPP_TRIAGE_BURST_WAIT_MS}ms`);
  console.log(`[bridge] Inbound active burst wait: ${WHATSAPP_ACTIVE_BURST_WAIT_MS}ms`);
  console.log("[bridge] Reconnect mode: guide_fixed");
  await connectSocket();
}

void start().catch((error) => {
  console.error("[bridge] Fatal startup error:", error);
  process.exitCode = 1;
});
