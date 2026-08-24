# Reliability Notes

Prosper uses explicit controls around model-backed work.

## Duplicate and Replay Safety

Inbound messages are uniquely identified by chat and message ID. Replayed bridge events therefore do not create duplicate stored messages or duplicate conversations.

## State Gates

Processing is blocked when the global AI pause is enabled, a contact is paused or ignored, a conversation is closed, or a prior human reply indicates manual takeover. These checks happen before model work and before outbound execution.

## Model Failure

Provider errors, invalid JSON, and invalid output shapes are recorded as failed stage runs. The system returns a review-safe result and does not send a model-generated reply.

## Outbound Safety

Playbooks render outbound content deterministically from validated stage results. The send lock, action safety checks, bridge retry behavior, and outbound action records provide separate controls around the final side effect.

## Local Media

Uploaded property media is stored under `runtime/media` by default. The database stores the file path and metadata, while the authenticated backend serves previews and the WhatsApp bridge reads the same local file. Deployments must persist and back up the runtime directory.

## Dashboard Authentication

The dashboard uses a signed, expiring `HttpOnly` cookie for its single-user session. Password verification is constant-time, the cookie is `Secure` when enabled for HTTPS deployment, and bridge callbacks remain on their separate token-authenticated path.

## Known Boundary

The local reference implementation is SQLite-first and uses synchronous request handling around the pipeline. A high-volume deployment would need durable background jobs, explicit lease/lock ownership, and stronger operational tracing before scaling message throughput.
