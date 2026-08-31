# ADR 0001: Public Reference Boundaries

## Status

Accepted

## Context

Prosper is being prepared for public technical review. The repository needs to show the implemented rental-enquiry workflow clearly without implying broader licensing, deployment, channel, or product commitments.

The current implemented surface is a single-operator inbound WhatsApp rental-enquiry workflow orchestrator with a local dashboard, local SQLite middleman state, DeepSeek-backed model stages, deterministic Playbook replies, and a Baileys-based WhatsApp Bridge. WhatsApp remains the visible channel transcript.

## Decision

Prosper will be documented as a local, single-operator rental-enquiry workflow orchestrator.

The supported happy path uses DeepSeek through `DEEPSEEK_API_KEY`. The simulator and deterministic tests may run against fake DeepSeek-compatible responses, but live model-backed review requires a configured DeepSeek key.

SQLite remains reset-only local reference storage. Existing local databases are disposable, and migrations are outside the release scope.

Prosper uses local SQLite for this experimental repo. The database is intended for disposable local testing data and single-operator pilot state, not managed service storage. For this repo, schema changes are handled by resetting the local database rather than migrating existing files.

The schema keeps a rental-focused `Rental Listing` table with string listing identifiers. `property_id` and external listing IDs are strings because source systems and property portals do not guarantee numeric-only identifiers. The `property_type` field stays even though the supported workflow is rental-focused; it is an intentional extension point, not a promise that sale or non-rental enquiry workflows are implemented.

Contacts own Conversation history through channel identity. A Contact can have historical Conversations, but Prosper enforces one active Conversation per Contact through the current workflow and SQLite boundary. New Conversations for the same Contact are created only after the prior active Conversation is closed, such as through reset.

Conversation lifecycle status and current pipeline stage remain separate fields. `status` is constrained to lifecycle values such as active or closed, while `current_stage` is constrained to the implemented routing stages. Latest-message display state is derived from Messages at read time rather than cached on Contact or Conversation rows.

Stage Runs are retained as the local audit mechanism, including full input snapshots. `conversation_id` is nullable so pre-conversation triage can be audited before a Conversation exists. Stage Run context can include tenant message text and model inputs; it may be stored locally, included in verified backups, and sent to DeepSeek-backed endpoints during model work.

Messages retain `source`, `sender_jid`, and `raw_type` metadata. The raw-type marker records source event provenance or internal outbound action markers, and it supports auto-reply dedupe without turning into a user-facing taxonomy.

Runtime Config remains in `app_config`, but only keys explicitly allowed by the backend contract may be written. The current allowed-key boundary prevents arbitrary config drift while keeping operator toggles such as AI pause and send lock local and inspectable.

The Simulator Conversation is the supported local walkthrough path because it exercises the same backend pipeline as live inbound bridge messages without requiring a paired phone. It is not the WhatsApp channel transcript.

The WhatsApp Bridge is authenticated, experimental, and best effort. It is the repo's WhatsApp channel adapter, implemented with Baileys rather than the official WhatsApp Business Platform. Outbound delivery records attempts and results, but this release does not include a transactional outbox or channel idempotency guarantees.

Qualification forms, screening, sale enquiry behavior, production operations, multi-user identity, provider-neutral routing, and durable queue processing are deferred.

Final history squash or curation is a separate publication step and is not performed as part of this implementation ticket.

## Consequences

The repo provides a reproducible local path through setup, configuration, readiness checks, simulator execution, audit inspection, backup, restore, cleanup, and bridge pairing.

Documentation must be explicit about local privacy and retention boundaries because prompt inputs, messages, stage outputs, media references, and outbound action records can be stored in local SQLite and runtime directories.

Future work can replace individual boundaries, but those changes should be justified by concrete reliability, reuse, or operational needs rather than implied by this reference release.

The retained fields and nullable audit relationships are deliberate schema choices. Removing them should require a new ADR or issue-specific justification because they encode auditability, provenance, local resettable storage, and future listing extensibility decisions.
