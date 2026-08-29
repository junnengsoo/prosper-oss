import assert from "node:assert/strict";
import http from "node:http";

import { createBridgeHttpServer, type BridgeHttpServerOptions, type SendSocket } from "../src/sendServer.js";

type HttpResult = {
  status: number;
  body: Record<string, unknown>;
};

async function listen(server: http.Server): Promise<string> {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.equal(typeof address, "object");
  assert.ok(address);
  return `http://127.0.0.1:${address.port}`;
}

async function close(server: http.Server): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

async function request(baseUrl: string, path: string, options: { method?: string; token?: string; body?: Record<string, unknown> } = {}): Promise<HttpResult> {
  const headers: Record<string, string> = {};
  if (options.token !== undefined) headers["x-whatsapp-bridge-token"] = options.token;
  if (options.body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(`${baseUrl}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  return { status: response.status, body: (await response.json()) as Record<string, unknown> };
}

function fakeSocket(): SendSocket {
  return {
    async sendMessage() {
      return { key: { id: "sent-1" } };
    },
  };
}

function options(overrides: Partial<BridgeHttpServerOptions> = {}): BridgeHttpServerOptions {
  return {
    expectedToken: "bridge-secret",
    statusPayload: () => ({ ok: true, status: "ready" }),
    pairingQrPayload: async () => ({ ok: true, status: "qr_available", qr: "qr-data" }),
    reconnect: async (clearAuth) => ({
      ok: true,
      status: "reconnecting",
      clear_auth: clearAuth,
      socket_generation: 7,
    }),
    currentSocket: () => fakeSocket(),
    recordSendRequest: () => undefined,
    recordSendResult: () => undefined,
    recordSendException: () => undefined,
    ...overrides,
  };
}

{
  const server = createBridgeHttpServer(options());
  const baseUrl = await listen(server);
  try {
    const health = await request(baseUrl, "/health");
    assert.equal(health.status, 200);
    assert.equal(health.body.ok, true);
    assert.equal(health.body.bridge, "prosper-bridge");

    for (const path of ["/status", "/pairing/qr"]) {
      assert.equal((await request(baseUrl, path)).status, 401);
      assert.equal((await request(baseUrl, path, { token: "wrong-token" })).status, 401);
      assert.equal((await request(baseUrl, path, { token: "bridge-secret" })).status, 200);
    }

    for (const path of ["/send", "/pairing/reconnect"]) {
      assert.equal((await request(baseUrl, path, { method: "POST", body: {} })).status, 401);
      assert.equal((await request(baseUrl, path, { method: "POST", token: "wrong-token", body: {} })).status, 401);
      const valid = await request(baseUrl, path, {
        method: "POST",
        token: "bridge-secret",
        body: path === "/send" ? { chat_jid: "tenant@s.whatsapp.net", text: "Hi" } : { clear_auth: true },
      });
      assert.equal(valid.status, path === "/send" ? 200 : 202);
    }
  } finally {
    await close(server);
  }
}

console.log("bridge send HTTP server tests passed");
