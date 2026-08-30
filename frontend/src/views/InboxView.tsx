import { Building2, Search } from "lucide-react";
import type { Contact, Conversation, Message, PropertyRecord, StageRun } from "../api";
import { buildInboxRows, queueActionForConversation } from "../queueState";
import {
  classNames,
  EmptyState,
  formatMoney,
  formatTime,
  GalleryPreview,
  initials,
  MessageThread,
  propertyAvailabilityLabel,
  propertyAvailabilityTone,
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
  selectedStageRuns: StageRun[];
  onOpenProperty?: () => void;
}) {
  const triageRun = selectedStageRuns.find((run) => run.stage === "triage");
  const matchingRun = selectedStageRuns.find((run) => run.stage === "rental_listing_matching" || run.stage === "unit_matching");
  const outboundRun = selectedStageRuns.find((run) => run.stage === "outbound_actions");
  const matchingOutput = stageOutput(matchingRun);
  const outboundOutput = stageOutput(outboundRun);
  const triageOutput = stageOutput(triageRun) ?? objectOutput(outboundOutput?.triage);
  const outboundMessage = [...messages].reverse().find((message) => message.direction === "outbound" || message.direction === "from_me");
  const responseDecision = responseDecisionForConversation(outboundMessage, selectedProperty, outboundRun);

  return (
    <section className="inboxLayout">
      <aside className="leadListPane">
        <div className="searchBox">
          <Search size={18} />
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search for leads" />
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
            </div>
            <div className="leadSummaryGrid">
              {selectedProperty ? (
                <article className="matchedPropertyCard">
                  <GalleryPreview media={selectedProperty.media} />
                  <div>
                    <span className="summaryEyebrow">Matched listing</span>
                    <strong>{selectedProperty.property_name}</strong>
                    <span>{selectedProperty.full_address || "Matched property"}</span>
                    <b>{formatMoney(selectedProperty.asking_rent)}</b>
                    <span className={classNames("badge", propertyAvailabilityTone(selectedProperty.status), "matchedPropertyStatus")}>
                      {propertyAvailabilityLabel(selectedProperty.status)}
                    </span>
                  </div>
                  {onOpenProperty && <button type="button" onClick={onOpenProperty}>Edit</button>}
                </article>
              ) : (
                <article className="matchedPropertyCard noMatchCard">
                  <span className="summaryEyebrow">No listing matched</span>
                </article>
              )}
              <article className="responseCard">
                <div className="cardTitle">
                  <span>Prosper response</span>
                  <b className={classNames("badge", responseDecision.tone)}>{responseDecision.label}</b>
                </div>
                <p>{responseDecision.detail}</p>
              </article>
            </div>
            <details className="aiDetails">
              <summary>Prosper Audit</summary>
              <div className="aiDetailsBody">
                <div className="aiDecision">
                  <div className="cardTitle">
                    <span>Triage</span>
                    <b className={classNames("badge", statusTone(triageRun?.status ?? stageStatusFromOutput(triageOutput)))}>
                      {triageRun ? stageDecision(triageRun, "Not recorded") : outputDecision(triageOutput, "Not recorded")}
                    </b>
                  </div>
                  <p>{triageRun ? stageSummary(triageRun) : outputSummary(triageOutput, "No triage decision recorded.")}</p>
                </div>
                <div className="aiDecision">
                  <div className="cardTitle">
                    <span>Rental listing matching</span>
                    <b className={classNames("badge", statusTone(matchingRun?.status))}>{stageDecision(matchingRun, "Not run")}</b>
                  </div>
                  <p>{stageSummary(matchingRun)}</p>
                  {typeof matchingOutput?.matched_by === "string" && matchingOutput.matched_by && (
                    <span className="detailMeta">Matched by {matchingOutput.matched_by.replace(/_/g, " ")}</span>
                  )}
                </div>
                <div className="aiDecision">
                  <div className="cardTitle">
                    <span>Response decision</span>
                    <b className={classNames("badge", responseDecision.tone)}>{responseDecision.label}</b>
                  </div>
                  <p>{responseDecision.detail}</p>
                </div>
              </div>
            </details>
            <MessageThread messages={messages} />
          </>
        )}
      </section>
    </section>
  );
}

function stageOutput(run?: StageRun | null): Record<string, unknown> | null {
  if (!run?.output_json) return null;
  try {
    const parsed = JSON.parse(run.output_json) as unknown;
    return objectOutput(parsed);
  } catch {
    return null;
  }
}

function objectOutput(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function stageDecision(run: StageRun | undefined, fallback: string): string {
  if (!run) return fallback;
  if (run.status === "success") return "Passed";
  if (run.status === "manual_review") return "Needs review";
  return statusLabel(run.status);
}

function stageStatusFromOutput(output: Record<string, unknown> | null): string | null {
  if (!output) return null;
  if (output.stage_status === "manual_review" || output.match_status === "manual_review") return "manual_review";
  return "success";
}

function outputDecision(output: Record<string, unknown> | null, fallback: string): string {
  const status = stageStatusFromOutput(output);
  if (status === "success") return "Passed";
  if (status === "manual_review") return "Needs review";
  return fallback;
}

function outputSummary(output: Record<string, unknown> | null, fallback: string): string {
  if (!output) return fallback;
  const reason = output.reason ?? output.summary ?? output.status ?? output.match_status;
  return typeof reason === "string" && reason ? reason : fallback;
}

function responseDecisionForConversation(
  outboundMessage: Message | undefined,
  property: PropertyRecord | null,
  outboundRun?: StageRun,
): { label: string; tone: "success" | "warning" | "danger" | "neutral"; detail: string } {
  if (outboundMessage) {
    return property?.status === "available"
      ? { label: "Available reply sent", tone: "success", detail: "Prosper sent the available-listing Playbook reply." }
      : { label: "Reply sent", tone: "success", detail: "Prosper sent an automated follow-up reply." };
  }

  if (outboundRun?.status === "manual_review") {
    return { label: "Manual Review", tone: "warning", detail: "Prosper did not send an automated reply for this enquiry." };
  }

  if (outboundRun?.status === "blocked" || outboundRun?.status === "failed") {
    return { label: "Reply blocked", tone: "danger", detail: stageSummary(outboundRun) };
  }

  return { label: "No reply sent", tone: "warning", detail: "No automated response was sent for this enquiry." };
}
