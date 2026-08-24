import http from "node:http";
import { timingSafeEqual } from "node:crypto";

type SendMessageResult = {
  key?: {
    id?: string | null;
  };
};

export type SendSocket = {
  sendMessage(chatJid: string, payload: { text: string } | { image: { url: string } } | { video: { url: string } }): Promise<SendMessageResult | undefined>;
  sendPresenceUpdate?(type: "composing" | "paused", chatJid: string): Promise<void>;
};

export type SendPayloadResult = {
  statusCode: number;
  body: Record<string, unknown>;
};

export type BridgeHttpServerOptions = {
  expectedToken: string;
  statusPayload(): Record<string, unknown>;
  pairingQrPayload(): Promise<Record<string, unknown>>;
  reconnect(clearAuth: boolean): Promise<Record<string, unknown>>;
  currentSocket(): SendSocket | null;
  notConnectedBody?(): Record<string, unknown>;
  recordSendRequest(body: Record<string, unknown>): void;
  recordSendResult(result: SendPayloadResult): void;
  recordSendException(error: unknown): void;
};

export function sendJson(response: http.ServerResponse, statusCode: number, value: Record<string, unknown>): void {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(JSON.stringify(value));
}

export function readJsonBody(request: http.IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 1024 * 1024) request.destroy(new Error("Request body too large"));
    });
    request.on("end", () => {
      try {
        resolve(JSON.parse(body || "{}") as Record<string, unknown>);
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function headerValue(request: http.IncomingMessage, name: string): string {
  const value = request.headers[name.toLowerCase()];
  if (Array.isArray(value)) return value[0] ?? "";
  return value ?? "";
}

export function bridgeTokenMatches(providedToken: string, expectedToken: string): boolean {
  if (!expectedToken) return true;
  if (!providedToken) return false;

  const expected = Buffer.from(expectedToken);
  const provided = Buffer.from(providedToken);
  if (provided.length !== expected.length) {
    const paddedProvided = Buffer.alloc(expected.length);
    provided.copy(paddedProvided, 0, 0, Math.min(provided.length, expected.length));
    timingSafeEqual(paddedProvided, expected);
    return false;
  }
  return timingSafeEqual(provided, expected);
}

export function requireBridgeToken(
  request: http.IncomingMessage,
  expectedToken: string,
): { ok: true } | { ok: false; statusCode: number; body: Record<string, unknown> } {
  if (bridgeTokenMatches(headerValue(request, "x-whatsapp-bridge-token"), expectedToken)) {
    return { ok: true };
  }
  return { ok: false, statusCode: 401, body: { error: "invalid_bridge_token" } };
}

export function isLoopbackHost(host: string): boolean {
  const normalized = host.trim().toLowerCase();
  if (normalized === "localhost" || normalized === "::1" || normalized === "[::1]" || normalized === "0:0:0:0:0:0:0:1") {
    return true;
  }
  return /^127(?:\.\d{1,3}){3}$/.test(normalized);
}

export function assertBridgeStartupSafe(host: string, expectedToken: string): void {
  if (!expectedToken && !isLoopbackHost(host)) {
    throw new Error("WHATSAPP_PA_BRIDGE_TOKEN is required when the bridge binds to a non-loopback host");
  }
}

async function sendWithTyping(sock: SendSocket, chatJid: string, send: () => Promise<SendMessageResult | undefined>): Promise<SendMessageResult | undefined> {
  try {
    await sock.sendPresenceUpdate?.("composing", chatJid);
  } catch {
    // Presence is a nice-to-have signal. Sending should still proceed if it fails.
  }

  try {
    return await send();
  } finally {
    try {
      await sock.sendPresenceUpdate?.("paused", chatJid);
    } catch {
      // Ignore presence cleanup failures for the same reason.
    }
  }
}

export async function handleSendPayload(sock: SendSocket, body: Record<string, unknown>): Promise<SendPayloadResult> {
  const chatJid = typeof body.chat_jid === "string" ? body.chat_jid.trim() : "";
  const text = typeof body.text === "string" ? body.text.trim() : "";
  const mediaType = typeof body.media_type === "string" ? body.media_type.trim() : "";
  const filePath = typeof body.file_path === "string" ? body.file_path.trim() : "";
  if (!chatJid) {
    return { statusCode: 400, body: { error: "chat_jid is required" } };
  }

  if (text) {
    const result = await sendWithTyping(sock, chatJid, () => sock.sendMessage(chatJid, { text }));
    return { statusCode: 200, body: { ok: true, message_id: result?.key?.id ?? "" } };
  }

  if (!filePath || !["photo", "video"].includes(mediaType)) {
    return { statusCode: 400, body: { error: "text or valid media_type/file_path is required" } };
  }

  const result =
    mediaType === "photo"
      ? await sendWithTyping(sock, chatJid, () => sock.sendMessage(chatJid, { image: { url: filePath } }))
      : await sendWithTyping(sock, chatJid, () => sock.sendMessage(chatJid, { video: { url: filePath } }));

  return { statusCode: 200, body: { ok: true, message_id: result?.key?.id ?? "" } };
}

export function createBridgeHttpServer(options: BridgeHttpServerOptions): http.Server {
  return http.createServer((request, response) => {
    if (request.method === "GET" && request.url === "/health") {
      sendJson(response, 200, { ok: true, bridge: "whatsapp-pa-bridge" });
      return;
    }

    const auth = requireBridgeToken(request, options.expectedToken);
    if (!auth.ok) {
      sendJson(response, auth.statusCode, auth.body);
      return;
    }

    if (request.method === "GET" && request.url === "/status") {
      sendJson(response, 200, options.statusPayload());
      return;
    }

    if (request.method === "GET" && request.url === "/pairing/qr") {
      void options
        .pairingQrPayload()
        .then((payload) => sendJson(response, payload.ok ? 200 : 404, payload))
        .catch((error) => sendJson(response, 500, { ok: false, error: error instanceof Error ? error.message : String(error) }));
      return;
    }

    if (request.method === "POST" && request.url === "/pairing/reconnect") {
      void readJsonBody(request)
        .then(async (body) => {
          const payload = await options.reconnect(body.clear_auth === true);
          sendJson(response, 202, payload);
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
        options.recordSendRequest(body);
        const sock = options.currentSocket();
        if (!sock) {
          const result = { statusCode: 503, body: options.notConnectedBody?.() ?? { error: "not_connected" } };
          options.recordSendResult(result);
          sendJson(response, result.statusCode, result.body);
          return;
        }
        const result = await handleSendPayload(sock, body);
        options.recordSendResult(result);
        sendJson(response, result.statusCode, result.body);
      })
      .catch((error) => {
        options.recordSendException(error);
        const errorName = error instanceof Error ? error.name : "Error";
        const errorMessage = error instanceof Error ? error.message : String(error);
        const errorStack = error instanceof Error ? (error.stack ?? "") : "";
        sendJson(response, 500, {
          error: "send_failed",
          error_name: errorName,
          error_message: errorMessage,
          stack: errorStack.split("\n").slice(0, 6).join("\n"),
        });
      });
  });
}
