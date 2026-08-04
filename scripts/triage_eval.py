#!/usr/bin/env python3
"""Run triage prompt evals against rental and sale WhatsApp enquiry fixtures.

This calls the configured LLM provider by default and exits non-zero if the
provider is unreachable, misconfigured, or any case fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.llm import LlmProviderError, generate_json  # noqa: E402
from app.pipeline import build_triage_messages  # noqa: E402


DEFAULT_CASES_PATH = ROOT_DIR / "evals" / "triage_cases.json"


def load_cases(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, list):
        raise ValueError("triage eval file must contain a JSON array")
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(f"case {index} must be an object")
        case_id = str(item.get("id") or "").strip()
        if not case_id:
            raise ValueError(f"case {index} missing id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        thread = str(item.get("thread") or "").strip()
        if not thread:
            raise ValueError(f"{case_id}: thread must not be blank")
        expected = item.get("expected_is_initial_property_enquiry")
        if not isinstance(expected, bool):
            raise ValueError(f"{case_id}: expected_is_initial_property_enquiry must be boolean")
        cases.append(item)
    return cases


def normalize_is_initial(result: dict[str, Any]) -> bool:
    """Read the new triage key, with old rental-only key as compatibility fallback."""
    return result.get("is_initial_property_enquiry") is True or result.get("is_initial_rental_enquiry") is True


def validate_prompt_contract(cases: list[dict[str, Any]]) -> None:
    sample_messages = build_triage_messages(cases[0]["thread"])
    system = sample_messages[0]["content"]
    required_phrases = [
        "initial property enquiry",
        "sale price",
        "purchase interest",
        "is_initial_property_enquiry",
    ]
    missing = [phrase for phrase in required_phrases if phrase not in system]
    if missing:
        raise ValueError(f"triage prompt is missing expected sale/property wording: {', '.join(missing)}")


async def run_live_eval(cases: list[dict[str, Any]]) -> int:
    failures = 0
    for case in cases:
        messages = build_triage_messages(case["thread"])
        try:
            result = await generate_json(
                messages,
                {
                    "stage": "triage_eval",
                    "metadata": {"case_id": case["id"]},
                },
            )
        except LlmProviderError as error:
            print(f"ERROR {case['id']}: {error}")
            failures += 1
            continue
        actual = normalize_is_initial(result)
        expected = bool(case["expected_is_initial_property_enquiry"])
        status = "PASS" if actual == expected else "FAIL"
        if status == "FAIL":
            failures += 1
        reason = str(result.get("reason") or "").replace("\n", " ")
        print(f"{status} {case['id']} expected={expected} actual={actual} reason={reason[:160]}")
    return failures


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    validate_prompt_contract(cases)
    failures = await run_live_eval(cases)
    print(f"triage live eval complete: {len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
