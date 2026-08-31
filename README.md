# Prosper

Prosper is an experimental WhatsApp rental-enquiry app for exploring how a single operator can use AI-assisted triage, local audit records, deterministic action planning, and explicit Manual Review boundaries.

The repo is intentionally small and local-first. It works best as a single-user workflow for inbound WhatsApp rental enquiries, with resettable SQLite storage, a Baileys-based WhatsApp bridge, and a simulator for manual walkthroughs.

## What It Demonstrates

- DeepSeek-backed triage of inbound rental messages.
- Rental Listing retrieval from operator-configured local data.
- Rental Listing matching against configured availability, address, rent, and tenant-facing notes.
- Schema-validated model outputs before any action planning.
- Deterministic Playbook / Auto Replies rendering from validated results.
- inbound deduplication by channel chat and message identifier.
- Manual Review fallbacks for provider errors, malformed JSON, unavailable listings, paused automation, and unsafe actions.
- Stage-run audit records for each pipeline decision.
- A Simulator Conversation path that exercises the same backend pipeline used by live inbound WhatsApp messages.
- Local media uploads stored under `runtime/media`.
- Authenticated WhatsApp Bridge connectivity through Baileys.
- Single-user dashboard authentication using signed HTTP-only cookies.

DeepSeek is the sole explicit model provider in this experimental repo. The application can start without `DEEPSEEK_API_KEY`, but the DeepSeek-backed happy path and live evals require that key. Without it, model-backed stages record a safe Manual Review fallback instead of generating a tenant reply.

## Documentation Map

- [Architecture](docs/architecture.md) describes the backend pipeline, dashboard, and WhatsApp bridge boundary.
- [Project Briefing](docs/project-presentation.md) gives a concise guide to architecture, workflow, safety decisions, tradeoffs, setup, and key files.
- [Tradeoff Inventory](docs/tradeoff-inventory.md) records what Prosper intentionally keeps, removes, and defers.
- [Reliability Notes](docs/reliability.md) documents duplicate handling, state gates, model failures, outbound delivery, and local retention.
- [Evaluation Strategy](docs/evaluation.md) separates deterministic checks from live model-quality checks.
- [Domain Glossary](docs/domain-glossary.md) defines Rental Listing, Playbook, Simulator Conversation, Stage Run, and related terms used in the code and UI.

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

The FastAPI backend owns business logic, persistence, prompts, state transitions, and outbound action planning. The React dashboard provides the operator interface, Rental Listing setup, Playbook / Auto Replies setup, audit view, and Simulator Conversation surface. The TypeScript bridge owns WhatsApp connectivity and forwards normalized messages to the backend.

Dashboard sessions use one configured application password rather than a user database. Successful login creates a signed, expiring `HttpOnly` cookie. The WhatsApp Bridge remains separately authenticated with `PROSPER_BRIDGE_TOKEN`; browser sessions are not used for bridge callbacks.

## Local Setup

Use Python 3.11, `uv`, Node.js 22 or newer, and npm. The repository includes `.node-version` with the CI baseline version.

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

   Keep real secrets out of commits, logs, screenshots, and PR bodies. If a separate worktree is used locally, copy the local `.env` into that worktree because ignored files are not shared between worktrees.

3. Run the setup script:

   ```bash
   scripts/setup.sh
   ```

   This installs the locked Python and Node dependencies, creates `.env` if it does not already exist, and initializes resettable local data with seeded sample configuration.
   Internally, the backend install uses `uv sync --locked --extra dev --python 3.11`: `--locked` keeps dependency versions exactly aligned with `uv.lock`, `--extra dev` includes the local test and development tools, and `--python 3.11` selects the supported backend Python version.

4. Start the supported local launcher:

   ```bash
   scripts/dev.sh
   ```

5. Open the dashboard at `http://127.0.0.1:5173`.

For a protected dashboard session, set these backend variables before launching:

```env
AUTH_REQUIRED=true
ACCESS_PASSWORD=choose-a-private-password
SESSION_SECRET=generate-a-long-random-secret
SESSION_TTL_SECONDS=86400
AUTH_COOKIE_SECURE=true
```

The frontend uses same-origin `/api/...` requests, so any deployment should serve the dashboard and backend API from the same HTTPS origin.

## Manual Walkthrough

The primary channel is WhatsApp. For local manual walkthroughs, the Simulator Conversation uses the same backend pipeline as live inbound bridge messages without requiring a paired phone.

1. Open `http://127.0.0.1:5173` and select `Properties`.
2. Create or edit a `Rental Listing`. Fill the listing name, status, listing URL, rent, availability date, bedrooms, bathrooms, and address. Save once the required fields are complete.
3. Reopen the listing and select `Auto Replies`. Enable the Playbook / Auto Replies and set at least one tenant-facing reply for an available match. Save and return to the listing list.
4. Select `Simulator`, start a new Simulator Conversation, and submit a tenant rental enquiry that mentions the configured Rental Listing.
5. Confirm the simulator transcript shows the inbound message and, when `DEEPSEEK_API_KEY` is configured and the listing is available, the deterministic Playbook reply.
6. Select `Inbox`, open the matched lead, and inspect `Prosper Audit`. Expand `Decision timeline` and confirm stage runs include `triage`, `rental_listing_matching`, and `outbound_actions`.
7. Submit an enquiry for an unavailable or ambiguous listing. The expected behavior is Manual Review with no model-generated outbound reply.
8. Open the WhatsApp panel after starting the authenticated WhatsApp Bridge. Confirm the bridge reports its connection state or pairing QR. Treat this as an experimental Baileys adapter check, not an official WhatsApp Business Platform integration.

For the authenticated WhatsApp Bridge, keep the backend and bridge on loopback during local use or configure a private token:

```env
PROSPER_BRIDGE_TOKEN=replace-with-a-private-token
PROSPER_BRIDGE_BASE_URL=http://127.0.0.1:8788
PROSPER_BRIDGE_HOST=127.0.0.1
PROSPER_BRIDGE_PORT=8788
```

Existing local `WHATSAPP_PA_*` bridge variables and `BRIDGE_BASE_URL` remain accepted as compatibility aliases, but new setup should use the `PROSPER_*` names shown above.

The bridge can forward inbound messages and can attempt outbound sends. The best-effort outbound delivery boundary is explicit: Prosper records the attempted action and bridge result, but this repo does not implement a transactional outbox, provider idempotency key management, or durable channel-level delivery guarantees.

## Privacy and Local Data

This repository stores local testing data by default. `.env.example` points SQLite at `runtime/prosper.sqlite3`, and media uploads are stored under `runtime/media`.

SQLite is reset-only SQLite storage for this release. Database migrations are intentionally out of scope; existing local databases are disposable. To reset local application state, stop the services and remove the relevant `runtime/*.sqlite3` files, then rerun:

```bash
.venv/bin/python -m app.cli init-db
```

Create a verified backup of the active SQLite database and managed property media with:

```bash
.venv/bin/python -m app.cli backup
```

The backup archive is written under `runtime/backups` by default and includes a manifest with file sizes and checksums. It does not include environment files, bridge authentication state, logs, caches, build output, or previous backups. In-place migrations, scheduled backups, and managed retention workflows remain out of scope.

Restore a verified Prosper backup only after stopping local services:

```bash
.venv/bin/python -m app.cli restore runtime/backups/prosper-backup-example.tar.gz
```

Restore validates the archive before replacing current data, keeps a rollback snapshot under `runtime/restore-rollbacks`, and reports that WhatsApp pairing credentials are not included, so re-pairing may be required.

Cleanup remains an explicit operator action. Remove selected operational data or selected backup archives with:

```bash
.venv/bin/python -m app.cli cleanup-data --database --media
.venv/bin/python -m app.cli cleanup-backups prosper-backup-example.tar.gz
```

Prosper does not automatically delete backups or runtime data.

The simulator also has a scoped reset for fake chat data:

```bash
scripts/fake_chat_smoke.sh --reset
```

Do not enter real tenant personal data during walkthroughs unless everyone involved has separately approved that handling. DeepSeek-backed runs send prompt inputs to the configured DeepSeek-compatible endpoint. Local audit records may include inbound text, matched listing details, model outputs, errors, and outbound action records.

Inbound deduplication is limited to the normalized chat identifier and message identifier received from the simulator or bridge. It protects against replayed inbound events with the same identifiers; it does not prove global person identity, prevent semantic duplicates typed as new messages, or replace channel-level delivery controls.

The bridge depends on Baileys, whose optional URL-card metadata path has an unresolved advisory (`GHSA-4gp8-rjrq-ch6q`) through a transitive dependency. Prosper does not declare or call that dependency directly; the remaining Baileys advisory is accepted as non-blocking for this experimental bridge and should be reassessed before any operational WhatsApp use.

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

The current experimental scope deliberately excludes:

- qualification forms, screening workflows, sale enquiry behavior, and post-match transaction automation;
- provider-neutral model routing or multi-provider parity;
- durable job queues, lease ownership, transactional outbox processing, and channel idempotency keys;
- a general-purpose identity system or multi-user roles;
- hosted object storage, migration management, scheduled backups, and managed retention policies;
- an official WhatsApp Business Platform integration.

These are future engineering directions, not hidden requirements for this release.
