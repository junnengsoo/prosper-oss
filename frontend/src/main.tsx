import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  Bath,
  BedDouble,
  Bell,
  Building2,
  CheckCircle2,
  ChevronLeft,
  Clock,
  Image as ImageIcon,
  Inbox,
  LockKeyhole,
  MessageSquare,
  Phone,
  Plus,
  RefreshCw,
  Save,
  Search,
  Send,
  Settings,
  Square,
  Trash2,
  UnlockKeyhole,
  Upload,
  UserCircle,
  Wand2,
  X,
} from "lucide-react";
import {
  api,
  apiUrl,
  type Contact,
  type Conversation,
  type Me,
  type Message,
  type PipelineInspection,
  type PlaybookBlock,
  type PropertyInput,
  type PropertyMedia,
  type PropertyPlaybook,
  type PropertyPlaybookInput,
  type PropertyRecord,
  type RuntimeStatus,
  type StageRun,
  type WhatsappConnection,
  type WhatsappQr,
} from "./api";
import { buildInboxRows, filterInboxRows, queueActionForConversation, type QueueFilter } from "./queueState";
import {
  APP_VIEW_STORAGE_KEY,
  appViewHash,
  type AppView,
  normalizeHashView,
  readStoredAppView,
} from "./viewState";
import "./styles.css";

type EditorSection = "facts" | "gallery" | "auto_replies";
type AutoReplyField = "availableInitial";
type PropertyRequiredField = "property_name" | "status" | "property_url";

const PROPERTY_REQUIRED_FIELDS: PropertyRequiredField[] = ["property_name", "status", "property_url"];

const EDITOR_SECTIONS: Array<{ key: EditorSection; label: string }> = [
  { key: "facts", label: "Listing Facts" },
  { key: "gallery", label: "Gallery" },
  { key: "auto_replies", label: "Auto Replies" },
];

const PLACEHOLDERS = ["{unit_info}", "{property_guru_listing}"];
const DEFAULT_MESSAGE_DELAY_SECONDS = 0.5;

const DEFAULT_AUTO_REPLIES: Record<AutoReplyField, string> = {
  availableInitial: "Hi, yes this unit is still available.",
};

const DEFAULT_VIEWING_MESSAGE = "Please share your preferred viewing time and move-in date, and I will check the next available slot.";

const DEFAULT_AUTO_REPLY_SEQUENCES: Record<AutoReplyField, PlaybookBlock[]> = {
  availableInitial: [
    { type: "message", text: DEFAULT_AUTO_REPLIES.availableInitial },
    { type: "delay", seconds: DEFAULT_MESSAGE_DELAY_SECONDS },
    { type: "message", text: DEFAULT_VIEWING_MESSAGE },
  ],
};

const LEGACY_AUTO_REPLY_DEFAULTS: Record<AutoReplyField, string[]> = {
  availableInitial: [
    "Hi, thanks for enquiring about {unit_info}. I'm the listing agent.",
    "Hi there! Thank you for inquiring about {unit_info}. I'm the listing agent.",
  ],
};

const STOCK_GALLERY_CAPTIONS = new Set([
  "Here are some photos of the unit.",
]);

const EMPTY_PROPERTY_FORM: PropertyInput = {
  property_id: "",
  property_name: "",
  status: "available",
  property_type: "rental",
  bedrooms: null,
  bathrooms: null,
  asking_rent: null,
  available_from: "",
  full_address: "",
  property_url: "",
  propertyguru_listing_id: "",
  tenant_facing_caveats: "",
};

function emptyPlaybook(): PropertyPlaybookInput {
  return {
    enabled: false,
    initial_reply_blocks: [],
  };
}

function playbookToInput(playbook: PropertyPlaybook | null | undefined): PropertyPlaybookInput {
  return {
    enabled: playbook?.enabled ?? false,
    initial_reply_blocks: playbook?.initial_reply_blocks ?? [],
  };
}

function defaultPlaybookInput(): PropertyPlaybookInput {
  return {
    enabled: false,
    initial_reply_blocks: [
      ...DEFAULT_AUTO_REPLY_SEQUENCES.availableInitial,
      { type: "gallery", mode: "enabled_property_gallery" },
    ],
  };
}

function hasWorkflowBlocks(input: PropertyPlaybookInput): boolean {
  return input.initial_reply_blocks.length > 0;
}

function effectivePlaybookInput(playbook: PropertyPlaybook | null | undefined): PropertyPlaybookInput {
  const input = playbookToInput(playbook);
  return playbook?.id || hasWorkflowBlocks(input) ? input : defaultPlaybookInput();
}

function effectiveAutoReplyInput(playbook: PropertyPlaybook | null | undefined): PropertyPlaybookInput {
  const input = effectivePlaybookInput(playbook);
  return {
    enabled: input.enabled,
    initial_reply_blocks: autoReplyWorkflowBlocks("availableInitial", input.initial_reply_blocks),
  };
}

function normalizeAutoReplyDefault(field: AutoReplyField, value: string): string {
  const normalized = value.trim();
  const text = LEGACY_AUTO_REPLY_DEFAULTS[field].some((legacy) => legacy.trim() === normalized) ? DEFAULT_AUTO_REPLIES[field] : value;
  return text;
}

function compactAutoReplyBlocks(blocks: PlaybookBlock[], field: AutoReplyField): PlaybookBlock[] {
  const editableBlocks = blocks.filter((block) => {
    if (block.type === "delay") return true;
    if (block.type !== "message") return false;
    return !STOCK_GALLERY_CAPTIONS.has((block.text || "").trim());
  });
  return editableBlocks.length > 0 ? editableBlocks : DEFAULT_AUTO_REPLY_SEQUENCES[field];
}

function normalizeAutoReplyBlocks(field: AutoReplyField, blocks: PlaybookBlock[]): PlaybookBlock[] {
  return compactAutoReplyBlocks(blocks, field).map((block) => (
    block.type === "message"
      ? { ...block, text: normalizeAutoReplyDefault(field, block.text ?? "") }
      : block
  ));
}

function autoReplyWorkflowBlocks(field: AutoReplyField, blocks: PlaybookBlock[]): PlaybookBlock[] {
  return [
    ...normalizeAutoReplyBlocks(field, blocks),
    { type: "gallery", mode: "enabled_property_gallery" },
  ];
}

function autoReplyBlocks(input: PropertyPlaybookInput, field: AutoReplyField): PlaybookBlock[] {
  return compactAutoReplyBlocks(input.initial_reply_blocks, field);
}

function playbookWithAutoReplyBlocks(input: PropertyPlaybookInput, field: AutoReplyField, blocks: PlaybookBlock[]): PropertyPlaybookInput {
  return {
    ...input,
    initial_reply_blocks: [...compactAutoReplyBlocks(blocks, field), { type: "gallery", mode: "enabled_property_gallery" }],
  };
}

function cleanAutoReplyBlocksForSave(blocks: PlaybookBlock[], field: AutoReplyField): PlaybookBlock[] {
  const cleaned: PlaybookBlock[] = [];
  for (const block of compactAutoReplyBlocks(blocks, field)) {
    if (block.type === "message") {
      if (block.text?.trim()) cleaned.push({ type: "message", text: block.text });
      continue;
    }
    if (block.type === "delay" && cleaned.length > 0 && cleaned[cleaned.length - 1].type !== "delay") {
      cleaned.push({ type: "delay", seconds: block.seconds ?? DEFAULT_MESSAGE_DELAY_SECONDS });
    }
  }
  while (cleaned[cleaned.length - 1]?.type === "delay") cleaned.pop();
  if (!cleaned.some((block) => block.type === "message")) cleaned.push(...DEFAULT_AUTO_REPLY_SEQUENCES[field]);
  return [...cleaned, { type: "gallery", mode: "enabled_property_gallery" }];
}

function cleanAutoRepliesForSave(input: PropertyPlaybookInput): PropertyPlaybookInput {
  return {
    enabled: input.enabled,
    initial_reply_blocks: cleanAutoReplyBlocksForSave(input.initial_reply_blocks, "availableInitial"),
  };
}

function propertyToInput(property: PropertyRecord | null | undefined): PropertyInput {
  if (!property) return { ...EMPTY_PROPERTY_FORM };
  return {
    property_id: property.property_id,
    property_name: property.property_name,
    status: property.status,
    property_type: "rental",
    bedrooms: property.bedrooms,
    bathrooms: property.bathrooms,
    asking_rent: property.asking_rent,
    available_from: property.available_from ?? "",
    full_address: property.full_address ?? "",
    property_url: property.property_url ?? "",
    propertyguru_listing_id: property.propertyguru_listing_id ?? "",
    tenant_facing_caveats: property.tenant_facing_caveats ?? "",
  };
}

function generatedPropertyId(name: string, existingIds: Set<string>): string {
  const base = name
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 36) || `PROPERTY-${Date.now().toString(36).toUpperCase()}`;
  let candidate = `PROP-${base}`;
  let suffix = 2;
  while (existingIds.has(candidate)) {
    candidate = `PROP-${base}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function getInitialView(): AppView {
  if (typeof window === "undefined") return "inbox";
  return normalizeHashView(window.location.hash) ?? readStoredAppView(window.localStorage) ?? "inbox";
}

function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function formatMoney(value?: number | null): string {
  if (!value) return "Rent not set";
  return `S$ ${new Intl.NumberFormat("en-SG").format(value)} /mo`;
}

function formatTime(timestampMs?: number | null): string {
  if (!timestampMs) return "";
  return new Intl.DateTimeFormat("en-SG", { hour: "2-digit", minute: "2-digit" }).format(new Date(timestampMs));
}

function formatDateTime(value?: string | null): string {
  if (!value) return "";
  return new Intl.DateTimeFormat("en-SG", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function statusTone(value?: string | null): "success" | "warning" | "danger" | "neutral" {
  if (!value) return "neutral";
  if (["active", "available", "sent", "success", "match", "open"].includes(value)) return "success";
  if (["pending", "draft", "handover", "manual_review"].includes(value)) return "warning";
  if (["paused", "ignored", "closed", "failed", "error", "unavailable"].includes(value)) return "danger";
  return "neutral";
}

function statusLabel(value?: string | null): string {
  if (!value) return "No run";
  if (value === "manual_review") return "Manual Review";
  return value.replace(/_/g, " ");
}

function manualReviewReason(value: unknown): string | null {
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

function propertyAvailabilityLabel(status?: string | null): string {
  return status === "available" ? "Available" : "Not available";
}

function propertyAvailabilityTone(status?: string | null): "success" | "danger" {
  return status === "available" ? "success" : "danger";
}

function initials(value?: string | null): string {
  const clean = (value || "?").trim();
  return clean
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "?";
}

function truncate(value: string | null | undefined, length = 90): string {
  if (!value) return "";
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}

function mediaSrc(media?: PropertyMedia): string | undefined {
  if (!media) return undefined;
  return apiUrl(`/api/property-media/${media.id}/content`);
}

function stageSummary(run?: StageRun | null): string {
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

function replacePlaceholders(text: string, property: PropertyRecord | null | undefined, config: Record<string, string>): string {
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

function App() {
  const [activeView, setActiveView] = useState<AppView>(getInitialView);
  const [authChecking, setAuthChecking] = useState(true);
  const [authReady, setAuthReady] = useState(false);
  const [currentUser, setCurrentUser] = useState<Me | null>(null);
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);

  const [contacts, setContacts] = useState<Contact[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [properties, setProperties] = useState<PropertyRecord[]>([]);
  const [stageRuns, setStageRuns] = useState<StageRun[]>([]);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [whatsappPanelOpen, setWhatsappPanelOpen] = useState(false);
  const [whatsappConnection, setWhatsappConnection] = useState<WhatsappConnection | null>(null);
  const [whatsappQr, setWhatsappQr] = useState<WhatsappQr | null>(null);
  const [whatsappBusy, setWhatsappBusy] = useState(false);
  const [whatsappError, setWhatsappError] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("Loading");
  const [warnings, setWarnings] = useState<string[]>([]);

  const [selectedConversationId, setSelectedConversationId] = useState<number | null>(null);
  const [inboxSearch, setInboxSearch] = useState("");
  const [queueFilter] = useState<QueueFilter>("all");

  const [propertySearch, setPropertySearch] = useState("");
  const [editingPropertyId, setEditingPropertyId] = useState<string | null>(null);
  const [selectedPropertyIds, setSelectedPropertyIds] = useState<string[]>([]);
  const [editorSection, setEditorSection] = useState<EditorSection>("facts");
  const [editorSectionBusy, setEditorSectionBusy] = useState(false);
  const [propertyForm, setPropertyForm] = useState<PropertyInput>({ ...EMPTY_PROPERTY_FORM });
  const [propertyFormErrors, setPropertyFormErrors] = useState<Partial<Record<PropertyRequiredField, string>>>({});
  const [mediaPathForm, setMediaPathForm] = useState({ file_path: "", caption: "", sort_order: 0, enabled: true, media_type: "photo" as "photo" | "video" });

  const [playbookDraft, setPlaybookDraft] = useState<PropertyPlaybookInput>(emptyPlaybook);
  const [playbookBaseline, setPlaybookBaseline] = useState<PropertyPlaybookInput>(emptyPlaybook);
  const loadedPlaybookPropertyIdRef = useRef("");

  const [fakeChatId, setFakeChatId] = useState(`fake-${Date.now()}`);
  const [fakeDisplayName, setFakeDisplayName] = useState("Tenant Test");
  const [fakeText, setFakeText] = useState("");
  const [fakeSending, setFakeSending] = useState(false);
  const [selectedFakeConversationId, setSelectedFakeConversationId] = useState<number | null>(null);
  const [creatingFakeChat, setCreatingFakeChat] = useState(false);
  const [fakeMessages, setFakeMessages] = useState<Message[]>([]);
  const [fakeInspection, setFakeInspection] = useState<PipelineInspection | null>(null);

  const applySession = useCallback(async () => {
    const me = await api.me();
    setCurrentUser(me);
    setAuthReady(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function initializeAuth() {
      try {
        const session = await api.authSession();
        if (!cancelled && session.authenticated) await applySession();
      } catch (error) {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : String(error);
          setLoginError(message.startsWith("401 ") ? "" : message);
          setAuthReady(false);
        }
      } finally {
        if (!cancelled) setAuthChecking(false);
      }
    }
    void initializeAuth();
    return () => {
      cancelled = true;
    };
  }, [applySession]);

  useEffect(() => {
    window.localStorage.setItem(APP_VIEW_STORAGE_KEY, activeView);
    const nextHash = `#${appViewHash(activeView)}`;
    if (window.location.hash !== nextHash) window.history.replaceState(null, "", nextHash);
    window.scrollTo({ top: 0, left: 0 });
  }, [activeView]);

  useEffect(() => {
    const onHashChange = () => {
      const nextView = normalizeHashView(window.location.hash);
      if (nextView) setActiveView(nextView);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const loadAll = useCallback(async (options: { includeSetup?: boolean } = {}) => {
    if (!authReady) return;
    const includeSetup = options.includeSetup ?? true;
    const results = await Promise.allSettled([
      api.contacts(),
      api.conversations(true),
      includeSetup ? api.properties() : Promise.resolve(null),
      api.stageRuns(),
      includeSetup ? api.config() : Promise.resolve(null),
      api.runtimeStatus(),
    ]);
    const [contactsResult, conversationsResult, propertiesResult, stageResult, configResult, runtimeResult] = results;
    const nextWarnings: string[] = [];

    if (contactsResult.status === "fulfilled") setContacts(contactsResult.value);
    else nextWarnings.push(`Contacts: ${contactsResult.reason}`);
    if (conversationsResult.status === "fulfilled") setConversations(conversationsResult.value);
    else nextWarnings.push(`Conversations: ${conversationsResult.reason}`);
    if (propertiesResult.status === "fulfilled" && propertiesResult.value) setProperties(propertiesResult.value);
    else if (propertiesResult.status === "rejected") nextWarnings.push(`Properties: ${propertiesResult.reason}`);
    if (stageResult.status === "fulfilled") setStageRuns(stageResult.value);
    else nextWarnings.push(`Stage runs: ${stageResult.reason}`);
    if (configResult.status === "fulfilled" && configResult.value) setConfig(configResult.value.values);
    else if (configResult.status === "rejected") nextWarnings.push(`Config: ${configResult.reason}`);
    if (runtimeResult.status === "fulfilled") setRuntimeStatus(runtimeResult.value);
    else nextWarnings.push(`Runtime: ${runtimeResult.reason}`);

    setWarnings(nextWarnings);
    setStatus(nextWarnings.length ? "Loaded with warnings" : "Ready");
  }, [authReady]);

  const loadWhatsappConnection = useCallback(async () => {
    if (!authReady) return;
    try {
      const connection = await api.whatsappConnection();
      setWhatsappConnection(connection);
      if (connection.state === "connected") {
        setWhatsappQr(null);
        return;
      }
      const qr = await api.whatsappQr().catch((error) => {
        setWhatsappError(error instanceof Error ? error.message : String(error));
        return null;
      });
      setWhatsappQr(qr);
    } catch (error) {
      setWhatsappConnection({
        state: "bridge_offline",
        bridge: {
          available: false,
          ok: false,
          detail: error instanceof Error ? error.message : String(error),
        },
      });
      setWhatsappQr(null);
      setWhatsappError(error instanceof Error ? error.message : String(error));
    }
  }, [authReady]);

  useEffect(() => {
    if (!whatsappPanelOpen) return;
    setWhatsappError("");
    void loadWhatsappConnection();
    const interval = window.setInterval(() => void loadWhatsappConnection(), 2000);
    return () => window.clearInterval(interval);
  }, [loadWhatsappConnection, whatsappPanelOpen]);

  async function reconnectWhatsapp(clearAuth = false) {
    setWhatsappBusy(true);
    setWhatsappError("");
    try {
      await api.reconnectWhatsapp(clearAuth);
      await loadWhatsappConnection();
      await loadAll();
    } catch (error) {
      setWhatsappError(error instanceof Error ? error.message : String(error));
    } finally {
      setWhatsappBusy(false);
    }
  }

  useEffect(() => {
    void loadAll();
    const interval = window.setInterval(() => void loadAll({ includeSetup: !editingPropertyId }), 8000);
    return () => window.clearInterval(interval);
  }, [editingPropertyId, loadAll]);

  const inboxRows = useMemo(() => buildInboxRows(contacts, conversations), [contacts, conversations]);
  const filteredInboxRows = useMemo(() => filterInboxRows(inboxRows, queueFilter, inboxSearch, properties), [inboxRows, inboxSearch, properties, queueFilter]);

  useEffect(() => {
    if (!selectedConversationId && filteredInboxRows[0]) setSelectedConversationId(filteredInboxRows[0].conversation.id);
  }, [filteredInboxRows, selectedConversationId]);

  const selectedConversation = selectedConversationId ? conversations.find((conversation) => conversation.id === selectedConversationId) ?? null : null;
  const selectedContact = selectedConversation ? contacts.find((contact) => contact.id === selectedConversation.contact_id) ?? null : null;
  const selectedProperty = selectedConversation?.matched_property_id ? properties.find((property) => property.property_id === selectedConversation.matched_property_id) ?? null : null;
  const selectedStageRuns = selectedConversation ? stageRuns.filter((run) => run.conversation_id === selectedConversation.id) : [];
  const latestStageRun = selectedStageRuns[0] ?? null;

  useEffect(() => {
    if (!selectedConversationId) {
      setMessages([]);
      return;
    }
    void api.messages(selectedConversationId).then(setMessages).catch((error) => setStatus(error instanceof Error ? error.message : String(error)));
  }, [selectedConversationId]);

  const editingPlaybookPropertyId = editingPropertyId && editingPropertyId !== "__new__" ? editingPropertyId : "";
  const playbookDirty = JSON.stringify(playbookDraft) !== JSON.stringify(playbookBaseline);

  useEffect(() => {
    if (!editingPlaybookPropertyId || !authReady) return;
    if (loadedPlaybookPropertyIdRef.current === editingPlaybookPropertyId && playbookDirty) return;
    void api
      .propertyPlaybook(editingPlaybookPropertyId)
      .then((playbook) => {
        const input = effectiveAutoReplyInput(playbook);
        loadedPlaybookPropertyIdRef.current = editingPlaybookPropertyId;
        setPlaybookDraft(input);
        setPlaybookBaseline(input);
      })
      .catch((error) => setStatus(error instanceof Error ? error.message : String(error)));
  }, [authReady, editingPlaybookPropertyId, playbookDirty]);
  function updatePropertyForm(patch: Partial<PropertyInput>) {
    setPropertyForm((current) => ({ ...current, ...patch }));
    const touchedKeys = PROPERTY_REQUIRED_FIELDS.filter((key) => Object.prototype.hasOwnProperty.call(patch, key));
    if (touchedKeys.some((key) => propertyFormErrors[key])) {
      setPropertyFormErrors((current) => {
        const next = { ...current };
        for (const key of touchedKeys) delete next[key];
        return next;
      });
    }
  }

  const filteredProperties = useMemo(() => {
    const query = propertySearch.trim().toLowerCase();
    if (!query) return properties;
    return properties.filter((property) =>
      [property.property_name, property.full_address, property.property_id, property.propertyguru_listing_id]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query)),
    );
  }, [properties, propertySearch]);

  useEffect(() => {
    const currentPropertyIds = new Set(properties.map((property) => property.property_id));
    setSelectedPropertyIds((current) => current.filter((propertyId) => currentPropertyIds.has(propertyId)));
  }, [properties]);

  const editingProperty = editingPropertyId && editingPropertyId !== "__new__" ? properties.find((property) => property.property_id === editingPropertyId) ?? null : null;
  const editingMedia = editingProperty?.media ?? [];
  const autoRepliesDirty = playbookDirty;

  const fakeConversations = conversations.filter((conversation) => conversation.source === "fake_chat");
  const selectedFakeConversation = selectedFakeConversationId ? fakeConversations.find((conversation) => conversation.id === selectedFakeConversationId) ?? null : null;

  useEffect(() => {
    if (!creatingFakeChat && !selectedFakeConversationId && fakeConversations[0]) setSelectedFakeConversationId(fakeConversations[0].id);
  }, [creatingFakeChat, fakeConversations, selectedFakeConversationId]);

  useEffect(() => {
    if (!selectedFakeConversation) {
      setFakeMessages([]);
      setFakeInspection(null);
      return;
    }
    void api.messages(selectedFakeConversation.id).then(setFakeMessages).catch(() => setFakeMessages([]));
    void api.pipelineInspection(selectedFakeConversation.id).then(setFakeInspection).catch(() => setFakeInspection(null));
  }, [selectedFakeConversation]);

  async function runAction(action: () => Promise<void>, success?: string) {
    try {
      await action();
      if (success) setStatus(success);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  }

  async function login(event: React.FormEvent) {
    event.preventDefault();
    setLoginBusy(true);
    setLoginError("");
    try {
      await api.authLogin(loginPassword);
      setLoginPassword("");
      await applySession();
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoginBusy(false);
    }
  }

  async function logout() {
    await api.authLogout().catch(() => undefined);
    setCurrentUser(null);
    setContacts([]);
    setConversations([]);
    setMessages([]);
    setFakeMessages([]);
    setSelectedConversationId(null);
    setSelectedFakeConversationId(null);
    setAuthReady(false);
  }

  function openPropertyEditor(property: PropertyRecord) {
    setEditingPropertyId(property.property_id);
    setPropertyForm(propertyToInput(property));
    setPropertyFormErrors({});
    setEditorSection("facts");
    setActiveView("properties");
  }

  function openNewPropertyEditor() {
    setEditingPropertyId("__new__");
    setPropertyForm({ ...EMPTY_PROPERTY_FORM });
    setPropertyFormErrors({});
    setPlaybookDraft(defaultPlaybookInput());
    setPlaybookBaseline(defaultPlaybookInput());
    setEditorSection("facts");
    setActiveView("properties");
  }

  async function saveProperty() {
    const requiredFields = [
      ["property_name", propertyForm.property_name, "Property name"],
      ["status", propertyForm.status, "Status"],
      ["property_url", propertyForm.property_url, "Listing URL"],
    ] as const;
    const nextErrors: Partial<Record<PropertyRequiredField, string>> = {};
    for (const [key, value, label] of requiredFields) {
      if (!String(value ?? "").trim()) nextErrors[key] = `${label} is required`;
    }
    setPropertyFormErrors(nextErrors);
    const missingFields = requiredFields.filter(([key]) => nextErrors[key]).map(([, , label]) => label);
    if (missingFields.length) throw new Error(`Required fields missing: ${missingFields.join(", ")}`);
    const existingIds = new Set(properties.map((property) => property.property_id));
    const payload = {
      ...propertyForm,
      property_id: propertyForm.property_id.trim() || generatedPropertyId(propertyForm.property_name, existingIds),
      property_type: "rental",
      propertyguru_listing_id: "",
    };
    const saved = await api.upsertProperty(payload);
    setPropertyFormErrors({});
    setEditingPropertyId(saved.property_id);
    setPropertyForm(propertyToInput(saved));
    await loadAll();
    return saved;
  }

  async function changeEditorSection(nextSection: EditorSection) {
    if (nextSection === editorSection || editorSectionBusy) return;
    if (editorSection === "facts" && nextSection !== "facts") {
      setEditorSectionBusy(true);
      setStatus("Saving listing facts");
      try {
        const saved = await saveProperty();
        const playbook = await api.propertyPlaybook(saved.property_id).catch(() => null);
        const input = effectiveAutoReplyInput(playbook);
        setPlaybookDraft(input);
        setPlaybookBaseline(input);
        setStatus("Listing facts saved");
      } finally {
        setEditorSectionBusy(false);
      }
    }
    setEditorSection(nextSection);
  }

  async function savePropertyAndExit() {
    const saved = await saveProperty();
    if (autoRepliesDirty) await saveAutoRepliesForProperty(saved.property_id);
    setEditingPropertyId(null);
  }

  function confirmPropertyDelete(propertyIds: string[]): boolean {
    const count = propertyIds.length;
    if (!count) return false;
    const label = count === 1 ? "this property" : `${count} properties`;
    return window.confirm(
      `Delete ${label}?\n\nThis removes property setup, gallery records, and Playbooks.\n\nHistorical chats and AI audit logs will remain.`,
    );
  }

  async function deleteSelectedProperties(propertyIds: string[]) {
    const ids = [...new Set(propertyIds)].filter(Boolean);
    if (!confirmPropertyDelete(ids)) return;
    const summary = ids.length === 1 ? await api.deleteProperty(ids[0]) : await api.bulkDeleteProperties(ids);
    setSelectedPropertyIds((current) => current.filter((propertyId) => !summary.deleted_property_ids.includes(propertyId)));
    if (editingPropertyId && summary.deleted_property_ids.includes(editingPropertyId)) setEditingPropertyId(null);
    await loadAll();
  }

  async function addMediaPath() {
    if (!editingPropertyId || editingPropertyId === "__new__" || !mediaPathForm.file_path.trim()) throw new Error("Save the property before adding Gallery media");
    await api.upsertPropertyMedia(editingPropertyId, {
      media_type: mediaPathForm.media_type,
      file_path: mediaPathForm.file_path,
      caption: mediaPathForm.caption,
      sort_order: mediaPathForm.sort_order,
      enabled: mediaPathForm.enabled,
    });
    setMediaPathForm({ file_path: "", caption: "", sort_order: 0, enabled: true, media_type: "photo" });
    await loadAll();
  }

  async function uploadMedia(file: File) {
    if (!editingPropertyId || editingPropertyId === "__new__") throw new Error("Save the property before uploading Gallery media");
    setStatus(`Uploading ${file.name}`);
    await api.uploadPropertyMedia(editingPropertyId, file, { caption: "", sort_order: editingMedia.length + 1, enabled: true });
    await loadAll();
  }

  async function deleteMedia(media: PropertyMedia) {
    await api.deletePropertyMedia(media.id);
    await loadAll();
  }

  async function saveAutoRepliesForProperty(propertyId: string) {
    const payload = cleanAutoRepliesForSave(playbookDraft);
    const saved = await api.upsertPropertyPlaybook(propertyId, payload);
    const input = effectiveAutoReplyInput(saved);
    setPlaybookDraft(input);
    setPlaybookBaseline(input);
    await loadAll();
  }

  async function submitFakeMessage() {
    const messageText = fakeText.trim();
    if (!messageText || fakeSending) return;
    setFakeSending(true);
    try {
      const result = await api.fakeInboundAndRun(fakeChatId, messageText, fakeDisplayName || fakeChatId);
      setFakeText("");
      await loadAll();
      if (result.conversation_id) {
        setCreatingFakeChat(false);
        setSelectedFakeConversationId(result.conversation_id);
        setFakeMessages(await api.messages(result.conversation_id));
        setFakeInspection(await api.pipelineInspection(result.conversation_id).catch(() => null));
      }
    } finally {
      setFakeSending(false);
    }
  }

  async function toggleConfig(key: "pause_ai" | "send_lock") {
    const next = config[key] === "true" ? "false" : "true";
    const updated = await api.updateConfig({ [key]: next });
    setConfig(updated.values);
  }

  if (authChecking) {
    return (
      <main className="loginScreen">
        <div className="loginCard">Loading Prosper...</div>
      </main>
    );
  }

  if (!authReady) {
    return (
      <main className="loginScreen">
        <form className="loginCard" onSubmit={login}>
          <strong>Prosper Agent</strong>
          <span>Enter the application password to continue.</span>
          <input value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} placeholder="Password" type="password" />
          {loginError && <p className="formError">{loginError}</p>}
          <button className="primaryButton" disabled={loginBusy}>{loginBusy ? "Signing in..." : "Sign in"}</button>
        </form>
      </main>
    );
  }

  return (
    <main className="appFrame">
      <Sidebar activeView={activeView} setActiveView={setActiveView} />
      <section className="appContentFrame">
        <TopBar
          activeView={activeView}
          runtimeStatus={runtimeStatus}
          config={config}
          status={status}
          currentUser={currentUser}
          onRefresh={() => void loadAll()}
          onOpenWhatsappConnection={() => setWhatsappPanelOpen(true)}
          onToggleSendLock={() => runAction(() => toggleConfig("send_lock"))}
          onLogout={currentUser ? logout : undefined}
        />
        {whatsappPanelOpen && (
          <WhatsappConnectionModal
            connection={whatsappConnection}
            qr={whatsappQr}
            busy={whatsappBusy}
            error={whatsappError}
            onClose={() => setWhatsappPanelOpen(false)}
            onReconnect={() => void reconnectWhatsapp(false)}
            onClearAuthReconnect={() => void reconnectWhatsapp(true)}
          />
        )}
        {warnings.length > 0 && (
          <div className="warningBanner">
            <AlertTriangle size={16} />
            <span>{warnings[0]}</span>
            {warnings.length > 1 && <em>+{warnings.length - 1} more</em>}
          </div>
        )}

        {activeView === "inbox" && (
          <InboxView
            rows={filteredInboxRows}
            search={inboxSearch}
            setSearch={setInboxSearch}
            selectedConversationId={selectedConversationId}
            setSelectedConversationId={setSelectedConversationId}
            contacts={contacts}
            properties={properties}
            selectedConversation={selectedConversation}
            selectedContact={selectedContact}
            selectedProperty={selectedProperty}
            messages={messages}
            latestStageRun={latestStageRun}
            selectedStageRuns={selectedStageRuns}
            onOpenProperty={selectedProperty ? () => openPropertyEditor(selectedProperty) : undefined}
          />
        )}

        {activeView === "properties" && !editingPropertyId && (
          <PropertiesView
            properties={filteredProperties}
            selectedPropertyIds={selectedPropertyIds}
            setSelectedPropertyIds={setSelectedPropertyIds}
            search={propertySearch}
            setSearch={setPropertySearch}
            onNewProperty={openNewPropertyEditor}
            onManage={openPropertyEditor}
            onDeleteSelected={(propertyIds) => runAction(() => deleteSelectedProperties(propertyIds), "Properties deleted")}
          />
        )}

        {activeView === "properties" && editingPropertyId && (
          <PropertyEditorView
            property={editingProperty}
            form={propertyForm}
            setForm={updatePropertyForm}
            formErrors={propertyFormErrors}
            section={editorSection}
            setSection={(section) => runAction(() => changeEditorSection(section))}
            sectionBusy={editorSectionBusy}
            media={editingMedia}
            mediaPathForm={mediaPathForm}
            setMediaPathForm={setMediaPathForm}
            playbookDraft={playbookDraft}
            playbookDirty={autoRepliesDirty}
            setPlaybookDraft={setPlaybookDraft}
            config={config}
            onBack={() => setEditingPropertyId(null)}
            onSave={() => runAction(savePropertyAndExit, "Property saved")}
            onDelete={editingProperty ? () => runAction(() => deleteSelectedProperties([editingProperty.property_id]), "Property deleted") : undefined}
            onAddMediaPath={() => runAction(addMediaPath, "Gallery item added")}
            onUploadMedia={(file) => runAction(() => uploadMedia(file), "Gallery media uploaded")}
            onDeleteMedia={(media) => runAction(() => deleteMedia(media), "Gallery item removed")}
          />
        )}

        {activeView === "simulator" && (
          <SimulatorView
            conversations={fakeConversations}
            selectedConversation={selectedFakeConversation}
            selectedConversationId={selectedFakeConversationId}
            creatingFakeChat={creatingFakeChat}
            setSelectedConversationId={(id) => {
              setCreatingFakeChat(false);
              setSelectedFakeConversationId(id);
            }}
            messages={fakeMessages}
            fakeText={fakeText}
            setFakeText={setFakeText}
            fakeSending={fakeSending}
            fakeChatId={fakeChatId}
            setFakeChatId={setFakeChatId}
            fakeDisplayName={fakeDisplayName}
            setFakeDisplayName={setFakeDisplayName}
            onSend={() => runAction(submitFakeMessage, "Simulator message sent")}
            onNewChat={() => {
              setFakeChatId(`fake-${Date.now()}`);
              setFakeDisplayName("Tenant Test");
              setFakeText("");
              setFakeSending(false);
              setCreatingFakeChat(true);
              setSelectedFakeConversationId(null);
              setFakeMessages([]);
              setFakeInspection(null);
            }}
            onReset={() => runAction(async () => {
              await api.resetFakeChat();
              setCreatingFakeChat(true);
              setFakeSending(false);
              setSelectedFakeConversationId(null);
              setFakeMessages([]);
              await loadAll();
            }, "Simulator data cleared")}
          />
        )}
      </section>
    </main>
  );
}

function Sidebar({ activeView, setActiveView }: { activeView: AppView; setActiveView: (view: AppView) => void }) {
  const items: Array<{ view: AppView; label: string; icon: React.ComponentType<{ size?: number }> }> = [
    { view: "inbox", label: "Inbox", icon: Inbox },
    { view: "properties", label: "Properties", icon: Building2 },
    { view: "simulator", label: "Simulator", icon: MessageSquare },
  ];
  return (
    <aside className="sideRail">
      <div className="brandMark">P</div>
      <nav>
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.view} className={classNames("navItem", activeView === item.view && "active")} onClick={() => setActiveView(item.view)}>
              <Icon size={22} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="accountRail">
        <UserCircle size={24} />
        <span>Account</span>
      </div>
    </aside>
  );
}

function TopBar({
  activeView,
  runtimeStatus,
  config,
  status,
  currentUser,
  onRefresh,
  onOpenWhatsappConnection,
  onToggleSendLock,
  onLogout,
}: {
  activeView: AppView;
  runtimeStatus: RuntimeStatus | null;
  config: Record<string, string>;
  status: string;
  currentUser: Me | null;
  onRefresh: () => void;
  onOpenWhatsappConnection: () => void;
  onToggleSendLock: () => void;
  onLogout?: () => void;
}) {
  const titles: Record<AppView, string> = { inbox: "Leads", properties: "Prosper", simulator: "Simulator" };
  const whatsappConnected = runtimeStatus?.bridge.connection === "open";
  const sendLocked = config.send_lock === "true";
  return (
    <header className="topBar">
      <div className="topTitle">
        <h1>{titles[activeView]}</h1>
        <span>{status}</span>
      </div>
      <div className="topActions">
        <button className={classNames("statusPill", whatsappConnected ? "success" : "danger")} onClick={onOpenWhatsappConnection}>
          <MessageSquare size={15} />
          WhatsApp {whatsappConnected ? "Online" : "Offline"}
        </button>
        <button className={classNames("statusPill", sendLocked ? "danger" : "success")} onClick={onToggleSendLock}>
          {sendLocked ? <LockKeyhole size={15} /> : <UnlockKeyhole size={15} />}
          Sending {sendLocked ? "Locked" : "Enabled"}
        </button>
        <button className="iconButton" onClick={onRefresh} title="Refresh"><Settings size={18} /></button>
        <Bell size={19} className="mutedIcon" />
        {currentUser && onLogout ? (
          <button className="logoutButton" onClick={onLogout}>
            <span>{initials(currentUser.email || currentUser.auth_user_id)}</span>
            Sign out
          </button>
        ) : (
          <div className="avatarButton">PA</div>
        )}
      </div>
    </header>
  );
}

function WhatsappConnectionModal({
  connection,
  qr,
  busy,
  error,
  onClose,
  onReconnect,
  onClearAuthReconnect,
}: {
  connection: WhatsappConnection | null;
  qr: WhatsappQr | null;
  busy: boolean;
  error: string;
  onClose: () => void;
  onReconnect: () => void;
  onClearAuthReconnect: () => void;
}) {
  const state = connection?.state || "connecting";
  const connected = state === "connected";
  const bridgeOffline = state === "bridge_offline" || connection?.bridge.available === false;
  const needsReauth = state === "needs_reauth";
  const qrDataUrl = qr?.qr_data_url || "";
  const qrAvailable = Boolean(qrDataUrl && (qr?.ok === true || qr?.qr_available === true));
  const qrExpired = qr?.qr_expired === true || state === "qr_expired" || qr?.status === "qr_expired";
  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true" aria-label="WhatsApp connection">
      <section className="connectionModal">
        <header>
          <div>
            <h2>Connect WhatsApp</h2>
            <span className={classNames("badge", connected ? "success" : bridgeOffline ? "danger" : "warning")}>
              {connected ? "Connected" : bridgeOffline ? "Bridge offline" : needsReauth ? "Reconnect needed" : qrAvailable ? "Scan QR" : "Connecting"}
            </span>
          </div>
          <button className="iconButton" onClick={onClose} aria-label="Close WhatsApp connection panel"><X size={18} /></button>
        </header>

        {connected && (
          <div className="connectionState">
            <CheckCircle2 size={28} />
            <div>
              <strong>WhatsApp is connected</strong>
              <span>Last event {formatDateTime(connection?.bridge.last_connection_event_at)}</span>
            </div>
          </div>
        )}

        {!connected && (
          <div className="qrLayout">
            <div className="qrBox">
              {qrAvailable ? (
                <img src={qrDataUrl} alt="WhatsApp pairing QR code" />
              ) : (
                <div className="qrPlaceholder">
                  {bridgeOffline ? <AlertTriangle size={36} /> : <Clock size={36} />}
                  <strong>{bridgeOffline ? "Bridge offline" : qrExpired ? "QR expired" : "Waiting for QR"}</strong>
                  <span>{bridgeOffline ? "Start the WhatsApp bridge, then refresh this panel." : "Prosper will show the QR here once WhatsApp asks for pairing."}</span>
                </div>
              )}
            </div>
            <div className="qrInstructions">
              <strong>Scan with WhatsApp</strong>
              <ol>
                <li>Open WhatsApp on the phone.</li>
                <li>Go to Linked Devices.</li>
                <li>Tap Link a Device.</li>
                <li>Scan this QR code.</li>
              </ol>
              {qr?.qr_expires_at && <span className="mutedLine">QR expires {formatDateTime(qr.qr_expires_at)}</span>}
              {needsReauth && <p className="formError">WhatsApp says this session needs to be linked again.</p>}
              {error && <p className="formError">{error}</p>}
              <div className="connectionActions">
                <button className="primaryButton" onClick={needsReauth ? onClearAuthReconnect : onReconnect} disabled={busy}>
                  {busy ? <Clock size={16} /> : <RefreshCw size={16} />}
                  {needsReauth ? "Reconnect WhatsApp" : "Refresh QR"}
                </button>
                {!needsReauth && (
                  <button onClick={onClearAuthReconnect} disabled={busy || bridgeOffline}>
                    Reset link
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function InboxView({
  rows,
  search,
  setSearch,
  selectedConversationId,
  setSelectedConversationId,
  properties,
  selectedConversation,
  selectedContact,
  selectedProperty,
  messages,
  latestStageRun,
  selectedStageRuns,
  onOpenProperty,
}: {
  rows: ReturnType<typeof buildInboxRows>;
  search: string;
  setSearch: (value: string) => void;
  selectedConversationId: number | null;
  setSelectedConversationId: (id: number) => void;
  contacts: Contact[];
  properties: PropertyRecord[];
  selectedConversation: Conversation | null;
  selectedContact: Contact | null;
  selectedProperty: PropertyRecord | null;
  messages: Message[];
  latestStageRun: StageRun | null;
  selectedStageRuns: StageRun[];
  onOpenProperty?: () => void;
}) {
  return (
    <section className="inboxLayout">
      <aside className="leadListPane">
        <div className="searchBox">
          <Search size={18} />
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search for leads" />
        </div>
            <div className="filterChips">
              <button className="active">All Today</button>
              <button>Unread</button>
              <button>Interested</button>
              <button>Matched</button>
        </div>
        <div className="leadList">
          {rows.length === 0 && <EmptyState title="No matched enquiries" body="Matched WhatsApp enquiries will appear here after rental listing matching." />}
          {rows.map((row) => {
            const property = row.conversation.matched_property_id ? properties.find((item) => item.property_id === row.conversation.matched_property_id) : null;
            const action = queueActionForConversation(row.conversation, row.contact);
            return (
              <button
                key={row.conversation.id}
                className={classNames("leadRow", selectedConversationId === row.conversation.id && "active")}
                onClick={() => setSelectedConversationId(row.conversation.id)}
              >
                <div className="leadRowTop">
                  <strong>{row.contact?.display_name || row.contact?.phone || row.contact?.chat_jid || "Unknown lead"}</strong>
                  <span>{formatTime(row.conversation.latest_message_timestamp_ms)}</span>
                </div>
                <p>{truncate(row.conversation.latest_message_text, 72) || "No latest message"}</p>
                <div className="leadProperty">
                  <Building2 size={14} />
                  <span>{property?.property_name || row.conversation.matched_property_id || action.label}</span>
                  <b className={classNames("badge", action.tone)}>{action.label}</b>
                  <i className={classNames("dot", action.tone)} />
                </div>
              </button>
            );
          })}
        </div>
      </aside>
      <section className="leadDetailPane">
        {!selectedConversation ? (
          <EmptyState title="Select a matched enquiry" body="The lead transcript, matched listing, and audit summary will appear here." />
        ) : (
          <>
            <div className="leadHeader">
              <div className="leadAvatar">{initials(selectedContact?.display_name || selectedContact?.chat_jid)}</div>
              <div>
                <h2>{selectedContact?.display_name || selectedContact?.phone || selectedContact?.chat_jid}</h2>
                <span>Last active {formatTime(selectedConversation.latest_message_timestamp_ms)} · Singapore</span>
              </div>
              <div className="leadActions">
                <button><Phone size={16} /> Call</button>
                <button className="primaryButton"><MessageSquare size={16} /> WhatsApp Agent</button>
              </div>
            </div>
            <div className="leadSummaryGrid">
              <article className="matchedPropertyCard">
                <GalleryPreview media={selectedProperty?.media ?? []} />
                <div>
                  <strong>{selectedProperty?.property_name || selectedConversation.matched_property_id}</strong>
                  <span>{selectedProperty?.full_address || "Matched property"}</span>
                  <b>{formatMoney(selectedProperty?.asking_rent)}</b>
                </div>
                {onOpenProperty && <button onClick={onOpenProperty}>Edit</button>}
              </article>
              <article className="auditCard">
                <div className="cardTitle">
                  <span>Prosper Audit</span>
                  <b className={classNames("badge", statusTone(latestStageRun?.status))}>{statusLabel(latestStageRun?.status)}</b>
                </div>
                <p>{stageSummary(latestStageRun)}</p>
                <details>
                  <summary>Decision timeline</summary>
                  <div className="timelineList">
                    {selectedStageRuns.map((run) => (
                      <div key={run.id}>
                        <strong>{run.stage}</strong>
                        <span>{statusLabel(run.status)} · {formatDateTime(run.created_at)}</span>
                      </div>
                    ))}
                  </div>
                </details>
              </article>
            </div>
            <MessageThread messages={messages} />
          </>
        )}
      </section>
    </section>
  );
}

function PropertiesView({
  properties,
  selectedPropertyIds,
  setSelectedPropertyIds,
  search,
  setSearch,
  onNewProperty,
  onManage,
  onDeleteSelected,
}: {
  properties: PropertyRecord[];
  selectedPropertyIds: string[];
  setSelectedPropertyIds: React.Dispatch<React.SetStateAction<string[]>>;
  search: string;
  setSearch: (value: string) => void;
  onNewProperty: () => void;
  onManage: (property: PropertyRecord) => void;
  onDeleteSelected: (propertyIds: string[]) => void;
}) {
  const visiblePropertyIds = properties.map((property) => property.property_id);
  const selectedVisibleIds = visiblePropertyIds.filter((propertyId) => selectedPropertyIds.includes(propertyId));
  const allVisibleSelected = visiblePropertyIds.length > 0 && selectedVisibleIds.length === visiblePropertyIds.length;

  function togglePropertySelection(propertyId: string) {
    setSelectedPropertyIds((current) =>
      current.includes(propertyId) ? current.filter((item) => item !== propertyId) : [...current, propertyId],
    );
  }

  function toggleSelectAllVisible() {
    setSelectedPropertyIds((current) => {
      const visible = new Set(visiblePropertyIds);
      if (allVisibleSelected) return current.filter((propertyId) => !visible.has(propertyId));
      return [...current, ...visiblePropertyIds.filter((propertyId) => !current.includes(propertyId))];
    });
  }

  return (
    <section className="propertiesPage">
      <div className="pageToolbar">
        <div className="toolbarLeft">
          <div className="searchBox">
            <Search size={18} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search listings" />
          </div>
          <span>{properties.length} listings managed</span>
          <label className="selectAllControl">
            <input type="checkbox" checked={allVisibleSelected} disabled={visiblePropertyIds.length === 0} onChange={toggleSelectAllVisible} />
            Select all visible
          </label>
        </div>
        <div className="toolbarActions">
          {selectedPropertyIds.length > 0 && (
            <div className="bulkActionBar">
              <strong>{selectedPropertyIds.length} selected</strong>
              <button className="dangerButton" onClick={() => onDeleteSelected(selectedPropertyIds)}>
                <Trash2 size={15} /> Delete selected
              </button>
            </div>
          )}
          <button className="primaryButton" onClick={onNewProperty}><Plus size={17} /> New Listing</button>
        </div>
      </div>
      <div className="listingStack">
        {properties.length === 0 && <EmptyState title="No properties yet" body="Create or seed properties to manage listing workflows." />}
        {properties.map((property) => {
          const enabledMedia = property.media.filter((media) => media.enabled);
          return (
            <article key={property.property_id} className="listingCard">
              <div className="listingImage"><GalleryPreview media={property.media} /></div>
              <div className="listingBody">
                <div className="listingTop">
                  <div>
                    <div className="badgeRow">
                      <span className={classNames("badge", propertyAvailabilityTone(property.status))}>{propertyAvailabilityLabel(property.status)}</span>
                    </div>
                    <h2>{property.property_name}</h2>
                    <p>
                      <span>Rental</span>
                      <span><BedDouble size={15} /> {property.bedrooms ?? "-"} BR</span>
                      <span><Bath size={15} /> {property.bathrooms ?? "-"} Bath</span>
                    </p>
                  </div>
                  <div className="listingPrice">
                    <strong>{formatMoney(property.asking_rent)}</strong>
                    <span>{property.available_from || "Availability not set"}</span>
                  </div>
                </div>
                <div className="listingMeta">
                  <div><span>Gallery</span><strong>{enabledMedia.length} enabled</strong></div>
                </div>
                <div className="listingActions">
                  <label className="listingSelect">
                    <input
                      type="checkbox"
                      checked={selectedPropertyIds.includes(property.property_id)}
                      onChange={() => togglePropertySelection(property.property_id)}
                    />
                    Select
                  </label>
                  <button className="dangerButton" onClick={() => onDeleteSelected([property.property_id])}>
                    <Trash2 size={15} /> Delete
                  </button>
                  <button className="primaryButton" onClick={() => onManage(property)}>Edit</button>
                </div>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function PropertyEditorView({
  property,
  form,
  setForm,
  formErrors,
  section,
  setSection,
  sectionBusy,
  media,
  mediaPathForm,
  setMediaPathForm,
  playbookDraft,
  playbookDirty,
  setPlaybookDraft,
  config,
  onBack,
  onSave,
  onDelete,
  onAddMediaPath,
  onUploadMedia,
  onDeleteMedia,
}: {
  property: PropertyRecord | null;
  form: PropertyInput;
  setForm: (patch: Partial<PropertyInput>) => void;
  formErrors: Partial<Record<PropertyRequiredField, string>>;
  section: EditorSection;
  setSection: (section: EditorSection) => void;
  sectionBusy: boolean;
  media: PropertyMedia[];
  mediaPathForm: { file_path: string; caption: string; sort_order: number; enabled: boolean; media_type: "photo" | "video" };
  setMediaPathForm: (form: { file_path: string; caption: string; sort_order: number; enabled: boolean; media_type: "photo" | "video" }) => void;
  playbookDraft: PropertyPlaybookInput;
  playbookDirty: boolean;
  setPlaybookDraft: (draft: PropertyPlaybookInput | ((current: PropertyPlaybookInput) => PropertyPlaybookInput)) => void;
  config: Record<string, string>;
  onBack: () => void;
  onSave: () => void;
  onDelete?: () => void;
  onAddMediaPath: () => void;
  onUploadMedia: (file: File) => void;
  onDeleteMedia: (media: PropertyMedia) => void;
}) {
  const previewProperty = property ? { ...property, ...form, media } : null;
  return (
    <section className="propertyEditorPage">
      <header className="editorHeader">
        <button onClick={onBack}><ChevronLeft size={18} /> Back</button>
        <div>
          <h2>{property?.property_name || "New Listing"}</h2>
          <span className={classNames("badge", statusTone(form.status))}>{form.status || "draft"}</span>
        </div>
        <div className="editorHeaderActions">
          {property && onDelete && <button className="dangerButton" onClick={onDelete}><Trash2 size={16} /> Delete</button>}
          <button className="primaryButton" onClick={onSave}><Save size={16} /> Save & Exit</button>
        </div>
      </header>
      <div className="editorProgress"><span style={{ width: `${((EDITOR_SECTIONS.findIndex((item) => item.key === section) + 1) / EDITOR_SECTIONS.length) * 100}%` }} /></div>
      <div className="editorLayout">
        <aside className="sectionNav">
          <strong>Your progress</strong>
          {EDITOR_SECTIONS.map((item) => (
            <button key={item.key} className={classNames(section === item.key && "active")} onClick={() => setSection(item.key)} disabled={sectionBusy}>
              <span>{item.label}</span>
              <CheckCircle2 size={15} />
            </button>
          ))}
        </aside>
        <section className="editorContent">
          {section === "facts" && (
            <EditorPanel title="Listing Facts" body="Structured facts used by matching and tenant-facing context.">
              <Field label="Property name" required error={formErrors.property_name}><input value={form.property_name} onChange={(event) => setForm({ property_name: event.target.value })} /></Field>
              <Field label="Status" required error={formErrors.status}>
                <select value={form.status} onChange={(event) => setForm({ status: event.target.value })}>
                  <option value="available">Available</option>
                  <option value="unavailable">Unavailable</option>
                  <option value="unknown">Unknown</option>
                  <option value="draft">Draft</option>
                </select>
              </Field>
              <Field label="Listing URL" required wide error={formErrors.property_url}><input value={form.property_url ?? ""} onChange={(event) => setForm({ property_url: event.target.value })} /></Field>
              <Field label="Rent"><input type="number" value={form.asking_rent ?? ""} onChange={(event) => setForm({ asking_rent: event.target.value ? Number(event.target.value) : null })} /></Field>
              <Field label="Available date"><input value={form.available_from ?? ""} onChange={(event) => setForm({ available_from: event.target.value })} /></Field>
              <Field label="Bedrooms"><input type="number" value={form.bedrooms ?? ""} onChange={(event) => setForm({ bedrooms: event.target.value ? Number(event.target.value) : null })} /></Field>
              <Field label="Bathrooms"><input type="number" value={form.bathrooms ?? ""} onChange={(event) => setForm({ bathrooms: event.target.value ? Number(event.target.value) : null })} /></Field>
              <Field label="Address" wide><input value={form.full_address ?? ""} onChange={(event) => setForm({ full_address: event.target.value })} /></Field>
            </EditorPanel>
          )}
          {section === "gallery" && (
            <EditorPanel title="Gallery" body="Images and videos Prosper sends after this unit is matched.">
              <div className="galleryGrid">
                {media.map((item) => (
                  <article key={item.id} className="galleryTile">
                    <MediaPreview media={item} />
                    <strong>{item.caption || item.file_path}</strong>
                    <span>{item.enabled ? "Enabled" : "Disabled"} · {item.media_type}</span>
                    <button onClick={() => onDeleteMedia(item)}><Trash2 size={15} /> Remove</button>
                  </article>
                ))}
              </div>
              <div className="inlineForm">
                <input value={mediaPathForm.file_path} onChange={(event) => setMediaPathForm({ ...mediaPathForm, file_path: event.target.value })} placeholder="Local file path or storage reference" />
                <input value={mediaPathForm.caption} onChange={(event) => setMediaPathForm({ ...mediaPathForm, caption: event.target.value })} placeholder="Caption" />
                <button onClick={onAddMediaPath}>Add path</button>
              </div>
              <label className="uploadDrop">
                <Upload size={18} />
                Upload image/video
                <input type="file" accept="image/*,video/*" onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.currentTarget.value = "";
                  if (file) onUploadMedia(file);
                }} />
              </label>
            </EditorPanel>
          )}
          {section === "auto_replies" && (
            <AutoRepliesEditor
              property={previewProperty}
              draft={playbookDraft}
              dirty={playbookDirty}
              setDraft={setPlaybookDraft}
              config={config}
              disabled={!property}
            />
          )}
        </section>
      </div>
    </section>
  );
}

function AutoRepliesEditor({
  property,
  draft,
  dirty,
  setDraft,
  config,
  disabled,
}: {
  property: PropertyRecord | null;
  draft: PropertyPlaybookInput;
  dirty: boolean;
  setDraft: (draft: PropertyPlaybookInput | ((current: PropertyPlaybookInput) => PropertyPlaybookInput)) => void;
  config: Record<string, string>;
  disabled: boolean;
}) {
  return (
    <section className="autoRepliesLayout">
      <div className="autoRepliesForm">
        <div className="autoRepliesHeader">
          <div>
            <h2>Auto Replies</h2>
            <p>Edit the WhatsApp messages Prosper sends for this listing.</p>
          </div>
          <label className="toggleRow">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
              disabled={disabled}
            />
            Enabled
          </label>
          {dirty && <span className="badge warning">Unsaved</span>}
        </div>

        {disabled && <EmptyState title="Save listing first" body="Create the property before editing its Auto Replies." />}

        <article className="replyGroup">
          <header>
            <strong>When Unit Is Available</strong>
            <span>Prosper sends this after matching this property.</span>
          </header>
          <AutoReplySequence
            field="availableInitial"
            blocks={autoReplyBlocks(draft, "availableInitial")}
            setDraft={setDraft}
            disabled={disabled}
          />
          <div className="mediaStepNote"><ImageIcon size={17} /> Prosper sends this unit's media after the reply.</div>
        </article>

      </div>

      <aside className="autoPreviewPane">
        <div>
          <span className="eyebrow">Available Preview</span>
          <WhatsAppPreview property={property} blocks={draft.initial_reply_blocks} config={config} />
        </div>
      </aside>
    </section>
  );
}

function AutoReplySequence({
  field,
  blocks,
  setDraft,
  disabled,
}: {
  field: AutoReplyField;
  blocks: PlaybookBlock[];
  setDraft: (draft: PropertyPlaybookInput | ((current: PropertyPlaybookInput) => PropertyPlaybookInput)) => void;
  disabled: boolean;
}) {
  function commit(nextBlocks: PlaybookBlock[]) {
    setDraft((current) => playbookWithAutoReplyBlocks(current, field, nextBlocks));
  }

  function updateBlock(index: number, patch: Partial<PlaybookBlock>) {
    commit(blocks.map((block, blockIndex) => blockIndex === index ? { ...block, ...patch } : block));
  }

  function appendPlaceholder(index: number, placeholder: string) {
    const block = blocks[index];
    if (block?.type !== "message") return;
    updateBlock(index, { text: `${block.text ?? ""}${placeholder}` });
  }

  function addMessage() {
    const nextBlocks: PlaybookBlock[] = [...blocks];
    if (nextBlocks.length > 0) nextBlocks.push({ type: "delay", seconds: DEFAULT_MESSAGE_DELAY_SECONDS });
    nextBlocks.push({ type: "message", text: "" });
    commit(nextBlocks);
  }

  function removeBlock(index: number) {
    const nextBlocks = blocks.filter((_, blockIndex) => blockIndex !== index);
    commit(nextBlocks.length > 0 ? nextBlocks : DEFAULT_AUTO_REPLY_SEQUENCES[field]);
  }

  return (
    <div className="autoReplySequence">
      {blocks.map((block, index) => (
        block.type === "delay" ? (
          <div className="autoReplyDelay" key={`${block.type}-${index}`}>
            <Clock size={16} />
            <span>Wait</span>
            <input
              type="number"
              min="0"
              max="30"
              step="0.5"
              value={block.seconds ?? DEFAULT_MESSAGE_DELAY_SECONDS}
              onChange={(event) => updateBlock(index, { seconds: Number(event.target.value) })}
              disabled={disabled}
            />
            <span>seconds</span>
            <button type="button" className="iconButton" onClick={() => removeBlock(index)} disabled={disabled} aria-label="Remove delay">
              <Trash2 size={15} />
            </button>
          </div>
        ) : (
          <label className="autoReplyTextarea" key={`${block.type}-${index}`}>
            <span>Message {messageNumber(blocks, index)}</span>
            <textarea rows={4} value={block.text ?? ""} onChange={(event) => updateBlock(index, { text: event.target.value })} disabled={disabled} />
            <div className="placeholderRow">
              {PLACEHOLDERS.map((placeholder) => (
                <button key={placeholder} type="button" onClick={() => appendPlaceholder(index, placeholder)} disabled={disabled}>
                  {placeholder}
                </button>
              ))}
              {blocks.filter((item) => item.type === "message").length > 1 && (
                <button type="button" className="dangerTextButton" onClick={() => removeBlock(index)} disabled={disabled}>
                  <Trash2 size={14} /> Remove
                </button>
              )}
            </div>
          </label>
        )
      ))}
      <button type="button" className="secondaryButton autoReplyAddButton" onClick={addMessage} disabled={disabled}>
        <Plus size={16} /> Add message
      </button>
    </div>
  );
}

function messageNumber(blocks: PlaybookBlock[], index: number): number {
  return blocks.slice(0, index + 1).filter((block) => block.type === "message").length;
}

function SimulatorView({
  conversations,
  selectedConversation,
  selectedConversationId,
  creatingFakeChat,
  setSelectedConversationId,
  messages,
  fakeText,
  setFakeText,
  fakeSending,
  fakeChatId,
  setFakeChatId,
  fakeDisplayName,
  setFakeDisplayName,
  onSend,
  onNewChat,
  onReset,
}: {
  conversations: Conversation[];
  selectedConversation: Conversation | null;
  selectedConversationId: number | null;
  creatingFakeChat: boolean;
  setSelectedConversationId: (id: number) => void;
  messages: Message[];
  fakeText: string;
  setFakeText: (value: string) => void;
  fakeSending: boolean;
  fakeChatId: string;
  setFakeChatId: (value: string) => void;
  fakeDisplayName: string;
  setFakeDisplayName: (value: string) => void;
  onSend: () => void;
  onNewChat: () => void;
  onReset: () => void;
}) {
  return (
    <section className="simulatorLayout">
      <aside className="simListPane">
        <div className="simHeader">
          <h2>Simulations</h2>
          <div className="simHeaderActions">
            <button onClick={onReset} title="Clear simulator data" aria-label="Clear simulator data"><Trash2 size={16} /></button>
            <button className="primaryButton" onClick={onNewChat}><Plus size={16} /> New</button>
          </div>
        </div>
        <div className="simConfig">
          <input value={fakeDisplayName} onChange={(event) => setFakeDisplayName(event.target.value)} placeholder="Display name" />
          <input value={fakeChatId} onChange={(event) => setFakeChatId(event.target.value)} placeholder="Fake chat ID" />
        </div>
        <div className="simList">
          {creatingFakeChat && (
            <button className="active draftChatRow" type="button">
              <strong>New chat</strong>
              <span>{fakeDisplayName || fakeChatId}</span>
            </button>
          )}
          {conversations.map((conversation) => (
            <button key={conversation.id} className={conversation.id === selectedConversationId ? "active" : ""} onClick={() => setSelectedConversationId(conversation.id)}>
              <strong>Tenant #{conversation.id}</strong>
              <span>{truncate(conversation.latest_message_text, 44) || "No latest message"}</span>
            </button>
          ))}
        </div>
      </aside>
      <section className="simChatPane">
        <div className="chatHeader">
          <div className="leadAvatar">{initials(fakeDisplayName)}</div>
          <div><strong>{fakeDisplayName}</strong><span>{selectedConversation ? `Conversation #${selectedConversation.id}` : "New local chat"}</span></div>
        </div>
        <MessageThread messages={messages} whatsapp />
        <div className="chatComposer">
          <span className="composerStatus">{fakeSending ? "Sending..." : <Plus size={20} />}</span>
          <textarea value={fakeText} onChange={(event) => setFakeText(event.target.value)} placeholder="Type a fake tenant WhatsApp message..." onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSend();
            }
          }} disabled={fakeSending} />
          <button className="primaryButton" disabled={fakeSending || !fakeText.trim()} onClick={onSend} aria-label="Send fake message" title="Send fake message">
            {fakeSending ? <Clock size={18} /> : <Send size={18} />}
          </button>
        </div>
      </section>
    </section>
  );
}

function MessageThread({ messages, whatsapp = false }: { messages: Message[]; whatsapp?: boolean }) {
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

function WhatsAppPreview({ property, blocks, config }: { property: PropertyRecord | null; blocks: PlaybookBlock[]; config: Record<string, string> }) {
  return (
    <div className="phoneShell">
      <div className="phoneHeader">
        <ChevronLeft size={20} />
        <div className="phoneAvatar">{initials("Sarah Tan")}</div>
        <div><strong>Sarah Tan (Lead)</strong><span>online</span></div>
        <Phone size={17} />
      </div>
      <div className="phoneBody">
        <span className="todayPill">Today</span>
        <div className="phoneBubble inbound">Hi, I'm interested in {property?.property_name || "this unit"}. Is it still available?</div>
        {blocks.map((block, index) => {
          if (block.type === "delay") return <div key={index} className="delayPill">{block.seconds ?? 1}s delay</div>;
          if (block.type === "gallery") {
            return (
              <div key={index} className="phoneBubble outbound mediaBubble">
                <GalleryPreview media={property?.media.filter((item) => item.enabled) ?? []} />
              </div>
            );
          }
          const text = block.text || "";
          return <div key={index} className="phoneBubble outbound">{replacePlaceholders(text, property, config)}</div>;
        })}
      </div>
      <div className="phoneComposer"><span>Type a message</span><Send size={15} /></div>
    </div>
  );
}

function GalleryPreview({ media }: { media: PropertyMedia[] }) {
  const first = media.find((item) => mediaSrc(item));
  if (first) return <MediaPreview media={first} className="galleryPreviewImage" />;
  return <div className="galleryPreviewImage placeholder"><ImageIcon size={24} /></div>;
}

function MediaPreview({ media, className = "" }: { media: PropertyMedia; className?: string }) {
  const src = mediaSrc(media);
  const label = media.caption || media.file_path;
  if (!src) return <ImageIcon className={className} size={28} />;
  if (media.media_type === "video") {
    return <video className={className} src={src} aria-label={label} muted playsInline preload="metadata" controls />;
  }
  return <img className={className} src={src} alt={label} />;
}

function Field({ label, children, wide = false, required = false, error = "" }: { label: string; children: React.ReactNode; wide?: boolean; required?: boolean; error?: string }) {
  return (
    <label className={classNames("field", wide && "wide", error && "error")}>
      <span>{label}{required && <b aria-label="required">*</b>}</span>
      {children}
      {error && <em>{error}</em>}
    </label>
  );
}

function EditorPanel({ title, body, children }: { title: string; body: string; children: React.ReactNode }) {
  return (
    <article className="editorPanel">
      <div className="panelIntro"><h2>{title}</h2><p>{body}</p></div>
      <div className="formGrid">{children}</div>
    </article>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return <div className="emptyState"><strong>{title}</strong><span>{body}</span></div>;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
