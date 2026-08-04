import {
  buildInboxRows,
  filterInboxRows,
  matchesQueueSearch,
  queueActionForConversation,
} from "../src/queueState";
import type { Contact, Conversation, PropertyRecord } from "../src/api";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

const contacts: Contact[] = [
  { id: 1, chat_jid: "tenant-1@s.whatsapp.net", display_name: "Demo Tenant", phone: "555-0101", status: "active", status_reason: null, last_message_at: null },
  { id: 2, chat_jid: "tenant-2@s.whatsapp.net", display_name: "Second Tenant", phone: "555-0102", status: "active", status_reason: null, last_message_at: null },
  { id: 3, chat_jid: "tenant-3@s.whatsapp.net", display_name: "Paused Contact", phone: "555-0103", status: "paused", status_reason: "manual_takeover", last_message_at: null },
];

const conversations: Conversation[] = [
  {
    id: 11,
    contact_id: 1,
    source: "whatsapp",
    status: "open",
    current_stage: "qualification",
    matched_property_id: "RTF-023",
    current_suggested_property_id: null,
    latest_message_text: "Can view Rivervale this weekend?",
    latest_message_timestamp_ms: 1,
    latest_message_direction: "inbound",
  },
  {
    id: 12,
    contact_id: 2,
    source: "fake_chat",
    status: "open",
    current_stage: "end",
    matched_property_id: "RTF-028",
    current_suggested_property_id: null,
    latest_message_text: "Budget around 3000",
    latest_message_timestamp_ms: 2,
    latest_message_direction: "inbound",
  },
  {
    id: 13,
    contact_id: 2,
    source: "whatsapp",
    status: "open",
    current_stage: "triage",
    matched_property_id: null,
    current_suggested_property_id: null,
    latest_message_text: "General enquiry",
    latest_message_timestamp_ms: 3,
    latest_message_direction: "inbound",
  },
];

const properties: PropertyRecord[] = [
  {
    id: 1,
    property_id: "RTF-023",
    property_name: "185D Rivervale Crescent",
    status: "available",
    property_type: "HDB",
    bedrooms: 3,
    bathrooms: 2,
    asking_rent: 3400,
    available_from: "Immediate",
    full_address: "185D Rivervale Crescent",
    property_url: null,
    propertyguru_listing_id: "9000002",
    landlord_profile_requirements: "Family",
    tenant_facing_caveats: "",
    created_at: "",
    updated_at: "",
    media: [],
  },
  {
    id: 2,
    property_id: "RTF-028",
    property_name: "625 Senja Road",
    status: "available",
    property_type: "HDB",
    bedrooms: 3,
    bathrooms: 2,
    asking_rent: 3200,
    available_from: "Immediate",
    full_address: "625 Senja Road",
    property_url: null,
    propertyguru_listing_id: null,
    landlord_profile_requirements: "No pets",
    tenant_facing_caveats: "",
    created_at: "",
    updated_at: "",
    media: [],
  },
];

const rows = buildInboxRows(contacts, conversations);

function conversation(overrides: Partial<Conversation>): Conversation {
  return {
    id: 99,
    contact_id: 1,
    source: "whatsapp",
    status: "open",
    current_stage: "qualification",
    matched_property_id: null,
    current_suggested_property_id: null,
    latest_message_text: null,
    latest_message_timestamp_ms: null,
    latest_message_direction: null,
    ...overrides,
  };
}

assert(rows.length === 1, "buildInboxRows should only include WhatsApp conversations with a matched property");
assert(filterInboxRows(rows, "all", "", properties).length === 1, "all filter should show matched WhatsApp conversations");
assert(filterInboxRows(rows, "all", "rivervale", properties).length === 1, "search should match property name/address");
assert(filterInboxRows(rows, "all", "weekend", properties).length === 1, "search should match latest message text");
assert(filterInboxRows(rows, "all", "no-such-query", properties).length === 0, "search should exclude non-matching rows");

assert(queueActionForConversation(conversation({ current_stage: "end" })).label === "Unit matched", "stage should not drive the simplified inbox label");
assert(queueActionForConversation(conversation({ current_stage: "qualification" })).label === "Unit matched", "active AI stages should not be exposed in the simplified inbox");
assert(queueActionForConversation(conversation({ status: "closed" })).label === "Closed", "closed conversations should be marked closed");
assert(queueActionForConversation(conversation({}), { ...contacts[0], status: "paused" }).label === "Paused", "paused contact should override conversation stage");
assert(queueActionForConversation(conversation({ current_stage: "end" })).tone === "success", "matched rows should use success tone");
assert(queueActionForConversation(conversation({ current_stage: "qualification" })).tone === "success", "active AI stages should use the matched tone");
assert(matchesQueueSearch(rows[0], "demo", properties), "search should match contact details");

console.log("queueState tests passed");
