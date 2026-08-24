import { Building2, MessageSquare, Phone, Search } from "lucide-react";
import type { Contact, Conversation, Message, PropertyRecord, StageRun } from "../api";
import { buildInboxRows, queueActionForConversation } from "../queueState";
import {
  classNames,
  EmptyState,
  formatMoney,
  formatTime,
  formatDateTime,
  GalleryPreview,
  initials,
  MessageThread,
  stageSummary,
  statusLabel,
  statusTone,
  truncate,
} from "../viewHelpers";
import "./inbox.css";

export function InboxView({
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
