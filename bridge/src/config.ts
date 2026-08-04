import dotenv from "dotenv";
import path from "node:path";

const explicitEnvFile = process.env.WHATSAPP_PA_ENV_FILE;
dotenv.config({ path: path.resolve("..", ".env.prod") });
if (explicitEnvFile) {
  dotenv.config({ path: path.resolve("..", explicitEnvFile), override: true });
}
dotenv.config();

function readAccountId(value: string | undefined): string {
  return (value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export const BACKEND_BASE_URL = process.env.WHATSAPP_PA_BACKEND_URL ?? "http://127.0.0.1:8000";
export const BRIDGE_HOST = process.env.WHATSAPP_PA_BRIDGE_HOST ?? "127.0.0.1";
export const BRIDGE_PORT = Number(process.env.WHATSAPP_PA_BRIDGE_PORT ?? "8788");
const LEGACY_BURST_WAIT_MS = process.env.WHATSAPP_PA_BURST_WAIT_MS ?? "30000";
export const WHATSAPP_TRIAGE_BURST_WAIT_MS = Number(process.env.WHATSAPP_PA_TRIAGE_BURST_WAIT_MS ?? LEGACY_BURST_WAIT_MS);
export const WHATSAPP_ACTIVE_BURST_WAIT_MS = Number(process.env.WHATSAPP_PA_ACTIVE_BURST_WAIT_MS ?? LEGACY_BURST_WAIT_MS);
export const WHATSAPP_ACCOUNT_ID = readAccountId(process.env.WHATSAPP_ACCOUNT_ID);
export const WHATSAPP_BRIDGE_TOKEN = (process.env.WHATSAPP_PA_BRIDGE_TOKEN ?? "").trim();
export const WHATSAPP_PAIRING_PHONE_NUMBER = (process.env.WHATSAPP_PAIRING_PHONE_NUMBER ?? "").replace(/\D/g, "");
export const WHATSAPP_HISTORY_SYNC_ONBOARDING =
  (process.env.WHATSAPP_HISTORY_SYNC_ONBOARDING ?? "false").toLowerCase() === "true";
export const WHATSAPP_MAX_BACKFILL_MS = Number(process.env.WHATSAPP_PA_MAX_BACKFILL_MS ?? "300000");
export const RUNTIME_DIR = path.resolve("..", "runtime", "bridge");
export const ACCOUNT_RUNTIME_DIR = WHATSAPP_ACCOUNT_ID ? path.join(RUNTIME_DIR, "accounts", WHATSAPP_ACCOUNT_ID) : RUNTIME_DIR;
export const AUTH_DIR = path.join(ACCOUNT_RUNTIME_DIR, "auth");
