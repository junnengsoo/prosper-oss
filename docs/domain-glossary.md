# Domain Glossary

This glossary keeps public documentation, backend names, and dashboard labels aligned for the rental-enquiry reference implementation.

## Rental Enquiry

An inbound tenant message that appears to ask about renting a configured property. Sale enquiries, general spam, and post-match qualification workflows are out of scope for this release.

## Rental Listing

The operator-configured property record used for matching and replies. It includes a string Rental Listing ID (`property_id`), property name, listing URL, availability status, rent, address, bedroom and bathroom counts, availability date, tenant-facing caveats, and optional local media.

Prosper is rental-focused in this release. The listing `property_type` field is retained as intentional extensibility for future listing categories; it is not evidence that sale enquiries or non-rental workflows are currently supported.

## Contact

The stable tenant-facing chat identity known to Prosper. `chat_jid` is the Contact identity used for dedupe and conversation ownership. `display_name` and `phone` are optional channel metadata for display and operator context; phone metadata is not treated as tenant identity and is not used to merge or authenticate Contacts.

## Conversation

The lifecycle container for Messages and pipeline state for one Contact. A Contact can have historical Conversations, but the implemented workflow allows only one active Conversation per Contact. New Conversations for the same Contact are created only after the prior active Conversation is closed, such as through reset.

`status` records lifecycle (`active` or `closed`). `current_stage` records where the active workflow is routed (`rental_listing_matching`, `manual_review`, or `end`). Lifecycle status and current pipeline stage are separate concepts: a Conversation can be active while routed to manual review, and closing a Conversation moves it to the end stage.

## Playbook / Auto Replies

The per-listing deterministic reply configuration. After DeepSeek returns a validated available-listing match, Prosper renders enabled Playbook blocks into outbound text and media actions. The model does not author free-form tenant replies in the final action step.

## Simulator Conversation

The supported local evaluation path in the dashboard. It accepts fake tenant messages, stores them as local conversations, runs the same backend pipeline used by bridge inbound messages, and displays the resulting transcript and audit records.

## WhatsApp Bridge

The TypeScript adapter around Baileys. It normalizes WhatsApp events for the backend and attempts outbound sends when asked. It is authenticated separately from browser dashboard sessions and is experimental.

## Stage Run

An audit record for one pipeline stage. Stage runs capture the stage name, input snapshot, output, model, status, error details, and timestamp so a reviewer can inspect why a conversation was matched, routed to manual review, or blocked.

Stage Runs usually belong to a Conversation, but `conversation_id` is nullable by design. Pre-conversation triage can run before Prosper has decided to create a Conversation, and those Stage Runs still keep their input snapshot and outcome for audit.

## Message Raw Type

The source event marker retained with a Message. For inbound channel events it records the source raw message type when available, such as bridge text metadata. For internally planned outbound records it can mark Prosper-generated actions such as `action_send` or media actions. This marker supports provenance and auto-reply dedupe; it is not a user-facing message category.

## Runtime Config

Operator-adjustable local configuration stored in `app_config`. Runtime Config has an allowed-key boundary: this release accepts only the supported keys declared by the backend contract, currently `pause_ai` and `send_lock`. Unknown config keys are rejected instead of being silently stored.

## Outbound Action

A deterministic action plan produced after validated stage results. Actions can send Playbook text, send configured media, hand off to review, or do nothing. Bridge delivery is best effort and is not treated as a durable side effect.

## Manual Review

The safe route when the system should not send an automated response. Manual review is used for unavailable listings, ambiguous matches, provider failures, invalid model output, paused contacts, human takeover, and send-lock blocks.

## Send Lock

A local operator switch that prevents outbound action execution. It is useful for review walkthroughs where inbound processing and audit inspection should continue but tenant replies should not be sent.

## Local SQLite

The local storage posture for this release. SQLite files under `runtime/` are resettable local data. The CLI supports a narrow verified pilot backup for SQLite plus managed property media, while database migrations, in-place upgrades, restore workflows, and managed retention policies are deliberately deferred.
