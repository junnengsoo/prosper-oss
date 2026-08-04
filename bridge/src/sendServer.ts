import http from "node:http";

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
