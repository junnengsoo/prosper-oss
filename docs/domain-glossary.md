# Domain Glossary

This glossary keeps public documentation, backend names, and dashboard labels aligned for the rental-enquiry reference implementation.

## Rental Enquiry

An inbound tenant message that appears to ask about renting a configured property. Sale enquiries, general spam, and post-match qualification workflows are out of scope for this release.

## Rental Listing

The operator-configured property record used for matching and replies. It includes a property name, listing URL, availability status, rent, address, bedroom and bathroom counts, availability date, tenant-facing caveats, and optional local media.

## Playbook / Auto Replies

The per-listing deterministic reply configuration. After DeepSeek returns a validated available-listing match, Prosper renders enabled Playbook blocks into outbound text and media actions. The model does not author free-form tenant replies in the final action step.

## Simulator Conversation

The supported local evaluation path in the dashboard. It accepts fake tenant messages, stores them as local conversations, runs the same backend pipeline used by bridge inbound messages, and displays the resulting transcript and audit records.

## WhatsApp Bridge

The optional TypeScript adapter around Baileys. It normalizes WhatsApp events for the backend and attempts outbound sends when asked. It is authenticated separately from browser dashboard sessions and is experimental.

## Stage Run

An audit record for one pipeline stage. Stage runs capture the stage name, input snapshot, output, model, status, error details, and timestamp so a reviewer can inspect why a conversation was matched, routed to manual review, or blocked.

## Outbound Action

A deterministic action plan produced after validated stage results. Actions can send Playbook text, send configured media, hand off to review, or do nothing. Bridge delivery is best effort and is not treated as a durable side effect.

## Manual Review

The safe route when the system should not send an automated response. Manual review is used for unavailable listings, ambiguous matches, provider failures, invalid model output, paused contacts, human takeover, and send-lock blocks.

## Send Lock

A local operator switch that prevents outbound action execution. It is useful for review walkthroughs where inbound processing and audit inspection should continue but tenant replies should not be sent.

## Reset-Only SQLite

The local storage posture for this release. SQLite files under `runtime/` are disposable reference data. Database migrations, in-place upgrades, backups, and managed retention policies are deliberately deferred.
