#!/usr/bin/env python3
"""Run rental listing matching evals against rental property fixtures.

This calls the configured LLM provider and exits non-zero if the provider is
unreachable, misconfigured, or any case fails.
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
from app.prompts import get_prompt  # noqa: E402


DEFAULT_CASES_PATH = ROOT_DIR / "evals" / "rental_listing_matching_cases.json"


def load_eval_file(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with path.open() as handle:
        parsed = json.load(handle)
    if not isinstance(parsed, dict):
        raise ValueError("rental listing matching eval file must contain a JSON object")
    property_list = parsed.get("property_list")
    cases = parsed.get("cases")
    if not isinstance(property_list, list) or not property_list:
        raise ValueError("property_list must be a non-empty array")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty array")

    seen_property_ids: set[str] = set()
    for index, property_ in enumerate(property_list):
        if not isinstance(property_, dict):
            raise ValueError(f"property_list[{index}] must be an object")
        property_id = str(property_.get("property_id") or "").strip()
        if not property_id:
            raise ValueError(f"property_list[{index}] missing property_id")
        if property_id in seen_property_ids:
            raise ValueError(f"duplicate property_id: {property_id}")
        seen_property_ids.add(property_id)
        for field in ("property_name", "full_address", "property_url", "propertyguru_listing_id"):
            if field not in property_:
                raise ValueError(f"{property_id}: missing {field}")

    seen_case_ids: set[str] = set()
    valid_statuses = {"matched", "no_property_mentioned", "unmatched_property", "ambiguous_multiple_matches"}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"cases[{index}] must be an object")
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            raise ValueError(f"cases[{index}] missing id")
        if case_id in seen_case_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_case_ids.add(case_id)
        if not str(case.get("message") or "").strip():
            raise ValueError(f"{case_id}: message must not be blank")
        if case.get("expected_match_status") not in valid_statuses:
            raise ValueError(f"{case_id}: invalid expected_match_status")
    return property_list, cases


def property_jsonl(properties: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(property_, ensure_ascii=False) for property_ in properties)


def build_rental_listing_matching_messages(message: str, properties: list[dict[str, Any]]) -> list[dict[str, str]]:
    prompt = get_prompt("rental_listing_matching")
    return [
        {"role": "system", "content": prompt.render(property_list=property_jsonl(properties))},
        {"role": "user", "content": message},
    ]


def matched_property_ids(result: dict[str, Any]) -> list[str]:
    matched = result.get("matched_properties")
    if not isinstance(matched, list):
        return []
    return [
        str(item.get("property_id") or "")
        for item in matched
        if isinstance(item, dict) and str(item.get("property_id") or "")
    ]


def case_passed(case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str]:
    expected_status = case["expected_match_status"]
    actual_status = result.get("match_status")
    if actual_status != expected_status:
        return False, f"match_status expected={expected_status} actual={actual_status}"

    expected_property_id = str(case.get("expected_property_id") or "")
    actual_ids = matched_property_ids(result)
    if expected_status == "matched":
        if actual_ids != [expected_property_id]:
            return False, f"matched property expected={[expected_property_id]} actual={actual_ids}"
    elif expected_property_id and expected_property_id not in actual_ids:
        return False, f"expected property id {expected_property_id} not found in {actual_ids}"
    return True, "ok"


async def run_eval(properties: list[dict[str, Any]], cases: list[dict[str, Any]]) -> int:
    failures = 0
    for case in cases:
        messages = build_rental_listing_matching_messages(case["message"], properties)
        try:
            result = await generate_json(
                messages,
                {
                    "stage": "rental_listing_matching_eval",
                    "metadata": {"case_id": case["id"]},
                },
            )
        except LlmProviderError as error:
            print(f"ERROR {case['id']}: {error}")
            failures += 1
            continue
        passed, detail = case_passed(case, result)
        status = "PASS" if passed else "FAIL"
        if not passed:
            failures += 1
        reason = str(result.get("reason") or "").replace("\n", " ")
        print(
            f"{status} {case['id']} status={result.get('match_status')} "
            f"matched={matched_property_ids(result)} "
            f"detail={detail} reason={reason[:160]}"
        )
    return failures


def validate_prompt_contract(properties: list[dict[str, Any]]) -> None:
    system_prompt = build_rental_listing_matching_messages("Hi Maple Grove Residence", properties)[0]["content"]
    required_phrases = [
        "rental enquiry",
        "propertyguru_listing_id",
        "Do not extract or judge tenant profile",
    ]
    missing = [phrase for phrase in required_phrases if phrase not in system_prompt]
    if missing:
        raise ValueError(f"rental listing matching prompt is missing expected rental wording: {', '.join(missing)}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    args = parser.parse_args()

    properties, cases = load_eval_file(args.cases)
    validate_prompt_contract(properties)
    failures = await run_eval(properties, cases)
    print(f"rental listing matching live eval complete: {len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
