# ADR 0001: Public Reference Boundaries

## Status

Accepted

## Context

Prosper is being prepared for public technical review. The repository needs to show the implemented rental-enquiry workflow clearly without implying broader licensing, deployment, channel, or product commitments.

The current implemented surface is an inbound rental-enquiry pipeline with a local dashboard, local SQLite storage, DeepSeek-backed model stages, deterministic Playbook replies, and an optional Baileys WhatsApp Bridge.

## Decision

Prosper will be documented as a public rental-enquiry reference implementation.

The supported happy path uses DeepSeek through `DEEPSEEK_API_KEY`. The simulator and deterministic tests may run against fake DeepSeek-compatible responses, but live model-backed review requires a configured DeepSeek key.

SQLite remains reset-only local reference storage. Existing local databases are disposable, and migrations are outside the release scope.

The Simulator Conversation is the supported evaluation path because it exercises the same backend pipeline as live inbound bridge messages without requiring a WhatsApp account.

The WhatsApp Bridge remains optional, authenticated, experimental, and best effort. It is a Baileys adapter, not an official WhatsApp integration. Outbound delivery records attempts and results, but this release does not include a transactional outbox or channel idempotency guarantees.

Qualification forms, screening, sale enquiry behavior, production operations, multi-user identity, provider-neutral routing, and durable queue processing are deferred.

Final history squash or curation is a separate publication step and is not performed as part of this implementation ticket.

## Consequences

Reviewers get a reproducible local path through setup, configuration, simulator execution, audit inspection, and optional bridge pairing.

Documentation must be explicit about local privacy and retention boundaries because prompt inputs, messages, stage outputs, media references, and outbound action records can be stored in local SQLite and runtime directories.

Future work can replace individual boundaries, but those changes should be justified by concrete reliability, reuse, or operational needs rather than implied by this reference release.
