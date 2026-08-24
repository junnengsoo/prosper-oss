#!/usr/bin/env python3
"""Run sanitized fake-chat scenarios against the local backend."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    label: str
    display_name: str
    expected_run_status: str
    text: str


SCENARIOS = [
    Scenario(
        "available_listing",
        "Available unit",
        "Demo Tenant",
        "created_conversation",
        "Hi, is Maple Grove Residence available? We are 4 family members. Budget is 3400. Immediate move-in and one-year lease.",
    ),
    Scenario(
        "ambiguous_property",
        "Ambiguous property",
        "Unspecified Tenant",
        "created_conversation",
        "Hi, do you have a suitable two-bedroom rental near the city? Budget is around 3000.",
    ),
    Scenario(
        "unavailable_property",
        "Unavailable listing",
        "Pending Tenant",
        "created_conversation",
        "Hi, is Riverside Lofts still available? We need two bedrooms and can move in immediately.",
    ),
    Scenario(
        "not_enquiry",
        "Non-enquiry",
        "Existing Contact",
        "skipped",
        "Thanks, noted.",
    ),
]


def request_json(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else None


def conversation_stage(base_url: str, conversation_id: int | None) -> str:
    if conversation_id is None:
        return "-"
    conversations = request_json(base_url, "GET", "/api/conversations?include_closed=true")
    for conversation in conversations:
        if conversation.get("id") == conversation_id:
            return str(conversation.get("current_stage") or "-")
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    health = request_json(args.base_url, "GET", "/health")
    print(f"Backend health: {health}")
    runtime_status = request_json(args.base_url, "GET", "/api/runtime/status")
    llm_configured = bool(runtime_status.get("llm", {}).get("configured"))
    if not llm_configured:
        print("Model provider is not configured; model-backed scenarios should fall back to review safely.")
    if args.reset:
        print(f"Reset fake chat data: {request_json(args.base_url, 'POST', '/api/fake-chat/reset', {})}")

    mismatches = 0
    for index, scenario in enumerate(SCENARIOS):
        timestamp_ms = int(time.time() * 1000) + index
        payload = {
            "chat_jid": f"fake-demo-{scenario.scenario_id}@s.whatsapp.net",
            "display_name": scenario.display_name,
            "message_id": f"fake-demo-{scenario.scenario_id}-{timestamp_ms}",
            "timestamp_ms": timestamp_ms,
            "text": scenario.text,
        }
        result = request_json(args.base_url, "POST", "/api/fake-chat/inbound-and-run", payload)
        conversation_id = result.get("conversation_id")
        actual_status = "created_conversation" if conversation_id is not None else "skipped"
        stage = conversation_stage(args.base_url, conversation_id)
        accepted_statuses = {scenario.expected_run_status}
        if scenario.scenario_id == "not_enquiry" and not llm_configured:
            accepted_statuses.add("created_conversation")
        ok = actual_status in accepted_statuses

        if not ok:
            mismatches += 1

        marker = "ok" if ok else "warn"
        print(f"{marker:4} {scenario.scenario_id}: run={actual_status} stage={stage}")

    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
