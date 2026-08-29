import assert from "node:assert/strict";

const originalValues = {
  PROSPER_BACKEND_URL: process.env.PROSPER_BACKEND_URL,
  PROSPER_BRIDGE_HOST: process.env.PROSPER_BRIDGE_HOST,
  PROSPER_BRIDGE_PORT: process.env.PROSPER_BRIDGE_PORT,
  PROSPER_TRIAGE_BURST_WAIT_MS: process.env.PROSPER_TRIAGE_BURST_WAIT_MS,
  PROSPER_ACTIVE_BURST_WAIT_MS: process.env.PROSPER_ACTIVE_BURST_WAIT_MS,
  PROSPER_BRIDGE_TOKEN: process.env.PROSPER_BRIDGE_TOKEN,
  PROSPER_MAX_BACKFILL_MS: process.env.PROSPER_MAX_BACKFILL_MS,
  WHATSAPP_PA_BACKEND_URL: process.env.WHATSAPP_PA_BACKEND_URL,
  WHATSAPP_PA_BRIDGE_HOST: process.env.WHATSAPP_PA_BRIDGE_HOST,
  WHATSAPP_PA_BRIDGE_PORT: process.env.WHATSAPP_PA_BRIDGE_PORT,
  WHATSAPP_PA_TRIAGE_BURST_WAIT_MS: process.env.WHATSAPP_PA_TRIAGE_BURST_WAIT_MS,
  WHATSAPP_PA_ACTIVE_BURST_WAIT_MS: process.env.WHATSAPP_PA_ACTIVE_BURST_WAIT_MS,
  WHATSAPP_PA_BRIDGE_TOKEN: process.env.WHATSAPP_PA_BRIDGE_TOKEN,
  WHATSAPP_PA_MAX_BACKFILL_MS: process.env.WHATSAPP_PA_MAX_BACKFILL_MS,
};

try {
  for (const key of Object.keys(originalValues)) {
    delete process.env[key];
  }

  process.env.PROSPER_BACKEND_URL = "http://backend.test";
  process.env.PROSPER_BRIDGE_HOST = "127.0.0.2";
  process.env.PROSPER_BRIDGE_PORT = "8789";
  process.env.PROSPER_TRIAGE_BURST_WAIT_MS = "1234";
  process.env.PROSPER_ACTIVE_BURST_WAIT_MS = "2345";
  process.env.PROSPER_BRIDGE_TOKEN = "prosper-bridge-secret";
  process.env.PROSPER_MAX_BACKFILL_MS = "3456";

  const config = await import("../src/config.js");

  assert.equal(config.BACKEND_BASE_URL, "http://backend.test");
  assert.equal(config.BRIDGE_HOST, "127.0.0.2");
  assert.equal(config.BRIDGE_PORT, 8789);
  assert.equal(config.WHATSAPP_TRIAGE_BURST_WAIT_MS, 1234);
  assert.equal(config.WHATSAPP_ACTIVE_BURST_WAIT_MS, 2345);
  assert.equal(config.WHATSAPP_BRIDGE_TOKEN, "prosper-bridge-secret");
  assert.equal(config.WHATSAPP_MAX_BACKFILL_MS, 3456);
} finally {
  for (const [key, value] of Object.entries(originalValues)) {
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
}

console.log("bridge config tests passed");
