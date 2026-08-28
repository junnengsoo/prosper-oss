import { useMemo, useState } from "react";
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
import { CheckCircle2, ChevronLeft, GripVertical, Save, Trash2, Upload } from "lucide-react";
import type { PropertyInput, PropertyMedia, PropertyPlaybookInput, PropertyRecord } from "../api";
import {
  EDITOR_SECTIONS,
  type EditorSection,
  type MediaPathForm,
  type PropertyRequiredField,
} from "../propertyEditorState";
import { classNames, MediaPreview, statusTone } from "../viewHelpers";
import { AutoRepliesEditor } from "./PlaybookEditor";
import "./propertyEditor.css";

export function PropertyEditorView({
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
  onUpdateMedia,
  onReorderMedia,
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
  mediaPathForm: MediaPathForm;
  setMediaPathForm: (form: MediaPathForm) => void;
  playbookDraft: PropertyPlaybookInput;
  playbookDirty: boolean;
  setPlaybookDraft: (draft: PropertyPlaybookInput | ((current: PropertyPlaybookInput) => PropertyPlaybookInput)) => void;
  config: Record<string, string>;
  onBack: () => void;
  onSave: () => void;
  onDelete?: () => void;
  onAddMediaPath: () => void;
  onUploadMedia: (file: File) => void;
  onUpdateMedia: (media: PropertyMedia, patch: Partial<Pick<PropertyMedia, "caption" | "enabled" | "media_type" | "sort_order">>) => void;
  onReorderMedia: (media: PropertyMedia[]) => void;
  onDeleteMedia: (media: PropertyMedia) => void;
}) {
  const previewProperty = property ? { ...property, ...form, media } : null;
  const currentStepIndex = EDITOR_SECTIONS.findIndex((item) => item.key === section);
  const previousSection = EDITOR_SECTIONS[currentStepIndex - 1]?.key;
  const nextSection = EDITOR_SECTIONS[currentStepIndex + 1]?.key;

  function goBack() {
    if (previousSection) setSection(previousSection);
    else onBack();
  }

  return (
    <section className="propertyEditorPage">
      <header className="editorHeader">
        <button className="secondaryButton" onClick={onBack}><ChevronLeft size={18} /> Back</button>
        <div>
          <h2>{property?.property_name || "New Listing"}</h2>
          <span className={classNames("badge", statusTone(form.status))}>{form.status || "draft"}</span>
        </div>
        <div className="editorHeaderActions">
          {property && onDelete && <button className="dangerButton" onClick={onDelete}><Trash2 size={16} /> Delete</button>}
        </div>
      </header>
      <div className="editorProgress"><span style={{ width: `${((currentStepIndex + 1) / EDITOR_SECTIONS.length) * 100}%` }} /></div>
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
            <GalleryEditor
              media={media}
              disabled={!property}
              mediaPathForm={mediaPathForm}
              setMediaPathForm={setMediaPathForm}
              onAddMediaPath={onAddMediaPath}
              onUploadMedia={onUploadMedia}
              onUpdateMedia={onUpdateMedia}
              onReorderMedia={onReorderMedia}
              onDeleteMedia={onDeleteMedia}
            />
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
      <footer className="editorStickyActions">
        <button type="button" className="secondaryButton" onClick={goBack} disabled={sectionBusy}>
          <ChevronLeft size={16} /> Back
        </button>
        <div className="editorStickyActionGroup">
          {nextSection && (
            <button type="button" className="secondaryButton" onClick={() => setSection(nextSection)} disabled={sectionBusy}>
              Next
            </button>
          )}
          <button type="button" className="primaryButton" onClick={onSave}>
            <Save size={16} /> Save & Exit
          </button>
        </div>
      </footer>
    </section>
  );
}

function GalleryEditor({
  media,
  disabled,
  mediaPathForm,
  setMediaPathForm,
  onAddMediaPath,
  onUploadMedia,
  onUpdateMedia,
  onReorderMedia,
  onDeleteMedia,
}: {
  media: PropertyMedia[];
  disabled: boolean;
  mediaPathForm: MediaPathForm;
  setMediaPathForm: (form: MediaPathForm) => void;
  onAddMediaPath: () => void;
  onUploadMedia: (file: File) => void;
  onUpdateMedia: (media: PropertyMedia, patch: Partial<Pick<PropertyMedia, "caption" | "enabled" | "media_type" | "sort_order">>) => void;
  onReorderMedia: (media: PropertyMedia[]) => void;
  onDeleteMedia: (media: PropertyMedia) => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const mediaIds = useMemo(() => media.map((item) => String(item.id)), [media]);

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (disabled || !over || active.id === over.id) return;
    const oldIndex = mediaIds.indexOf(String(active.id));
    const newIndex = mediaIds.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    onReorderMedia(arrayMove(media, oldIndex, newIndex));
  }

  return (
    <EditorPanel title="Gallery" body="Images and videos Prosper sends after this unit is matched.">
      <div className="galleryGrid">
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={mediaIds} strategy={verticalListSortingStrategy}>
            {media.map((item) => (
              <SortableGalleryTile
                key={item.id}
                media={item}
                disabled={disabled}
                onUpdate={(patch) => onUpdateMedia(item, patch)}
                onDelete={() => onDeleteMedia(item)}
              />
            ))}
          </SortableContext>
        </DndContext>
      </div>
      <div className="inlineForm galleryAddForm">
        <input value={mediaPathForm.file_path} onChange={(event) => setMediaPathForm({ ...mediaPathForm, file_path: event.target.value })} placeholder="Local file path or storage reference" disabled={disabled} />
        <input value={mediaPathForm.caption} onChange={(event) => setMediaPathForm({ ...mediaPathForm, caption: event.target.value })} placeholder="Caption" disabled={disabled} />
        <select value={mediaPathForm.media_type} onChange={(event) => setMediaPathForm({ ...mediaPathForm, media_type: event.target.value as "photo" | "video" })} disabled={disabled}>
          <option value="photo">Photo</option>
          <option value="video">Video</option>
        </select>
        <label className="mediaToggle">
          <input type="checkbox" checked={mediaPathForm.enabled} onChange={(event) => setMediaPathForm({ ...mediaPathForm, enabled: event.target.checked })} disabled={disabled} />
          Enabled
        </label>
        <button className="secondaryButton" onClick={onAddMediaPath} disabled={disabled || !mediaPathForm.file_path.trim()}>Add path</button>
      </div>
      <label className={classNames("uploadDrop", disabled && "disabled")}>
        <Upload size={18} />
        Upload image/video
        <input type="file" accept="image/*,video/*" disabled={disabled} onChange={(event) => {
          const file = event.target.files?.[0];
          event.currentTarget.value = "";
          if (file) onUploadMedia(file);
        }} />
      </label>
    </EditorPanel>
  );
}

function SortableGalleryTile({
  media,
  disabled,
  onUpdate,
  onDelete,
}: {
  media: PropertyMedia;
  disabled: boolean;
  onUpdate: (patch: Partial<Pick<PropertyMedia, "caption" | "enabled" | "media_type" | "sort_order">>) => void;
  onDelete: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: String(media.id), disabled });
  const [captionDraft, setCaptionDraft] = useState(media.caption);
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <article ref={setNodeRef} style={style} className={`galleryTile ${isDragging ? "dragging" : ""}`}>
      <div className="galleryTileHeader">
        <button type="button" className="dragHandle" aria-label={`Reorder ${media.caption || media.file_path}`} disabled={disabled} {...attributes} {...listeners}>
          <GripVertical size={15} />
        </button>
        <span>#{media.sort_order}</span>
      </div>
      <MediaPreview media={media} />
      <label>
        <span>Caption</span>
        <input value={captionDraft} onChange={(event) => setCaptionDraft(event.target.value)} onBlur={() => onUpdate({ caption: captionDraft })} disabled={disabled} />
      </label>
      <div className="mediaControls">
        <select value={media.media_type} onChange={(event) => onUpdate({ media_type: event.target.value as "photo" | "video" })} disabled={disabled}>
          <option value="photo">Photo</option>
          <option value="video">Video</option>
        </select>
        <label className="mediaToggle">
          <input type="checkbox" checked={media.enabled} onChange={(event) => onUpdate({ enabled: event.target.checked })} disabled={disabled} />
          Enabled
        </label>
      </div>
      <span>{media.file_path}</span>
      <button className="dangerButton" onClick={onDelete} disabled={disabled}><Trash2 size={15} /> Remove</button>
    </article>
  );
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
