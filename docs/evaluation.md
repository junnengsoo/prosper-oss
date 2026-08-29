# Evaluation Strategy

Prosper separates deterministic correctness checks from model-quality checks. The implemented release boundary is summarized in [ADR 0001](adr/0001-public-reference-boundaries.md).

## Deterministic Tests

- backend tests cover state transitions, deduplication, Playbooks, actions, retries, and storage boundaries;
- frontend tests cover cookie-auth API calls, inbox filtering, and view state;
- bridge tests cover normalization, forwarding headers, retry behavior, sends, and pairing state;
- prompt contract tests check that required instructions and output fields remain present.

`.github/workflows/verify.yml` is the clean-checkout CI suite. `scripts/test.sh` is the aggregate local verification command. Together they cover locked dependency installation, backend/frontend/bridge checks, and the full-stack browser acceptance suite against deterministic DeepSeek-compatible responses.

## Scenario Checks

`scripts/fake_chat_smoke.py` provides repeatable enquiry scenarios for available, ambiguous, unavailable, purchase, and non-enquiry messages.

The browser acceptance suite covers the public manual flow on desktop and mobile: create Rental Listings, configure Playbook / Auto Replies, submit Simulator Conversation messages, inspect audit stage runs, and verify manual-review behavior for unavailable matches.

## Live Model Evaluations

The JSON files under `evals/` are intended for DeepSeek-backed runs. They test rental triage and rental listing matching against expected classifications. These evaluations are deliberately separate from `scripts/test.sh` because model availability, credentials, and output behavior are external dependencies.

## What Is Not Claimed

The evaluation set is a small reference suite, not a statistically representative benchmark. It is useful for regression detection and technical review, but it should not be presented as proof of general model accuracy.
