import { Clock, Plus, Send, Trash2 } from "lucide-react";
import type { Conversation, Message } from "../api";
import { initials, MessageThread, truncate } from "../viewHelpers";
import "./simulator.css";

export function SimulatorView({
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
