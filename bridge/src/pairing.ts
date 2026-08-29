import QRCode from "qrcode";

export const QR_TTL_MS = 60_000;

export type PairingQrSnapshot = {
  qr: string | null;
  generatedAtMs: number | null;
  generation: number;
};

export function pairingStatus(snapshot: PairingQrSnapshot, nowMs = Date.now()): Record<string, unknown> {
  const generatedAtMs = snapshot.generatedAtMs;
  const ageMs = generatedAtMs ? Math.max(0, nowMs - generatedAtMs) : null;
  const expiresAtMs = generatedAtMs ? generatedAtMs + QR_TTL_MS : null;
  const expired = ageMs !== null && ageMs > QR_TTL_MS;
  return {
    qr_available: Boolean(snapshot.qr && !expired),
    qr_generated_at: generatedAtMs ? new Date(generatedAtMs).toISOString() : null,
    qr_expires_at: expiresAtMs ? new Date(expiresAtMs).toISOString() : null,
    qr_age_seconds: ageMs === null ? null : Math.round(ageMs / 1000),
    qr_expired: expired,
    qr_generation: snapshot.generation,
  };
}

export async function renderQrDataUrl(qr: string): Promise<string> {
  return QRCode.toDataURL(qr, {
    errorCorrectionLevel: "M",
    margin: 2,
    scale: 8,
    type: "image/png",
  });
}
