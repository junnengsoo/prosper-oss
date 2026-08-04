# Reliability Notes

Prosper uses explicit controls around model-backed work.

## Duplicate and Replay Safety

Inbound messages are uniquely identified within the workspace, account, chat, and message ID. Replayed bridge events therefore do not create duplicate stored messages or duplicate conversations.

## State Gates

Processing is blocked when the global AI pause is enabled, a contact is paused or ignored, a conversation is closed, or a prior human reply indicates manual takeover. These checks happen before model work and before outbound execution.

## Model Failure

Provider errors, invalid JSON, and invalid nested output shapes are recorded as failed stage runs. The system returns a review-safe result and does not send a model-generated reply.

## Qualification Loop

Qualification is a bounded conversational loop. Each model turn is validated into a typed result containing the tenant-facing message, extracted facts, missing fields, and qualification status. The loop records its turn count and hands off after five continuable turns instead of making another model call.

## Outbound Safety

Playbooks render outbound content deterministically from validated stage results. The send lock, action safety checks, bridge retry behavior, and outbound action records provide separate controls around the final side effect.

## Dashboard Authentication

The dashboard uses a signed, expiring `HttpOnly` cookie for its single-user session. Password verification is constant-time, the cookie is `Secure` when enabled for HTTPS deployment, and bridge callbacks remain on their separate token-authenticated path.

## Known Boundary

The local reference implementation is SQLite-first and uses synchronous request handling around the pipeline. A high-volume deployment would need durable background jobs, explicit lease/lock ownership, and stronger operational tracing before scaling message throughput.
