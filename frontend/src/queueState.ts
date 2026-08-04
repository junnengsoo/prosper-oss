import type { Contact, Conversation, PropertyRecord } from "./api";

export type QueueFilter = "all";

export type QueueActionTone = "neutral" | "success" | "warning" | "danger";

export type QueueAction = {
  label: string;
  tone: QueueActionTone;
};

export type InboxRow =
  { kind: "conversation"; conversation: Conversation; contact: Contact | undefined };

export function buildInboxRows(contacts: Contact[], conversations: Conversation[]): InboxRow[] {
  return conversations
    .filter((conversation) => conversation.source === "whatsapp" && Boolean(conversation.matched_property_id))
    .map((conversation) => {
      const contact = contacts.find((item) => item.id === conversation.contact_id);
      return { kind: "conversation", conversation, contact };
    });
}

export function matchesQueueFilter(row: InboxRow, queueFilter: QueueFilter): boolean {
  return queueFilter === "all";
}

export function matchesQueueSearch(row: InboxRow, queryText: string, properties: PropertyRecord[]): boolean {
  const query = queryText.trim().toLowerCase();
  if (!query) return true;
  const property = row.conversation.matched_property_id
    ? properties.find((item) => item.property_id === row.conversation.matched_property_id)
    : null;
  return [
    row.contact?.display_name,
    row.contact?.chat_jid,
    row.contact?.phone,
    row.contact?.status,
    row.conversation.current_stage,
    row.conversation.status,
    row.conversation.source,
    row.conversation.matched_property_id,
    row.conversation.latest_message_direction,
    row.conversation.latest_message_text,
    property?.property_name,
    property?.full_address,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(query));
}

export function filterInboxRows(rows: InboxRow[], queueFilter: QueueFilter, queryText: string, properties: PropertyRecord[]): InboxRow[] {
  return rows.filter((row) => matchesQueueFilter(row, queueFilter) && matchesQueueSearch(row, queryText, properties));
}

export function queueActionForConversation(conversation: Conversation, contact?: Contact | null): QueueAction {
  if (contact?.status === "paused") return { label: "Paused", tone: "danger" };
  if (contact?.status === "ignored") return { label: "Ignored", tone: "danger" };
  if (conversation.status === "closed") return { label: "Closed", tone: "neutral" };
  return { label: "Unit matched", tone: "success" };
}
