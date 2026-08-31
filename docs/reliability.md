# Reliability Notes

Prosper uses explicit controls around model-backed rental-enquiry work in a single-operator local pilot. WhatsApp remains the visible channel transcript, while Prosper stores local workflow state and operational evidence in SQLite. See the [Domain Glossary](domain-glossary.md) for shared terms and [ADR 0001](adr/0001-public-reference-boundaries.md) for the public release boundary.

## Duplicate and Replay Safety

Inbound messages are uniquely identified by chat and message ID. Replayed bridge events therefore do not create duplicate stored messages or duplicate conversations.

This inbound deduplication boundary is intentionally narrow. It does not identify a person across channels, merge semantically duplicated messages with different message IDs, or guarantee outbound idempotency.

## State Gates

Processing is blocked when the global AI pause is enabled, a contact is paused or ignored, a conversation is closed, or a prior human reply indicates manual takeover. These checks happen before model work and before outbound execution.

## Model Failure

Provider errors, invalid JSON, and invalid output shapes are recorded as failed stage runs. The system returns a review-safe result and does not send a model-generated reply.

## Outbound Safety

Playbooks render outbound content deterministically from validated stage results. The send lock, action safety checks, bridge retry behavior, and outbound action records provide separate controls around the final side effect.

The Baileys bridge is experimental, and outbound WhatsApp delivery is best effort in this experimental repo. Prosper records attempted outbound actions and bridge results, but a production channel should add a transactional outbox and channel idempotency keys so retries and process restarts do not duplicate or lose sends.

## Local Media

Uploaded property media is stored under `runtime/media` by default. The database stores the file path and metadata, while the authenticated backend serves previews and the WhatsApp bridge reads the same local file. Deployments must persist and back up the runtime directory.

## Readiness And Doctor

Run `.venv/bin/python -m app.cli doctor` before startup to check local readiness without changing the database, configuration, media files, migrations, or services. The command reports `PASS`, `WARN`, and `FAIL` results and exits nonzero for required failures. Normal mode treats unavailable backend, dashboard, and bridge endpoints as warnings while still validating any endpoint that responds.

Run `.venv/bin/python -m app.cli doctor --strict-runtime` after starting the local backend, dashboard, and bridge when the operator wants strict validation of the running stack. In strict runtime mode, backend, dashboard, and bridge reachability are required, and missing Rental Listing / enabled Playbook coverage is a failure.

The doctor never prints secret values. It reports whether DeepSeek and bridge-token settings are present or unsafe without echoing the configured values. Prosper currently does not include schema migrations; the doctor says this plainly and checks the current SQLite table shape directly.

The local CLI can create a verified backup of the active SQLite database and managed property media with `.venv/bin/python -m app.cli backup`. The archive is self-describing through a manifest and can include tenant messages, Conversation state, Stage Run context, model inputs and outputs, action records, media metadata, and managed property media. It intentionally excludes environment files, bridge authentication state, logs, caches, build output, and previous backups. Restore is available with `.venv/bin/python -m app.cli restore <archive> --confirm-restore` after stopping local services; the command validates the archive first, refuses active services, preserves a rollback snapshot, and warns that WhatsApp re-pairing may be required. Without `--confirm-restore`, restore requires typing `RESTORE` interactively.

## Privacy and Retention

The default `.env.example` keeps SQLite data under `runtime/prosper.sqlite3`. Local audit records can contain tenant message text, listing details, Stage Run context, model input snapshots, model outputs, errors, and outbound action records. DeepSeek-backed runs send prompt input, including tenant messages and Stage Run context, to the configured DeepSeek-compatible endpoint.

SQLite remains Prosper's local application and middleman store for this release. Prosper currently does not include schema migrations. Existing local databases are still resettable; stop the services and use `.venv/bin/python -m app.cli cleanup-data --database --media --confirm-cleanup` for explicit data cleanup, or remove the relevant `runtime/*.sqlite3` files and rerun `.venv/bin/python -m app.cli init-db` to rebuild seeded local data. Cleanup commands require `--confirm-cleanup` or typing `CLEANUP` interactively. In-place migrations, scheduled backups, and automatic retention deletion are not implemented.

## Dashboard Authentication

The dashboard uses a signed, expiring `HttpOnly` cookie for its single-user session. Password verification is constant-time, the cookie is `Secure` when enabled for HTTPS deployment, and bridge callbacks remain on their separate token-authenticated path. The bridge process also requires that token for non-health operations and refuses non-loopback bindings when no token is configured.

## Known Boundary

The local pilot is SQLite-first and uses synchronous request handling around the pipeline. A high-volume deployment would need durable background jobs, explicit lease/lock ownership, durable outbound processing, managed storage cleanup, and stronger operational tracing before scaling message throughput.
