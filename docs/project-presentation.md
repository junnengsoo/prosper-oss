# Project Briefing

Prosper is a local-first WhatsApp rental-enquiry reference implementation. It shows how an AI-assisted property workflow can accept inbound tenant messages, classify intent, match configured Rental Listings, render deterministic Playbook replies, and preserve local audit records.

The supported scope is intentionally narrow: inbound WhatsApp rental enquiries, one operator dashboard, resettable SQLite storage, DeepSeek-backed Pipeline stages, Stage Runs for auditability, and an authenticated WhatsApp Bridge. The [Tradeoff Inventory](tradeoff-inventory.md) summarizes what was kept, removed, and deferred during the final cleanup pass.

## Architecture

The backend owns the business workflow. The Pipeline coordinates model-backed triage and Rental Listing matching, validates structured stage results, records Stage Runs, and hands validated decisions to the deterministic action planner. Message ingestion, deduplication, state gates, Playbook rendering, outbound records, media metadata, and bridge retries stay within the backend boundary.

The dashboard is the operator surface. It supports Rental Listing setup, Playbook / Auto Replies configuration, Simulator Conversations, inbox review, audit inspection, local media previews, and single-user session authentication. The dashboard talks to same-origin backend APIs and does not share browser session state with the bridge.

The bridge is the WhatsApp channel adapter. It normalizes WhatsApp events into the backend's inbound message contract and attempts outbound sends when the backend requests them. It is authenticated separately from the dashboard, exposes only operational status, and remains experimental.

## Workflow

1. A tenant message enters through the WhatsApp Bridge or the Simulator Conversation path.
2. The backend normalizes the message, deduplicates it by channel chat and message identifier, and checks pause, ignore, closed-conversation, human-takeover, and send-lock state.
3. The Pipeline runs triage and Rental Listing matching against configured local data.
4. Each model-backed stage is schema-validated and written as a Stage Run.
5. The action planner renders enabled Playbook blocks only after a validated available-listing match.
6. The result appears in the simulator transcript, the inbox, the audit view, and, when the bridge is configured, the outbound channel attempt record.

The Simulator Conversation is the local walkthrough path because it exercises the same backend workflow without requiring a paired phone.

## Safety Decisions

- Model output is treated as structured input to validate, not as the final tenant-facing message.
- Provider errors, malformed JSON, invalid shapes, paused contacts, unavailable listings, ambiguous matches, and unsafe actions route to Manual Review.
- Stage Runs store local evidence for why a conversation was matched, blocked, or handed off.
- The send lock can stop outbound side effects while preserving inbound processing and audit inspection.
- Dashboard authentication uses a signed, expiring `HttpOnly` cookie. Bridge callbacks use a separate machine-to-machine token.
- Local data and media are disposable review artifacts by default. DeepSeek-backed runs send prompt inputs to the configured DeepSeek-compatible endpoint, so real tenant personal data should not be used unless that handling has been separately approved.

## Tradeoffs

Prosper keeps the local workflow complete enough to inspect end to end, while avoiding infrastructure that would imply a managed service. Reset-only SQLite, local media, a single-user password, and synchronous request handling are enough for review but are not substitutes for migrations, scheduled backups, team identity, durable jobs, or managed retention.

The project keeps DeepSeek as the explicit live provider so prompts, schema contracts, and evals can stay concrete. Provider-neutral routing is deferred until there is a real compatibility target.

The bridge remains central because WhatsApp is the product channel, while the Baileys implementation keeps the repo runnable without an official WhatsApp Business Platform setup. Outbound delivery is best effort; a deployment target would need a transactional outbox, channel idempotency keys, and stronger process recovery before tenant messaging should be treated as durable.

## Setup

Use Python 3.11, `uv`, Node.js 22, and npm.

1. Copy `.env.example` to `.env`.
2. Set `DEEPSEEK_API_KEY` for the live model-backed happy path. Without it, model stages use the Manual Review fallback.
3. Install locked backend, frontend, and bridge dependencies.
4. Initialize resettable local data with `.venv/bin/python -m app.cli init-db`.
5. Run `.venv/bin/python -m app.cli doctor` before startup.
6. Start the local launcher with `scripts/dev.sh`.
7. Run `.venv/bin/python -m app.cli doctor --strict-runtime` after the backend, dashboard, and bridge are running.
8. Open `http://127.0.0.1:5173` and use the Simulator Conversation walkthrough from the README.

The WhatsApp Bridge uses `PROSPER_BRIDGE_TOKEN`, `PROSPER_BRIDGE_BASE_URL`, `PROSPER_BRIDGE_HOST`, and `PROSPER_BRIDGE_PORT`. Existing local bridge aliases are accepted only for compatibility; new setup should use the Prosper-named variables.

## What To Inspect

- Backend Pipeline and action planning: follow triage, Rental Listing matching, schema validation, Stage Runs, Manual Review fallback, and Playbook rendering.
- Persistence boundary: inspect reset-only SQLite models, seed data, local media metadata, and table creation.
- Dashboard workflow: review Rental Listing setup, Playbook / Auto Replies, Simulator Conversation, inbox queue behavior, audit inspection, and signed-cookie authentication.
- Bridge boundary: review token handling, event normalization, status reporting, backend forwarding, pairing behavior, and best-effort outbound sends.
- Documentation and verification: compare this briefing with the README, Architecture, Reliability Notes, Evaluation Strategy, Domain Glossary, ADR 0001, and the aggregate `scripts/test.sh` path.

Useful entry points are stable by concept: backend workflow coordination in `backend/app/pipeline.py` and `backend/app/services.py`, contracts in `backend/app/schemas.py`, deterministic actions in `backend/app/actions.py`, persistence in `backend/app/database/`, dashboard surfaces in `frontend/src/views/`, bridge behavior in `bridge/src/`, and public-review guards in `backend/tests/test_public_docs.py` and `scripts/legacy_surface_check.py`.

## Verification

Run the aggregate local verification from the repository root:

```bash
scripts/test.sh
```

That path installs locked dependencies, runs backend tests, checks Python and shell syntax, rejects removed public surfaces, builds and tests the dashboard, runs full-stack browser acceptance against deterministic DeepSeek-compatible responses, typechecks the bridge, and runs bridge tests.
