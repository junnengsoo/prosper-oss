# Prosper Reference

Prosper is an open-source reference implementation for an AI system that handles inbound rental enquiries.

It demonstrates how to combine a language model with explicit application state, business context, deterministic actions, and human review. The project began as a deployed assistant for a real property workflow; this repository contains the sanitized, reproducible version of the engineering patterns behind it.

## What It Demonstrates

- triage of inbound messages
- retrieval of configured property and Playbook context
- rental listing matching against configured listings
- schema-validated model outputs
- deterministic outbound action planning
- idempotent message handling
- retries and failure-safe manual review
- human pause, takeover, and handoff
- stage-run audit records
- a local simulator for repeatable testing
- local property-media uploads stored under `runtime/media`
- an optional WhatsApp bridge using Baileys
- a small single-user dashboard auth flow using signed HTTP-only cookies

The simulator is the default demo path. It does not require a WhatsApp account or a model API key for the application to start. Model-backed stages require a configured provider; without one, the backend records a safe review fallback instead of sending a model-generated reply.

## Demo Flow

1. Start the backend and dashboard.
2. Open the Simulator tab.
3. Send the seeded enquiry for `Maple Grove Residence`.
4. Inspect the matched property, stage output, planned action, and recorded outbound message.
5. Send a purchase enquiry and show that it is not treated as an actionable rental enquiry.
6. Pause automation and show that subsequent actions are routed to human review.

## Architecture

```text
Inbound message
      |
      v
Normalize and deduplicate
      |
      v
Safety gates and conversation state
      |
      v
Triage -> rental listing matching -> handoff or completion
      |
      v
Schema validation and stage audit record
      |
      v
Deterministic Playbook action planner
      |
      +--> simulator transcript
      +--> WhatsApp bridge
      +--> manual review queue
```

The FastAPI backend owns business logic, persistence, prompts, state transitions, and outbound action planning. The React dashboard provides the operator interface and simulator. The TypeScript bridge owns WhatsApp connectivity and forwards normalized messages to the backend.

The dashboard uses one configured application password rather than requiring a user database or account-selection flow. Successful login creates a signed, expiring `HttpOnly` session cookie. The WhatsApp bridge remains separately authenticated with its bridge token; browser sessions are not used for inbound bridge events.

## Reliability Decisions

Prosper treats the model as a component inside a controlled workflow rather than as the workflow itself.

- Database uniqueness constraints prevent duplicate inbound messages from creating duplicate work.
- Contact and conversation state gates processing when automation is paused or a human has taken over.
- Model outputs are parsed and validated before they can influence an action.
- Every stage run stores its input snapshot, output, status, model, and error information.
- Provider failures, malformed JSON, unavailable listings, and unsafe actions fall back to review instead of sending blindly.
- Playbooks render outbound text deterministically from validated stage results.
- A send lock provides an explicit safety switch for live operation.

## Evaluation

The repository includes:

- backend unit and integration tests
- frontend state and authentication tests
- bridge normalization, forwarding, retry, and pairing tests
- prompt contract checks
- sanitized triage and rental-listing-matching cases
- a fake-chat smoke helper for testing the running application

Live model evaluations are separate from the deterministic test suite because they depend on provider availability and model behavior.

## Local Setup

Requirements: Python 3.11, `uv`, Node.js 22, and npm.

```bash
cp .env.example .env
uv sync --locked --extra dev --python 3.11
cd frontend && npm ci
cd ../bridge && npm ci
cd ..
```

Initialize the local database and demo configuration:

```bash
.venv/bin/python -m app.cli
```

Run the backend:

```bash
.venv/bin/uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

In a second terminal, run the dashboard:

```bash
cd frontend
npm run dev
```

For a protected deployment, set these backend variables:

```env
AUTH_REQUIRED=true
ACCESS_PASSWORD=choose-a-private-password
SESSION_SECRET=generate-a-long-random-secret
SESSION_TTL_SECONDS=86400
AUTH_COOKIE_SECURE=true
# Required when the WhatsApp bridge is enabled.
WHATSAPP_PA_BRIDGE_TOKEN=another-private-token
```

The frontend uses same-origin `/api/...` requests, so the reverse proxy should serve the dashboard and backend API from the same HTTPS origin.

Open `http://127.0.0.1:5173` and use the Simulator tab. The bridge can be started separately when testing WhatsApp connectivity:

```bash
cd bridge
npm run dev
```

The WhatsApp bridge is optional and experimental. It depends on Baileys, whose optional link-preview path has an unresolved advisory (`GHSA-4gp8-rjrq-ch6q`) through `link-preview-js`. Prosper does not declare or call `link-preview-js` directly; the remaining Baileys advisory is accepted as non-blocking for this reference bridge and should be re-reviewed before production WhatsApp use.

## Verification

Run the repository checks from a clean checkout:

```bash
scripts/test.sh
```

With the backend running, exercise the seeded fake-chat scenarios:

```bash
scripts/fake_chat_smoke.sh --reset
```

Live model evaluations require the configured provider:

```bash
scripts/eval.sh
```

## Limitations and Future Work

This is a reference implementation, not a general-purpose agent platform. The current version uses SQLite for local development, a concrete rental-enquiry domain, code-owned prompts, a single-user dashboard password, and a single bridge process. The auth flow is intentionally appropriate for a private reference deployment, not a general-purpose identity system. It does not claim queue processing or multi-provider parity.

Natural next steps are a provider-neutral model interface, stronger replayable evaluations, background job processing where message volume requires it, richer tracing, and a more general channel adapter contract. Those should be added only when they improve a demonstrated reliability or reuse problem.

## License

This repository is prepared as a public reference project. Add the final license before publishing.
