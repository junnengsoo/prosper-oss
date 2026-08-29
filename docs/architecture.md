# Architecture

Prosper is organized around a shared inbound rental-enquiry workflow with separate channel and presentation layers. The terms in this document are defined in the [Domain Glossary](domain-glossary.md), and the release boundary is recorded in [ADR 0001](adr/0001-public-reference-boundaries.md).

## Components

- `backend/app/pipeline.py` coordinates DeepSeek-backed triage and rental listing matching stages.
- `backend/app/database/models.py` stores contacts, conversations, messages, stage runs, properties, Playbooks, and outbound state in one application scope.
- `backend/app/database/connection.py` owns the SQLAlchemy engine, sessions, and table creation; `backend/app/database/seed.py` owns sample data.
- `backend/app/schemas.py` defines the validated request and stage-result contracts.
- `backend/app/actions.py` turns validated stage results into deterministic outbound actions.
- `backend/app/services.py` owns message ingestion, deduplication, state checks, and bridge retries.
- `backend/app/media_storage.py` stores uploaded property media under the configured runtime directory and serves it through the authenticated API.
- `frontend/` provides the inbox, Rental Listing configuration, Playbook / Auto Replies configuration, audit views, and Simulator Conversation path.
- `bridge/` is an optional, authenticated, experimental Baileys adapter that maps WhatsApp events into the backend message contract and forwards outbound actions on a best-effort basis.
- `backend/app/auth.py` protects the dashboard with a signed single-user session cookie; bridge requests use a separate bridge token.

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
