import {
  autoReplyBlocks,
  cleanAutoRepliesForSave,
  defaultPlaybookInput,
  effectiveAutoReplyInput,
  playbookWithAutoReplyBlocks,
} from "../src/playbookState";
import type { PlaybookBlock, PropertyPlaybook } from "../src/api";

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

const defaultInput = defaultPlaybookInput();
assert(defaultInput.initial_reply_blocks.at(-1)?.type === "gallery", "default playbook should end with the property gallery block");
assert(autoReplyBlocks(defaultInput, "availableInitial").filter((block) => block.type === "message").length === 2, "default playbook should expose two editable messages");
assert(autoReplyBlocks(defaultInput, "availableInitial").at(-1)?.type === "gallery", "default playbook should expose gallery as an editable block");

const legacyPlaybook: PropertyPlaybook = {
  id: 1,
  property_id: "PROP-001",
  enabled: true,
  initial_reply_blocks: [
    { type: "message", text: "Hi, thanks for enquiring about {unit_info}. I'm the listing agent." },
    { type: "delay", seconds: 2 },
    { type: "message", text: "Here are some photos of the unit." },
    { type: "gallery", mode: "enabled_property_gallery" },
  ],
  created_at: "",
  updated_at: "",
};

const effective = effectiveAutoReplyInput(legacyPlaybook);
assert(effective.enabled, "effective playbook should preserve enabled state");
assert(effective.initial_reply_blocks[0].type === "message", "first block should remain a message");
assert(effective.initial_reply_blocks[0].text === "Hi, thanks for enquiring about {unit_info}. I'm the listing agent.", "existing playbook text should not be rewritten");
assert(!effective.initial_reply_blocks.some((block) => block.type === "message" && block.text === "Here are some photos of the unit."), "stock gallery caption should not be editable");
assert(effective.initial_reply_blocks.at(-1)?.type === "gallery", "effective playbook should retain gallery ordering");

const editedBlocks: PlaybookBlock[] = [
  { type: "message", text: "First {unit_info}" },
  { type: "delay", seconds: 1.5 },
  { type: "message", text: "Second {property_guru_listing}" },
];
const edited = playbookWithAutoReplyBlocks(defaultInput, "availableInitial", editedBlocks);
assert(edited.initial_reply_blocks.length === 3, "edited playbook should preserve exactly the edited block list");
assert(!edited.initial_reply_blocks.some((block) => block.type === "gallery"), "edited playbook should not force a removed gallery block back in");

const editedWithGallery = playbookWithAutoReplyBlocks(defaultInput, "availableInitial", [
  ...editedBlocks,
  { type: "delay", seconds: 1 },
  { type: "gallery", mode: "enabled_property_gallery" },
]);
assert(editedWithGallery.initial_reply_blocks.at(-1)?.type === "gallery", "edited playbook should preserve gallery when it is present");

const cleaned = cleanAutoRepliesForSave({
  enabled: true,
  initial_reply_blocks: [
    { type: "delay", seconds: 4 },
    { type: "message", text: "  " },
    { type: "message", text: "Ready to view?" },
    { type: "delay", seconds: 3 },
    { type: "gallery", mode: "enabled_property_gallery" },
  ],
});
assert(cleaned.initial_reply_blocks[0].type === "message", "save cleanup should drop leading delays and blank messages");
assert(cleaned.initial_reply_blocks[0].text === "Ready to view?", "save cleanup should preserve non-empty message text");
assert(cleaned.initial_reply_blocks.at(-1)?.type === "gallery", "save cleanup should end with gallery");

console.log("playbookState tests passed");
