import assert from "node:assert/strict";

import { QR_TTL_MS, pairingStatus, renderQrDataUrl } from "../src/pairing.js";

const generatedAtMs = Date.parse("2026-07-19T09:00:00.000Z");

{
  const status = pairingStatus({ qr: "test-qr", generatedAtMs, generation: 3 }, generatedAtMs + 10_000);
  assert.equal(status.qr_available, true);
  assert.equal(status.qr_expired, false);
  assert.equal(status.qr_age_seconds, 10);
  assert.equal(status.qr_generation, 3);
}

{
  const status = pairingStatus({ qr: "test-qr", generatedAtMs, generation: 4 }, generatedAtMs + QR_TTL_MS + 1);
  assert.equal(status.qr_available, false);
  assert.equal(status.qr_expired, true);
}

{
  const status = pairingStatus({ qr: null, generatedAtMs: null, generation: 0 }, generatedAtMs);
  assert.equal(status.qr_available, false);
  assert.equal(status.qr_expired, false);
  assert.equal(status.qr_generated_at, null);
}

{
  const dataUrl = await renderQrDataUrl("sample-whatsapp-qr");
  assert.equal(dataUrl.startsWith("data:image/png;base64,"), true);
}

console.log("bridge pairing tests passed");
