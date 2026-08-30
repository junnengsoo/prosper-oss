import dotenv from "dotenv";
import path from "node:path";

function envValue(primary: string, legacy?: string): string | undefined {
  return process.env[primary] ?? (legacy ? process.env[legacy] : undefined);
}

const explicitEnvFile = envValue("PROSPER_ENV_FILE", "WHATSAPP_PA_ENV_FILE");
dotenv.config({ path: path.resolve("..", ".env.prod") });
dotenv.config({ path: path.resolve("..", ".env") });
if (explicitEnvFile) {
  dotenv.config({ path: path.resolve("..", explicitEnvFile), override: true });
}
dotenv.config();

export const BACKEND_BASE_URL = envValue("PROSPER_BACKEND_URL", "WHATSAPP_PA_BACKEND_URL") ?? "http://127.0.0.1:8000";
export const BRIDGE_HOST = envValue("PROSPER_BRIDGE_HOST", "WHATSAPP_PA_BRIDGE_HOST") ?? "127.0.0.1";
export const BRIDGE_PORT = Number(envValue("PROSPER_BRIDGE_PORT", "WHATSAPP_PA_BRIDGE_PORT") ?? "8788");
export const WHATSAPP_TRIAGE_BURST_WAIT_MS = Number(envValue("PROSPER_TRIAGE_BURST_WAIT_MS", "WHATSAPP_PA_TRIAGE_BURST_WAIT_MS") ?? "30000");
export const WHATSAPP_ACTIVE_BURST_WAIT_MS = Number(envValue("PROSPER_ACTIVE_BURST_WAIT_MS", "WHATSAPP_PA_ACTIVE_BURST_WAIT_MS") ?? "30000");
export const WHATSAPP_BRIDGE_TOKEN = (envValue("PROSPER_BRIDGE_TOKEN", "WHATSAPP_PA_BRIDGE_TOKEN") ?? "").trim();
export const WHATSAPP_PAIRING_PHONE_NUMBER = (process.env.WHATSAPP_PAIRING_PHONE_NUMBER ?? "").replace(/\D/g, "");
export const WHATSAPP_HISTORY_SYNC_ONBOARDING =
  (process.env.WHATSAPP_HISTORY_SYNC_ONBOARDING ?? "false").toLowerCase() === "true";
export const WHATSAPP_MAX_BACKFILL_MS = Number(envValue("PROSPER_MAX_BACKFILL_MS", "WHATSAPP_PA_MAX_BACKFILL_MS") ?? "300000");
export const RUNTIME_DIR = path.resolve("..", "runtime", "bridge");
export const AUTH_DIR = path.join(RUNTIME_DIR, "auth");
