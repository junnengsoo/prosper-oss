# Reliability Notes

Prosper uses explicit controls around model-backed rental-enquiry work. See the [Domain Glossary](domain-glossary.md) for shared terms and [ADR 0001](adr/0001-public-reference-boundaries.md) for the public release boundary.

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

## Privacy and Retention

The default `.env.example` keeps SQLite data under `runtime/prosper.sqlite3`. Local audit records can contain inbound message text, listing details, model input snapshots, model outputs, errors, and outbound action records. DeepSeek-backed runs send prompt input to the configured DeepSeek-compatible endpoint.

SQLite is reset-only local storage for this release. Existing local databases are disposable; stop the services, remove the relevant `runtime/*.sqlite3` files, and rerun `.venv/bin/python -m app.cli init-db` to rebuild seeded local data. In-place migrations, backup/restore, and managed retention policies are deferred.

## Dashboard Authentication

The dashboard uses a signed, expiring `HttpOnly` cookie for its single-user session. Password verification is constant-time, the cookie is `Secure` when enabled for HTTPS deployment, and bridge callbacks remain on their separate token-authenticated path. The bridge process also requires that token for non-health operations and refuses non-loopback bindings when no token is configured.

## Known Boundary

The local reference implementation is SQLite-first and uses synchronous request handling around the pipeline. A high-volume deployment would need durable background jobs, explicit lease/lock ownership, durable outbound processing, managed storage retention, and stronger operational tracing before scaling message throughput.
