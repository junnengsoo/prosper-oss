import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  Bell,
  Building2,
  CheckCircle2,
  Clock,
  Inbox,
  LockKeyhole,
  MessageSquare,
  Phone,
  RefreshCw,
  Settings,
  UnlockKeyhole,
  UserCircle,
  X,
} from "lucide-react";
import {
  api,
  type Contact,
  type Conversation,
  type Me,
  type Message,
  type PipelineInspection,
  type PropertyInput,
  type PropertyMedia,
  type PropertyPlaybookInput,
  type PropertyRecord,
  type RuntimeStatus,
  type StageRun,
  type WhatsappConnection,
  type WhatsappQr,
} from "./api";
import { buildInboxRows, filterInboxRows, type QueueFilter } from "./queueState";
import {
  APP_VIEW_STORAGE_KEY,
  appViewHash,
  type AppView,
  normalizeHashView,
  readStoredAppView,
} from "./viewState";
import { InboxView } from "./views/InboxView";
import { PropertiesView } from "./views/PropertiesView";
import { PropertyEditorView } from "./views/PropertyEditorView";
import { SimulatorView } from "./views/SimulatorView";
import {
  classNames,
  formatDateTime,
  initials,
} from "./viewHelpers";
import {
  EMPTY_MEDIA_PATH_FORM,
  EMPTY_PROPERTY_FORM,
  PROPERTY_REQUIRED_FIELDS,
  type EditorSection,
  type MediaPathForm,
  type PropertyRequiredField,
  generatedPropertyId,
  propertyToInput,
} from "./propertyEditorState";
import {
  cleanAutoRepliesForSave,
  defaultPlaybookInput,
  effectiveAutoReplyInput,
  emptyPlaybook,
} from "./playbookState";
import "./styles.css";

function getInitialView(): AppView {
  if (typeof window === "undefined") return "inbox";
  return normalizeHashView(window.location.hash) ?? readStoredAppView(window.localStorage) ?? "inbox";
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
  const [mediaPathForm, setMediaPathForm] = useState<MediaPathForm>({ ...EMPTY_MEDIA_PATH_FORM });

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
    setMediaPathForm({ ...EMPTY_MEDIA_PATH_FORM });
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

  async function updateMedia(media: PropertyMedia, patch: Partial<Pick<PropertyMedia, "caption" | "enabled" | "media_type" | "sort_order">>) {
    if (!editingPropertyId || editingPropertyId === "__new__") throw new Error("Save the property before updating Gallery media");
    await api.upsertPropertyMedia(editingPropertyId, {
      media_type: patch.media_type ?? media.media_type,
      file_path: media.file_path,
      caption: patch.caption ?? media.caption,
      sort_order: patch.sort_order ?? media.sort_order,
      enabled: patch.enabled ?? media.enabled,
    });
    await loadAll();
  }

  async function reorderMedia(nextMedia: PropertyMedia[]) {
    if (!editingPropertyId || editingPropertyId === "__new__") throw new Error("Save the property before reordering Gallery media");
    await Promise.all(nextMedia.map((media, index) => api.upsertPropertyMedia(editingPropertyId, {
      media_type: media.media_type,
      file_path: media.file_path,
      caption: media.caption,
      sort_order: index + 1,
      enabled: media.enabled,
    })));
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
            onUpdateMedia={(media, patch) => runAction(() => updateMedia(media, patch), "Gallery item saved")}
            onReorderMedia={(media) => runAction(() => reorderMedia(media), "Gallery order saved")}
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

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
