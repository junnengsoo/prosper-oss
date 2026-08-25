import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ChevronLeft, Clock, GripVertical, Image as ImageIcon, Phone, Plus, Send, Trash2 } from "lucide-react";
import type { PlaybookBlock, PropertyPlaybookInput, PropertyRecord } from "../api";
import type { AutoReplyField } from "../propertyEditorState";
import {
  DEFAULT_AUTO_REPLY_SEQUENCES,
  DEFAULT_MESSAGE_DELAY_SECONDS,
  PLACEHOLDERS,
  autoReplyBlocks,
  playbookWithAutoReplyBlocks,
} from "../playbookState";
import {
  EmptyState,
  GalleryPreview,
  initials,
  replacePlaceholders,
} from "../viewHelpers";
import "./playbook.css";

export function AutoRepliesEditor({
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
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const itemIds = blocks.map((_, index) => blockId(index));

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

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (disabled || !over || active.id === over.id) return;
    const oldIndex = itemIds.indexOf(String(active.id));
    const newIndex = itemIds.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    commit(arrayMove(blocks, oldIndex, newIndex));
  }

  return (
    <div className="autoReplySequence">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={itemIds} strategy={verticalListSortingStrategy}>
          {blocks.map((block, index) => (
            <SortableAutoReplyBlock
              key={blockId(index)}
              id={blockId(index)}
              block={block}
              index={index}
              blocks={blocks}
              disabled={disabled}
              onUpdate={(patch) => updateBlock(index, patch)}
              onRemove={() => removeBlock(index)}
              onAppendPlaceholder={(placeholder) => appendPlaceholder(index, placeholder)}
            />
          ))}
        </SortableContext>
      </DndContext>
      <button type="button" className="secondaryButton autoReplyAddButton" onClick={addMessage} disabled={disabled}>
        <Plus size={16} /> Add message
      </button>
    </div>
  );
}

function SortableAutoReplyBlock({
  id,
  block,
  index,
  blocks,
  disabled,
  onUpdate,
  onRemove,
  onAppendPlaceholder,
}: {
  id: string;
  block: PlaybookBlock;
  index: number;
  blocks: PlaybookBlock[];
  disabled: boolean;
  onUpdate: (patch: Partial<PlaybookBlock>) => void;
  onRemove: () => void;
  onAppendPlaceholder: (placeholder: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id, disabled });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  if (block.type === "delay") {
    return (
      <div ref={setNodeRef} style={style} className={`autoReplyDelay ${isDragging ? "dragging" : ""}`}>
        <button type="button" className="dragHandle" aria-label="Reorder delay" disabled={disabled} {...attributes} {...listeners}>
          <GripVertical size={15} />
        </button>
        <Clock size={16} />
        <span>Wait</span>
        <input
          type="number"
          min="0"
          max="30"
          step="0.5"
          value={block.seconds ?? DEFAULT_MESSAGE_DELAY_SECONDS}
          onChange={(event) => onUpdate({ seconds: Number(event.target.value) })}
          disabled={disabled}
        />
        <span>seconds</span>
        <button type="button" className="iconButton" onClick={onRemove} disabled={disabled} aria-label="Remove delay">
          <Trash2 size={15} />
        </button>
      </div>
    );
  }

  return (
    <label ref={setNodeRef} style={style} className={`autoReplyTextarea ${isDragging ? "dragging" : ""}`}>
      <span>
        <button type="button" className="dragHandle" aria-label="Reorder auto reply block" disabled={disabled} {...attributes} {...listeners}>
          <GripVertical size={15} />
        </button>
        Message {messageNumber(blocks, index)}
      </span>
      <textarea
        aria-label={`Message ${messageNumber(blocks, index)}`}
        rows={4}
        value={block.text ?? ""}
        onChange={(event) => onUpdate({ text: event.target.value })}
        disabled={disabled}
      />
      <div className="placeholderRow">
        {PLACEHOLDERS.map((placeholder) => (
          <button key={placeholder} type="button" onClick={() => onAppendPlaceholder(placeholder)} disabled={disabled}>
            {placeholder}
          </button>
        ))}
        {blocks.filter((item) => item.type === "message").length > 1 && (
          <button type="button" className="dangerTextButton" onClick={onRemove} disabled={disabled}>
            <Trash2 size={14} /> Remove
          </button>
        )}
      </div>
    </label>
  );
}

function blockId(index: number): string {
  return `playbook-block-${index}`;
}

function messageNumber(blocks: PlaybookBlock[], index: number): number {
  return blocks.slice(0, index + 1).filter((block) => block.type === "message").length;
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
