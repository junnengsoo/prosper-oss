# Architecture

Prosper is organized as a single-operator, stateful workflow orchestrator for inbound WhatsApp rental enquiries. The terms in this document are defined in the [Domain Glossary](domain-glossary.md), and the release boundary is recorded in [ADR 0001](adr/0001-public-reference-boundaries.md).

## Component Ownership

- `backend/app/pipeline.py` coordinates DeepSeek-backed triage and rental listing matching stages.
- `backend/app/database/models.py` stores contacts, conversations, messages, stage runs, properties, Playbooks, and outbound state in one application scope.
- `backend/app/database/connection.py` owns the SQLAlchemy engine, sessions, and table creation; `backend/app/database/seed.py` owns sample data. Prosper currently does not include schema migrations.
- `backend/app/schemas.py` defines the validated request and stage-result contracts.
- `backend/app/actions.py` turns validated stage results into deterministic outbound actions.
- `backend/app/services.py` owns message ingestion, deduplication, state checks, and bridge retries.
- `backend/app/media_storage.py` stores uploaded property media under the configured runtime directory and serves it through the authenticated API.
- `frontend/` provides the inbox, Rental Listing configuration, Playbook / Auto Replies configuration, audit views, and Simulator Conversation path.
- `bridge/` is an authenticated, experimental Baileys adapter that maps WhatsApp events into the backend message contract and forwards outbound actions on a best-effort basis.
- `backend/app/auth.py` protects the dashboard with a signed single-user session cookie; bridge requests use a separate bridge token.

WhatsApp remains the tenant-visible channel transcript. Prosper's local SQLite database is the application and middleman store for workflow state, listing configuration, Playbooks, dedupe evidence, Stage Runs, and outbound action records. The bridge is a connector between those two boundaries, not the owner of business decisions.

## Processing Flow

1. The Simulator Conversation path or an inbound channel sends a normalized inbound message.
2. The backend rejects unsupported, duplicate, stale, paused, or ignored work.
3. A conversation is created or resumed.
4. The current pipeline stage receives structured business context and prior conversation messages.
5. The model result is parsed, validated, and recorded as a `StageRun`.
6. A deterministic action planner decides whether a reply, media action, handoff, or no action is allowed.
7. The selected channel executes the action and records the result.

Dashboard requests authenticate through `/api/auth/login`, `/api/auth/session`, and `/api/auth/logout`. The cookie is only for the browser dashboard. It is not required for bridge callbacks, which use their own machine-to-machine bridge token. Backend-to-bridge operations use the same token for status, QR, reconnect, text, and media requests; the bridge health check remains unauthenticated for local process supervision.

The model proposes structured triage and listing-matching decisions. DeepSeek is the only explicitly supported live provider for this reference. The action layer renders Playbook replies only from validated matching results.

SQLite is local, reset-only reference storage for conversations, messages, listings, Playbooks, media metadata, app config, and stage runs. Runtime media files live under the configured media root, which defaults to `runtime/media`.

## Persistence And Backups

Prosper stores the operational state it needs to continue the workflow locally: inbound and outbound message records, Contact and Conversation rows, Rental Listings, Playbook / Auto Replies settings, Runtime Config, Stage Runs, outbound action attempts, and property media metadata. The database is a file-backed SQLite database by default at `runtime/prosper.sqlite3`; managed property media defaults to `runtime/media`.

The CLI backup path snapshots the active SQLite database and managed property media into a verified archive. That archive can include tenant messages, Conversation state, Stage Run context, model inputs and outputs, action records, media metadata, and managed property media. Environment files, bridge pairing state, logs, caches, build output, and previous backups are intentionally excluded.

Restore replaces the local SQLite database and managed property media only after explicit operator confirmation and while local services are stopped. Because WhatsApp pairing credentials are not part of Prosper backups, a restored worktree may need WhatsApp re-pairing before bridge traffic resumes.

## Failure Behavior

Duplicate inbound events with the same normalized chat and message identifiers are ignored. Paused automation, ignored contacts, closed conversations, human takeover, unavailable listings, ambiguous matches, missing Playbooks, provider errors, invalid JSON, invalid model output, and unsafe outbound actions route to Manual Review or a no-send result instead of a generated tenant reply.

Outbound WhatsApp delivery is best effort. Prosper records planned actions and bridge responses, but this release does not include a transactional outbox, durable background jobs, channel idempotency keys, or restart-proof delivery guarantees.

## Security And Privacy

The dashboard is protected by one configured operator password and a signed `HttpOnly` cookie. The bridge uses a separate token and refuses unsafe non-loopback bindings when no private token is configured. The doctor reports whether DeepSeek and bridge-token settings are present or unsafe without printing secret values.

DeepSeek-backed stages send prompt input to the configured DeepSeek-compatible endpoint. For real tenant data, that means tenant message text and Stage Run context may leave the local machine for model processing. The same tenant data and stage context may also remain in local SQLite and verified backup archives until the operator explicitly cleans up the selected database, media, or backup files.

## Scaling Limits And Tradeoffs

Prosper is built for a single local operator and synchronous request handling. SQLite, local media files, a single dashboard password, the Baileys bridge, manual cleanup, manual backups, and reset-based schema changes keep the pilot inspectable without claiming managed service infrastructure.

This release does not include schema migrations, scheduled backups, automatic retention deletion, team roles, durable queues, lease ownership, hosted media storage, or an official WhatsApp Business Platform integration. Those are future engineering choices, not hidden prerequisites for the current pilot.
