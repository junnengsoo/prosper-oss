import { Image as ImageIcon } from "lucide-react";
import { apiUrl, type Message, type PropertyMedia, type PropertyRecord, type StageRun } from "./api";
import "./messageHistory.css";

export function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function formatMoney(value?: number | null): string {
  if (!value) return "Rent not set";
  return `S$ ${new Intl.NumberFormat("en-SG").format(value)} /mo`;
}

export function formatTime(timestampMs?: number | null): string {
  if (!timestampMs) return "";
  return new Intl.DateTimeFormat("en-SG", { hour: "2-digit", minute: "2-digit" }).format(new Date(timestampMs));
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "";
  return new Intl.DateTimeFormat("en-SG", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function statusTone(value?: string | null): "success" | "warning" | "danger" | "neutral" {
  if (!value) return "neutral";
  if (["active", "available", "sent", "success", "match", "open"].includes(value)) return "success";
  if (["pending", "draft", "handover", "manual_review"].includes(value)) return "warning";
  if (["paused", "ignored", "closed", "failed", "error", "unavailable"].includes(value)) return "danger";
  return "neutral";
}

export function statusLabel(value?: string | null): string {
  if (!value) return "No run";
  if (value === "manual_review") return "Manual Review";
  return value.replace(/_/g, " ");
}

export function manualReviewReason(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (record.stage_status === "manual_review" || record.match_status === "manual_review") {
    return typeof record.reason === "string" ? record.reason : "Review before replying.";
  }
  for (const child of Object.values(record)) {
    const reason = manualReviewReason(child);
    if (reason) return reason;
  }
  return null;
}

export function stageSummary(run?: StageRun | null): string {
  if (!run) return "No stage run yet.";
  if (run.error) return run.error;
  if (!run.output_json) return `${run.stage} completed.`;
  try {
    const parsed = JSON.parse(run.output_json) as Record<string, unknown>;
    const reason = manualReviewReason(parsed);
    if (reason) {
      return `Manual Review: ${reason}`;
    }
    const direct = parsed.reason || parsed.summary || parsed.status || parsed.match_status;
    return typeof direct === "string" ? direct : JSON.stringify(parsed).slice(0, 160);
  } catch {
    return run.output_json.slice(0, 160);
  }
}

export function propertyAvailabilityLabel(status?: string | null): string {
  return status === "available" ? "Available" : "Not available";
}

export function propertyAvailabilityTone(status?: string | null): "success" | "danger" {
  return status === "available" ? "success" : "danger";
}

export function initials(value?: string | null): string {
  const clean = (value || "?").trim();
  return clean
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "?";
}

export function truncate(value: string | null | undefined, length = 90): string {
  if (!value) return "";
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

export function mediaSrc(media?: PropertyMedia): string | undefined {
  if (!media) return undefined;
  return apiUrl(`/api/property-media/${media.id}/content`);
}

export function MessageThread({ messages, whatsapp = false }: { messages: Message[]; whatsapp?: boolean }) {
  return (
    <div className={classNames("messageThread", whatsapp && "whatsappThread")}>
      {messages.length === 0 && <EmptyState title="No messages yet" body="Messages will appear here once this conversation has activity." />}
      {messages.map((message) => (
        <article key={message.id} className={classNames("bubble", message.direction === "outbound" || message.direction === "from_me" ? "outbound" : "inbound")}>
          <p>{message.text}</p>
          <span>{formatTime(message.timestamp_ms)}</span>
        </article>
      ))}
    </div>
  );
}

export function GalleryPreview({ media }: { media: PropertyMedia[] }) {
  const first = media.find((item) => mediaSrc(item));
  if (first) return <MediaPreview media={first} className="galleryPreviewImage" />;
  return <div className="galleryPreviewImage placeholder"><ImageIcon size={24} /></div>;
}

export function MediaPreview({ media, className = "" }: { media: PropertyMedia; className?: string }) {
  const src = mediaSrc(media);
  const label = media.caption || media.file_path;
  if (!src) return <ImageIcon className={className} size={28} />;
  if (media.media_type === "video") {
    return <video className={className} src={src} aria-label={label} muted playsInline preload="metadata" controls />;
  }
  return <img className={className} src={src} alt={label} />;
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return <div className="emptyState"><strong>{title}</strong><span>{body}</span></div>;
}

export function replacePlaceholders(text: string, property: PropertyRecord | null | undefined, config: Record<string, string>): string {
  const unitInfo = property
    ? `${property.property_name}${property.asking_rent ? `, ${formatMoney(property.asking_rent)}` : ""}${property.available_from ? `, available ${property.available_from}` : ""}`
    : "this unit";
  return [
    ["{unit_info}", unitInfo],
    ["{tenant_notes}", property?.tenant_facing_caveats || ""],
    ["{tenant_facing_caveats}", property?.tenant_facing_caveats || ""],
    ["{property_guru_listing}", property?.property_url || ""],
  ].reduce((current, [token, value]) => current.split(token).join(value), text);
}
