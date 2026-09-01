const viteEnv = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env ?? {};
const API_BASE = viteEnv.VITE_API_BASE ?? (viteEnv.DEV ? "http://127.0.0.1:8000" : "/api");

export function apiUrl(path: string): string {
  const base = API_BASE.replace(/\/$/, "");
  if ((base === "/api" || base.endsWith("/api")) && path.startsWith("/api/")) {
    return `${base}${path.slice(4)}`;
  }
  return `${base}${path}`;
}

export type Contact = {
  id: number;
  chat_jid: string;
  display_name: string | null;
  phone: string | null;
  status: ContactStatus;
  status_reason: string | null;
};

export type ContactStatus = "active" | "paused" | "ignored";

export type Me = {
  auth_user_id: string;
  email: string | null;
};

export type AuthSession = {
  authenticated: boolean;
  email: string | null;
};

export type Conversation = {
  id: number;
  contact_id: number;
  source: string;
  status: ConversationLifecycleStatus;
  current_stage: ConversationPipelineStage | null;
  matched_property_id: string | null;
  latest_message_text: string | null;
  latest_message_timestamp_ms: number | null;
  latest_message_direction: string | null;
};

export type ConversationLifecycleStatus = "active" | "closed";
export type ConversationPipelineStage = "rental_listing_matching" | "manual_review" | "end";

export type Message = {
  id: number;
  conversation_id: number;
  direction: string;
  text: string;
  timestamp_ms: number;
};

export type PropertyMedia = {
  id: number;
  property_id: string;
  media_type: "photo" | "video";
  file_path: string;
  file_exists?: boolean;
  sendable?: boolean;
  storage_reference?: string;
  caption: string;
  sort_order: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type PropertyRecord = {
  id: number;
  property_id: string;
  property_name: string;
  status: string;
  property_type: string | null;
  bedrooms: number | null;
  bathrooms: number | null;
  asking_rent: number | null;
  available_from: string | null;
  full_address: string | null;
  property_url: string | null;
  propertyguru_listing_id: string | null;
  tenant_facing_caveats: string;
  created_at: string;
  updated_at: string;
  media: PropertyMedia[];
};

export type PropertyInput = Omit<PropertyRecord, "id" | "created_at" | "updated_at" | "media">;

export type PropertyDeleteSummary = {
  deleted_property_ids: string[];
  deleted_counts: {
    properties: number;
    media: number;
    playbooks: number;
  };
};

export type PlaybookBlock = {
  type: "message" | "delay" | "gallery";
  text?: string | null;
  seconds?: number | null;
  mode?: "enabled_property_gallery" | null;
};

export type PropertyPlaybook = {
  id: number | null;
  property_id: string;
  initial_reply_blocks: PlaybookBlock[];
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
};

export type PropertyPlaybookInput = Omit<PropertyPlaybook, "id" | "property_id" | "created_at" | "updated_at">;

export type StageRun = {
  id: number;
  conversation_id: number | null;
  stage: string;
  input_snapshot: string;
  output_json: string | null;
  status: string;
  error: string | null;
  model: string | null;
  created_at: string;
};

export type PlannedAction = {
  action_type: "send_playbook";
  stage: string;
  reason?: string;
  property_id?: string;
  blocks?: PlaybookBlock[];
};

export type PipelineInspection = {
  conversation_id: number;
  pipeline_result: Record<string, unknown>;
  planned_actions: PlannedAction[];
  stage_runs: Array<{
    id: number;
    stage: string;
    status: string;
    output: Record<string, unknown> | null;
    error: string | null;
    model: string | null;
    created_at: string;
  }>;
};

export type RuntimeStatus = {
  app: string;
  config: RuntimeConfigValues;
  llm?: {
    provider: string;
    configured: boolean;
    model: string;
    base_url: string;
  };
  bridge: {
    available: boolean;
    ok: boolean;
    connection?: string;
    pairing?: {
      qr_available?: boolean;
      qr_generated_at?: string | null;
      qr_expires_at?: string | null;
      qr_age_seconds?: number | null;
      qr_expired?: boolean;
      qr_generation?: number;
    };
    backend_base_url?: string;
    burst_wait_ms?: number;
    max_backfill_ms?: number;
    buffered_chat_count?: number;
    buffered_message_count?: number;
    dropped_message_counts?: Record<string, number>;
    last_connection_event_at?: string;
    last_disconnect_status_code?: number | null;
    last_forwarded_message_at?: string | null;
    error?: string;
    detail?: string;
    url?: string;
  };
};

export type RuntimeConfigKey = "pause_ai" | "send_lock";
export type RuntimeConfigValues = Partial<Record<RuntimeConfigKey, string>>;

export type WhatsappConnection = {
  state: "connected" | "connecting" | "qr_available" | "qr_expired" | "bridge_offline" | "needs_reauth" | "disconnected" | string;
  bridge: RuntimeStatus["bridge"];
};

export type WhatsappQr = {
  state: string;
  available?: boolean;
  ok?: boolean;
  status?: string;
  qr?: string;
  qr_data_url?: string;
  qr_available?: boolean;
  qr_generated_at?: string | null;
  qr_expires_at?: string | null;
  qr_age_seconds?: number | null;
  qr_expired?: boolean;
  qr_generation?: number;
  error?: string;
  detail?: string;
};

export type FakeChatResetResult = {
  contacts_deleted: number;
  conversations_deleted: number;
  messages_deleted: number;
  stage_runs_deleted: number;
};

function formatApiError(status: number, bodyText: string): string {
  if (!bodyText) return `${status} Request failed`;
  try {
    const parsed = JSON.parse(bodyText) as unknown;
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string") return `${status} ${detail}`;
      if (Array.isArray(detail)) {
        const messages = detail
          .map((item) => {
            if (!item || typeof item !== "object") return String(item);
            const record = item as Record<string, unknown>;
            const location = Array.isArray(record.loc) ? record.loc.join(".") : "";
            const message = typeof record.msg === "string" ? record.msg : JSON.stringify(record);
            return location ? `${location}: ${message}` : message;
          })
          .join("; ");
        return `${status} ${messages}`;
      }
      return `${status} ${JSON.stringify(detail)}`;
    }
  } catch {
    // Fall through to plain-text response body.
  }
  return `${status} ${bodyText}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(formatApiError(response.status, await response.text()));
  }
  return response.json() as Promise<T>;
}

async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    credentials: "include",
    headers: {
    },
    body: formData,
  });
  if (!response.ok) {
    throw new Error(formatApiError(response.status, await response.text()));
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ ok: boolean; app: string }>("/health"),
  authLogin: (password: string) => request<AuthSession>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  authSession: () => request<AuthSession>("/api/auth/session"),
  authLogout: () => request<AuthSession>("/api/auth/logout", { method: "POST" }),
  me: () => request<Me>("/api/me"),
  contacts: () => request<Contact[]>("/api/contacts"),
  updateContactStatus: (contactId: number, status: ContactStatus, status_reason = "") =>
    request<Contact>(`/api/contacts/${contactId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status, status_reason }),
    }),
  conversations: (includeClosed = false) => request<Conversation[]>(`/api/conversations?include_closed=${includeClosed ? "true" : "false"}`),
  messages: (conversationId: number) => request<Message[]>(`/api/conversations/${conversationId}/messages`),
  properties: () => request<PropertyRecord[]>("/api/properties"),
  propertyMedia: (propertyId: string, includeDisabled = false) =>
    request<PropertyMedia[]>(`/api/properties/${encodeURIComponent(propertyId)}/media?include_disabled=${includeDisabled ? "true" : "false"}`),
  playbooks: () => request<PropertyPlaybook[]>("/api/property-playbooks"),
  propertyPlaybook: (propertyId: string) => request<PropertyPlaybook>(`/api/properties/${encodeURIComponent(propertyId)}/playbook`),
  upsertPropertyPlaybook: (propertyId: string, playbook: PropertyPlaybookInput) =>
    request<PropertyPlaybook>(`/api/properties/${encodeURIComponent(propertyId)}/playbook`, {
      method: "PUT",
      body: JSON.stringify(playbook),
    }),
  stageRuns: () => request<StageRun[]>("/api/stage-runs"),
  pipelineInspection: (conversationId: number) => request<PipelineInspection>(`/api/conversations/${conversationId}/inspection`),
  config: () => request<{ values: RuntimeConfigValues }>("/api/config"),
  runtimeStatus: () => request<RuntimeStatus>("/api/runtime/status"),
  whatsappConnection: () => request<WhatsappConnection>("/api/whatsapp/connection"),
  whatsappQr: () => request<WhatsappQr>("/api/whatsapp/qr"),
  reconnectWhatsapp: (clearAuth = false) =>
    request<Record<string, unknown>>(`/api/whatsapp/reconnect?clear_auth=${clearAuth ? "true" : "false"}`, { method: "POST" }),
  updateConfig: (values: RuntimeConfigValues) =>
    request<{ values: RuntimeConfigValues }>("/api/config", { method: "PATCH", body: JSON.stringify({ values }) }),
  upsertProperty: (property: PropertyInput) =>
    request<PropertyRecord>("/api/properties", { method: "POST", body: JSON.stringify(property) }),
  deleteProperty: (propertyId: string) =>
    request<PropertyDeleteSummary>(`/api/properties/${encodeURIComponent(propertyId)}`, { method: "DELETE" }),
  bulkDeleteProperties: (propertyIds: string[]) =>
    request<PropertyDeleteSummary>("/api/properties/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ property_ids: propertyIds }),
    }),
  upsertPropertyMedia: (propertyId: string, media: Omit<PropertyMedia, "id" | "property_id" | "created_at" | "updated_at">) =>
    request<PropertyMedia>(`/api/properties/${encodeURIComponent(propertyId)}/media`, { method: "POST", body: JSON.stringify(media) }),
  uploadPropertyMedia: (
    propertyId: string,
    file: File,
    options?: { media_type?: "photo" | "video"; caption?: string; sort_order?: number; enabled?: boolean },
  ) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("media_type", options?.media_type ?? (file.type.startsWith("video/") ? "video" : "photo"));
    formData.append("caption", options?.caption ?? "");
    formData.append("sort_order", String(options?.sort_order ?? 0));
    formData.append("enabled", String(options?.enabled ?? true));
    return requestForm<PropertyMedia>(`/api/properties/${encodeURIComponent(propertyId)}/media/upload`, formData);
  },
  deletePropertyMedia: (mediaId: number) => request<PropertyMedia>(`/api/property-media/${mediaId}`, { method: "DELETE" }),
  fakeInbound: (chat_jid: string, text: string, display_name?: string) =>
    request<Message>("/api/fake-chat/inbound", { method: "POST", body: JSON.stringify({ chat_jid, text, display_name }) }),
  fakeInboundAndRun: (chat_jid: string, text: string, display_name?: string) =>
    request<{ conversation_id: number | null; result: Record<string, unknown> }>("/api/fake-chat/inbound-and-run", {
      method: "POST",
      body: JSON.stringify({ chat_jid, text, display_name }),
    }),
  resetFakeChat: () => request<FakeChatResetResult>("/api/fake-chat/reset", { method: "POST" }),
};
