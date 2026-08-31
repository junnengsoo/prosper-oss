# Tradeoff Inventory

This inventory records the final public-review wording decisions behind Prosper's cleaned surface. It is intentionally short so the reviewer briefing can explain the product boundary without turning into a roadmap.

## Kept

- Local-first review path: The Simulator Conversation, local SQLite application and middleman store, seeded Rental Listings, verified backups, and local media directory stay because they make the workflow reproducible without external channel setup.
- DeepSeek-backed model stages: DeepSeek remains the sole named live provider because the prompts, schemas, and evals are written around that contract. Missing credentials fall back to Manual Review so setup still succeeds.
- Stage Runs as the audit story: Local stage records stay because they expose inputs, outputs, statuses, and errors at the workflow level without requiring external tracing.
- Deterministic Playbook / Auto Replies: Final tenant-facing replies are rendered from configured Playbook blocks after validated model output, keeping the last action predictable.
- Authenticated WhatsApp Bridge: The bridge stays because WhatsApp is the visible channel transcript. The Baileys implementation keeps local pairing possible while preserving a clear backend channel contract.
- Single-user dashboard auth: A signed cookie and one configured password stay because they protect the local dashboard without adding a user-management system outside the current scope.

## Removed

- Optional observability infrastructure: External tracing was removed because Stage Runs now carry the local audit responsibility for this reference.
- Managed database hints: Managed-database dependencies and wording were removed because Prosper uses local SQLite in this release.
- Inert dashboard affordances: Nonfunctional inbox filters and contact action buttons were removed because visible controls should describe supported behavior.
- Legacy frontend compatibility: Old route aliases, browser-state compatibility, and obsolete Playbook normalization were removed because local data is disposable and the public surface has one current shape.
- Stale runtime contracts: Export and action branches that no supported workflow can produce were trimmed so reviewers can follow the active path.

## Deferred

- Managed operations: durable queues, leases, channel idempotency keys, schema migrations, scheduled backups, automatic retention deletion, and hosted media storage are deferred until there is an operational target.
- Broader product scope: sale enquiries, screening, qualification forms, transaction automation, and post-match workflows are deferred to keep Prosper focused on inbound rental enquiries.
- Provider-neutral model routing: multi-provider parity is deferred because the current quality and contract checks are DeepSeek-specific.
- Multi-user identity and roles: user databases, roles, and team permissions are deferred because they would add security surface without helping the current local review path.
- Official channel integration: an official WhatsApp Business Platform integration is deferred; the current bridge is a Baileys adapter for local WhatsApp connectivity.

## Why

The cleanup favors a smaller, auditable reference over a larger product-shaped shell. Prosper keeps the pieces needed to inspect the implemented rental-enquiry loop, removes surfaces that imply unsupported commitments, and defers operational hardening until the repository has a concrete deployment or reuse target.
