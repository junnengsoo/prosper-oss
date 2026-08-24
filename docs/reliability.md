# Reliability Notes

Prosper uses explicit controls around model-backed work.

## Duplicate and Replay Safety

Inbound messages are uniquely identified by chat and message ID. Replayed bridge events therefore do not create duplicate stored messages or duplicate conversations.

## State Gates

Processing is blocked when the global AI pause is enabled, a contact is paused or ignored, a conversation is closed, or a prior human reply indicates manual takeover. These checks happen before model work and before outbound execution.

## Model Failure

Provider errors, invalid JSON, and invalid nested output shapes are recorded as failed stage runs. The system returns a review-safe result and does not send a model-generated reply.

## Qualification Loop

Qualification is a bounded conversational loop. Each model turn is validated into a typed result containing the tenant-facing message, extracted facts, missing fields, and qualification status. The loop records its turn count and hands off after five continuable turns instead of making another model call.

## Outbound Safety

Playbooks render outbound content deterministically from validated stage results. The send lock, action safety checks, bridge retry behavior, and outbound action records provide separate controls around the final side effect.

The optional Baileys bridge is experimental, and outbound WhatsApp delivery is best effort in this reference product. A production channel should add a transactional outbox and channel idempotency keys so retries and process restarts do not duplicate or lose sends.

## Local Media

Uploaded property media is stored under `runtime/media` by default. The database stores the file path and metadata, while the authenticated backend serves previews and the WhatsApp bridge reads the same local file. Deployments must persist and back up the runtime directory.

## Dashboard Authentication

The dashboard uses a signed, expiring `HttpOnly` cookie for its single-user session. Password verification is constant-time, the cookie is `Secure` when enabled for HTTPS deployment, and bridge callbacks remain on their separate token-authenticated path. The bridge process also requires that token for non-health operations and refuses non-loopback bindings when no token is configured.

## Known Boundary

The local reference implementation is SQLite-first and uses synchronous request handling around the pipeline. A high-volume deployment would need durable background jobs, explicit lease/lock ownership, and stronger operational tracing before scaling message throughput.
