# Evaluation Strategy

Prosper separates deterministic correctness checks from model-quality checks.

## Deterministic Tests

- backend tests cover workspace scoping, state transitions, deduplication, Playbooks, actions, retries, and storage boundaries;
- frontend tests cover cookie-auth API calls, inbox filtering, and view state;
- bridge tests cover normalization, forwarding headers, retry behavior, sends, and pairing state;
- prompt contract tests check that required instructions and output fields remain present.

## Scenario Checks

`frontend/src/fakeChatScenarios.ts` and `scripts/fake_chat_smoke.py` provide repeatable enquiry scenarios for available, ambiguous, unavailable, incomplete, qualified, and non-enquiry messages.

## Live Model Evaluations

The JSON files under `evals/` are intended for provider-backed runs. They test triage and property matching against expected classifications. These evaluations are deliberately separate from `scripts/test.sh` because model availability and output behavior are external dependencies.

## What Is Not Claimed

The evaluation set is a small reference suite, not a statistically representative benchmark. It is useful for regression detection and interview discussion, but it should not be presented as proof of general model accuracy.
