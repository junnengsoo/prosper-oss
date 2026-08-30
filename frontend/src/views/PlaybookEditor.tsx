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
import { ChevronLeft, Clock, GripVertical, Image as ImageIcon, MessageSquare, Phone, Plus, Send, Trash2 } from "lucide-react";
import { useRef } from "react";
import type { PlaybookBlock, PropertyPlaybookInput, PropertyRecord, RuntimeConfigValues } from "../api";
import type { AutoReplyField } from "../propertyEditorState";
import {
  DEFAULT_AUTO_REPLY_SEQUENCES,
  DEFAULT_MESSAGE_DELAY_SECONDS,
  PLACEHOLDERS,
  autoReplyBlocks,
  playbookWithAutoReplyBlocks,
} from "../playbookState";
import {
  classNames,
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
  config: RuntimeConfigValues;
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

        <article className="replyGroup sequenceGroup">
          <header className="replyGroupHeader">
            <div>
              <strong>When Unit Is Available</strong>
            </div>
            <b className="badge success">Current status</b>
          </header>
          <AutoReplySequence
            field="availableInitial"
            blocks={autoReplyBlocks(draft, "availableInitial")}
            setDraft={setDraft}
            disabled={disabled}
          />
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
  const blockIdsRef = useRef<string[]>([]);
  const blockIdCounterRef = useRef(0);
  while (blockIdsRef.current.length < blocks.length) {
    blockIdsRef.current.push(`${field}-block-${blockIdCounterRef.current}`);
    blockIdCounterRef.current += 1;
  }
  if (blockIdsRef.current.length > blocks.length) {
    blockIdsRef.current.splice(blocks.length);
  }
  const sortableItems = blockIdsRef.current.slice(0, blocks.length);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

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

  function addDelay() {
    const nextBlocks: PlaybookBlock[] = [...blocks];
    if (nextBlocks.length === 0 || nextBlocks[nextBlocks.length - 1].type === "delay") return;
    nextBlocks.push({ type: "delay", seconds: DEFAULT_MESSAGE_DELAY_SECONDS });
    commit(nextBlocks);
  }

  function addGallery() {
    const nextBlocks: PlaybookBlock[] = [...blocks];
    if (nextBlocks.length > 0 && nextBlocks[nextBlocks.length - 1].type !== "delay") {
      nextBlocks.push({ type: "delay", seconds: DEFAULT_MESSAGE_DELAY_SECONDS });
    }
    nextBlocks.push({ type: "gallery", mode: "enabled_property_gallery" });
    commit(nextBlocks);
  }

  function removeBlock(index: number) {
    const nextBlocks = blocks.filter((_, blockIndex) => blockIndex !== index);
    blockIdsRef.current.splice(index, 1);
    commit(nextBlocks.length > 0 ? nextBlocks : DEFAULT_AUTO_REPLY_SEQUENCES[field]);
  }

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (disabled || !over || active.id === over.id) return;
    const oldIndex = sortableItems.indexOf(String(active.id));
    const newIndex = sortableItems.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    blockIdsRef.current = arrayMove(blockIdsRef.current, oldIndex, newIndex);
    commit(arrayMove(blocks, oldIndex, newIndex));
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={sortableItems} strategy={verticalListSortingStrategy}>
        <div className="autoReplySequence">
          {blocks.map((block, index) => (
            <SortableAutoReplyBlock
              key={sortableItems[index]}
              id={sortableItems[index]}
              block={block}
              index={index}
              blocks={blocks}
              disabled={disabled}
              onUpdate={(patch) => updateBlock(index, patch)}
              onRemove={() => removeBlock(index)}
              onAppendPlaceholder={(placeholder) => appendPlaceholder(index, placeholder)}
            />
          ))}
          <div className="autoReplyAddRow">
            <button type="button" className="secondaryButton autoReplyAddButton" onClick={addMessage} disabled={disabled}>
              <Plus size={16} /> Add message
            </button>
            <button type="button" className="secondaryButton autoReplyAddButton" onClick={addDelay} disabled={disabled || blocks[blocks.length - 1]?.type === "delay"}>
              <Clock size={16} /> Add delay
            </button>
            <button type="button" className="secondaryButton autoReplyAddButton" onClick={addGallery} disabled={disabled}>
              <ImageIcon size={16} /> Add gallery
            </button>
          </div>
        </div>
      </SortableContext>
    </DndContext>
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
  const dragHandle = (
    <button
      type="button"
      className="dragHandle"
      aria-label="Drag to reorder"
      disabled={disabled}
      {...attributes}
      {...listeners}
    >
      <GripVertical size={16} />
    </button>
  );

  if (block.type === "delay") {
    return (
      <div ref={setNodeRef} style={style} className={classNames("sequenceNodeFrame", isDragging && "dragging")}>
        <div className="sequenceNode delayNode">
          <div className="sequenceNodeHeader">
            {dragHandle}
            <Clock size={16} />
            <span>Delay</span>
            <button type="button" className="iconButton" onClick={onRemove} disabled={disabled} aria-label="Remove delay">
              <Trash2 size={15} />
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (block.type === "gallery") {
    return (
      <div ref={setNodeRef} style={style} className={classNames("sequenceNodeFrame", isDragging && "dragging")}>
        <div className="sequenceNode galleryNode">
          <div className="sequenceNodeHeader">
            {dragHandle}
            <ImageIcon size={16} />
            <span>Gallery</span>
            <button type="button" className="iconButton" onClick={onRemove} disabled={disabled} aria-label="Remove gallery">
              <Trash2 size={15} />
            </button>
          </div>
          <strong>Send enabled property media</strong>
          <p>Uses this property for available replies.</p>
        </div>
      </div>
    );
  }

  return (
    <div ref={setNodeRef} style={style} className={classNames("sequenceNodeFrame", isDragging && "dragging")}>
      <div className="sequenceNode messageNode">
        <div className="sequenceNodeHeader">
          {dragHandle}
          <MessageSquare size={16} />
          <span>Message {messageNumber(blocks, index)}</span>
          {blocks.filter((item) => item.type === "message").length > 1 && (
            <button type="button" className="iconButton" onClick={onRemove} disabled={disabled} aria-label="Remove message">
              <Trash2 size={15} />
            </button>
          )}
        </div>
        <label className="autoReplyTextarea">
          <span>Send WhatsApp message</span>
          <textarea
            aria-label={`Message ${messageNumber(blocks, index)}`}
            rows={4}
            value={block.text ?? ""}
            onChange={(event) => onUpdate({ text: event.target.value })}
            disabled={disabled}
          />
          <div className="placeholderRow">
            {PLACEHOLDERS.map((placeholder) => (
              <button key={placeholder.token} type="button" onClick={() => onAppendPlaceholder(placeholder.token)} disabled={disabled}>
                {placeholder.label}
              </button>
            ))}
          </div>
        </label>
      </div>
    </div>
  );
}

function messageNumber(blocks: PlaybookBlock[], index: number): number {
  return blocks.slice(0, index + 1).filter((block) => block.type === "message").length;
}

function WhatsAppPreview({ property, blocks, config }: { property: PropertyRecord | null; blocks: PlaybookBlock[]; config: RuntimeConfigValues }) {
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
          if (block.type === "delay") return <div key={index} className="delayPill">Delay</div>;
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
