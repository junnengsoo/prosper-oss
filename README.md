# Prosper Reference

Prosper is a public rental-enquiry reference implementation for reviewing how an AI-assisted property workflow can be built with explicit state, local audit records, deterministic action planning, and human review boundaries.

The repository is prepared for technical inspection by one reviewing company. It makes no claim that the code is licensed for reuse, generally deployable, or ready to operate as a managed messaging service. The current scope is inbound rental enquiries only.

## What It Demonstrates

- DeepSeek-backed triage of inbound rental messages.
- Rental Listing retrieval from operator-configured local data.
- Rental Listing matching against configured availability, address, rent, and tenant-facing notes.
- Schema-validated model outputs before any action planning.
- Deterministic Playbook / Auto Replies rendering from validated results.
- inbound deduplication by channel chat and message identifier.
- Review-safe fallbacks for provider errors, malformed JSON, unavailable listings, paused automation, and unsafe actions.
- Stage-run audit records for each pipeline decision.
- A Simulator Conversation path that exercises the same backend pipeline used by live inbound bridge messages.
- Local media uploads stored under `runtime/media`.
- Optional authenticated WhatsApp Bridge connectivity through Baileys.
- Single-user dashboard authentication using signed HTTP-only cookies.

DeepSeek is the sole explicit model provider in this reference. The application can start without `DEEPSEEK_API_KEY`, but the DeepSeek-backed happy path and live evals require that key. Without it, model-backed stages record a safe manual-review fallback instead of generating a tenant reply.

## Documentation Map

- [Architecture](docs/architecture.md) describes the backend pipeline, dashboard, and optional bridge boundary.
- [Project Briefing](docs/project-presentation.md) gives reviewers a concise guide to architecture, workflow, safety decisions, tradeoffs, setup, and inspection points.
- [Tradeoff Inventory](docs/tradeoff-inventory.md) records what Prosper intentionally keeps, removes, and defers.
- [Reliability Notes](docs/reliability.md) documents duplicate handling, state gates, model failures, outbound delivery, and local retention.
- [Evaluation Strategy](docs/evaluation.md) separates deterministic checks from live model-quality checks.
- [Domain Glossary](docs/domain-glossary.md) defines Rental Listing, Playbook, Simulator Conversation, Stage Run, and related terms used in the code and UI.
- [ADR 0001: Public Reference Boundaries](docs/adr/0001-public-reference-boundaries.md) records the release boundary for DeepSeek, SQLite, the bridge, and deferred capabilities.

## Architecture

```text
Inbound message
      |
      v
Normalize and deduplicate
      |
      v
Safety gates and conversation state
      |
      v
Triage -> rental listing matching -> handoff or completion
      |
      v
Schema validation and stage audit record
      |
      v
Deterministic Playbook action planner
      |
      +--> simulator transcript
      +--> WhatsApp bridge
      +--> manual review queue
```

The FastAPI backend owns business logic, persistence, prompts, state transitions, and outbound action planning. The React dashboard provides the operator interface, Rental Listing setup, Playbook / Auto Replies setup, audit inspection, and Simulator Conversation surface. The TypeScript bridge owns optional WhatsApp connectivity and forwards normalized messages to the backend.

Dashboard sessions use one configured application password rather than a user database. Successful login creates a signed, expiring `HttpOnly` cookie. The WhatsApp Bridge remains separately authenticated with `PROSPER_BRIDGE_TOKEN`; browser sessions are not used for bridge callbacks.

## Local Setup

Use Python 3.11, `uv`, Node.js 22, and npm.

1. Copy the environment template:

   ```bash
   cp .env.example .env
   ```

2. Set the DeepSeek key for the complete happy path:

   ```env
   DEEPSEEK_API_KEY=replace-with-your-key
   DEEPSEEK_BASE_URL=https://api.deepseek.com
   DEEPSEEK_MODEL=deepseek-reasoner
   ```

   Keep real secrets out of commits, logs, screenshots, and PR bodies. If a separate worktree is used for review, copy the local `.env` into that worktree because ignored files are not shared between worktrees.

3. Install dependencies:

   ```bash
   uv sync --locked --extra dev --python 3.11
   cd frontend && npm ci
   cd ../bridge && npm ci
   cd ..
   ```

4. Initialize resettable local data and seeded sample configuration:

   ```bash
   .venv/bin/python -m app.cli init-db
   ```

5. Start the supported local launcher:

   ```bash
   scripts/dev.sh
   ```

6. Open the dashboard at `http://127.0.0.1:5173`.

For a protected dashboard session, set these backend variables before launching:

```env
AUTH_REQUIRED=true
ACCESS_PASSWORD=choose-a-private-password
SESSION_SECRET=generate-a-long-random-secret
SESSION_TTL_SECONDS=86400
AUTH_COOKIE_SECURE=true
```

The frontend uses same-origin `/api/...` requests, so any reviewed deployment should serve the dashboard and backend API from the same HTTPS origin.

## Manual Walkthrough

The supported evaluation path is the Simulator Conversation. It uses the same backend pipeline as live inbound bridge messages while avoiding any WhatsApp account requirement.

1. Open `http://127.0.0.1:5173` and select `Properties`.
2. Create or edit a `Rental Listing`. Fill the listing name, status, listing URL, rent, availability date, bedrooms, bathrooms, and address. Save once the required fields are complete.
3. Reopen the listing and select `Auto Replies`. Enable the Playbook / Auto Replies and set at least one tenant-facing reply for an available match. Save and return to the listing list.
4. Select `Simulator`, start a new Simulator Conversation, and submit a tenant rental enquiry that mentions the configured Rental Listing.
5. Confirm the simulator transcript shows the inbound message and, when `DEEPSEEK_API_KEY` is configured and the listing is available, the deterministic Playbook reply.
6. Select `Inbox`, open the matched lead, and inspect `Prosper Audit`. Expand `Decision timeline` and confirm stage runs include `triage`, `rental_listing_matching`, and `outbound_actions`.
7. Submit an enquiry for an unavailable or ambiguous listing. The expected behavior is manual review with no model-generated outbound reply.
8. Optional: open the WhatsApp panel only after starting the authenticated WhatsApp Bridge. Confirm the bridge reports its connection state or pairing QR. Treat this as an experimental adapter check, not an official WhatsApp integration.

For the optional authenticated WhatsApp Bridge, keep the backend and bridge on loopback during local review or configure a private token:

```env
PROSPER_BRIDGE_TOKEN=replace-with-a-private-token
PROSPER_BRIDGE_BASE_URL=http://127.0.0.1:8788
PROSPER_BRIDGE_HOST=127.0.0.1
PROSPER_BRIDGE_PORT=8788
```

Existing local `WHATSAPP_PA_*` bridge variables and `BRIDGE_BASE_URL` remain accepted as compatibility aliases, but new setup should use the `PROSPER_*` names shown above.

The bridge can forward inbound messages and can attempt outbound sends. The best-effort outbound delivery boundary is explicit: Prosper records the attempted action and bridge result, but the reference does not implement a transactional outbox, provider idempotency key management, or durable channel-level delivery guarantees.

## Privacy and Local Data

This repository stores reference data locally by default. `.env.example` points SQLite at `runtime/prosper.sqlite3`, and media uploads are stored under `runtime/media`.

SQLite is reset-only SQLite storage for this release. Database migrations are intentionally out of scope; existing local databases are disposable. To reset local application state, stop the services and remove the relevant `runtime/*.sqlite3` files, then rerun:

```bash
.venv/bin/python -m app.cli init-db
```

The simulator also has a scoped reset for fake chat data:

```bash
scripts/fake_chat_smoke.sh --reset
```

Do not enter real tenant personal data during public-review walkthroughs unless the reviewer has separately approved that handling. DeepSeek-backed runs send prompt inputs to the configured DeepSeek-compatible endpoint. Local audit records may include inbound text, matched listing details, model outputs, errors, and outbound action records.

Inbound deduplication is limited to the normalized chat identifier and message identifier received from the simulator or bridge. It protects against replayed inbound events with the same identifiers; it does not prove global person identity, prevent semantic duplicates typed as new messages, or replace channel-level delivery controls.

The bridge depends on Baileys, whose optional link-preview path has an unresolved advisory (`GHSA-4gp8-rjrq-ch6q`) through `link-preview-js`. Prosper does not declare or call `link-preview-js` directly; the remaining Baileys advisory is accepted as non-blocking for this reference bridge and should be re-reviewed before any operational WhatsApp use.

## Verification

The clean-checkout CI suite is defined in `.github/workflows/verify.yml`. It runs backend, frontend, full-stack browser acceptance, and bridge jobs on GitHub with Python 3.11 and Node.js 22.

Run the aggregate local verification from a clean checkout:

```bash
scripts/test.sh
```

That script installs locked Python and Node dependencies, runs backend tests, checks Python compilation and shell syntax, rejects legacy removed surfaces, builds and tests the frontend, runs the desktop and mobile browser acceptance suite with deterministic DeepSeek-compatible responses, typechecks the bridge, and runs bridge tests.

With local services already running through `scripts/dev.sh`, exercise seeded fake-chat scenarios:

```bash
scripts/fake_chat_smoke.sh --reset
```

Run live DeepSeek evals only when `DEEPSEEK_API_KEY` is configured:

```bash
scripts/eval.sh
```

## Deferred Capabilities

The current public-review scope deliberately excludes:

- qualification forms, screening workflows, sale enquiry behavior, and post-match transaction automation;
- provider-neutral model routing or multi-provider parity;
- durable job queues, lease ownership, transactional outbox processing, and channel idempotency keys;
- a general-purpose identity system or multi-user roles;
- hosted object storage, migration management, backup/restore operations, and managed retention policies;
- an official WhatsApp Business Platform integration.

These are future engineering directions, not hidden requirements for this release.

## Publication Readiness

Before publication, perform the separate final history squash or curation step outside this PR. This ticket prepares documentation and verification evidence only; it does not merge, close issues manually, publish, or rewrite branch history.
