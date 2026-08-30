import type { PlaybookBlock, PropertyPlaybook, PropertyPlaybookInput } from "./api";
import type { AutoReplyField } from "./propertyEditorState";

export const PLACEHOLDERS: Array<{ token: string; label: string }> = [
  { token: "{unit_info}", label: "Unit info" },
  { token: "{property_guru_listing}", label: "Listing URL" },
];
export const DEFAULT_MESSAGE_DELAY_SECONDS = 1;

export const DEFAULT_AUTO_REPLIES: Record<AutoReplyField, string> = {
  availableInitial: "Hi, yes this unit is still available.",
};

const DEFAULT_VIEWING_MESSAGE = "Please share your preferred viewing time and move-in date, and I will check the next available slot.";

export const DEFAULT_AUTO_REPLY_SEQUENCES: Record<AutoReplyField, PlaybookBlock[]> = {
  availableInitial: [
    { type: "message", text: DEFAULT_AUTO_REPLIES.availableInitial },
    { type: "delay", seconds: DEFAULT_MESSAGE_DELAY_SECONDS },
    { type: "message", text: DEFAULT_VIEWING_MESSAGE },
  ],
};

const STOCK_GALLERY_CAPTIONS = new Set([
  "Here are some photos of the unit.",
]);

export function emptyPlaybook(): PropertyPlaybookInput {
  return {
    enabled: false,
    initial_reply_blocks: [],
  };
}

export function playbookToInput(playbook: PropertyPlaybook | null | undefined): PropertyPlaybookInput {
  return {
    enabled: playbook?.enabled ?? false,
    initial_reply_blocks: playbook?.initial_reply_blocks ?? [],
  };
}

export function defaultPlaybookInput(): PropertyPlaybookInput {
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

export function effectivePlaybookInput(playbook: PropertyPlaybook | null | undefined): PropertyPlaybookInput {
  const input = playbookToInput(playbook);
  return playbook?.id || hasWorkflowBlocks(input) ? input : defaultPlaybookInput();
}

export function effectiveAutoReplyInput(playbook: PropertyPlaybook | null | undefined): PropertyPlaybookInput {
  const input = effectivePlaybookInput(playbook);
  return {
    enabled: input.enabled,
    initial_reply_blocks: autoReplyWorkflowBlocks("availableInitial", input.initial_reply_blocks),
  };
}

export function compactAutoReplyBlocks(blocks: PlaybookBlock[], field: AutoReplyField): PlaybookBlock[] {
  const editableBlocks = blocks.filter((block) => {
    if (block.type === "delay") return true;
    if (block.type === "gallery") return true;
    if (block.type !== "message") return false;
    return !STOCK_GALLERY_CAPTIONS.has((block.text || "").trim());
  });
  return editableBlocks.length > 0 ? editableBlocks : DEFAULT_AUTO_REPLY_SEQUENCES[field];
}

export function autoReplyWorkflowBlocks(field: AutoReplyField, blocks: PlaybookBlock[]): PlaybookBlock[] {
  return compactAutoReplyBlocks(blocks, field);
}

export function autoReplyBlocks(input: PropertyPlaybookInput, field: AutoReplyField): PlaybookBlock[] {
  return compactAutoReplyBlocks(input.initial_reply_blocks, field);
}

export function playbookWithAutoReplyBlocks(input: PropertyPlaybookInput, field: AutoReplyField, blocks: PlaybookBlock[]): PropertyPlaybookInput {
  return {
    ...input,
    initial_reply_blocks: compactAutoReplyBlocks(blocks, field),
  };
}

export function cleanAutoReplyBlocksForSave(blocks: PlaybookBlock[], field: AutoReplyField): PlaybookBlock[] {
  const cleaned: PlaybookBlock[] = [];
  for (const block of compactAutoReplyBlocks(blocks, field)) {
    if (block.type === "message") {
      if (block.text?.trim()) cleaned.push({ type: "message", text: block.text });
      continue;
    }
    if (block.type === "delay" && cleaned.length > 0 && cleaned[cleaned.length - 1].type !== "delay") {
      cleaned.push({ type: "delay", seconds: block.seconds ?? DEFAULT_MESSAGE_DELAY_SECONDS });
      continue;
    }
    if (block.type === "gallery") {
      cleaned.push({ type: "gallery", mode: "enabled_property_gallery" });
    }
  }
  while (cleaned[cleaned.length - 1]?.type === "delay") cleaned.pop();
  if (!cleaned.some((block) => block.type === "message")) cleaned.push(...DEFAULT_AUTO_REPLY_SEQUENCES[field]);
  return cleaned;
}

export function cleanAutoRepliesForSave(input: PropertyPlaybookInput): PropertyPlaybookInput {
  return {
    enabled: input.enabled,
    initial_reply_blocks: cleanAutoReplyBlocksForSave(input.initial_reply_blocks, "availableInitial"),
  };
}
