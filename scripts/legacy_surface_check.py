#!/usr/bin/env python3
"""Fail if old global-template or draft-approval runtime surfaces return."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
SEARCH_PATHS = [
    ROOT_DIR / "backend" / "app",
    ROOT_DIR / "backend" / "tests",
    ROOT_DIR / "frontend" / "src",
    ROOT_DIR / "frontend" / "tests",
]

PATTERNS = [
    r"class Template\b",
    r"\bTemplateOut\b",
    r"\bTemplateUpdate\b",
    r"/api/templates",
    r"api\.templates",
    r"templatePreview",
    r'__tablename__\s*=\s*"templates"',
    r"created_draft",
    r"\bDraftAttachment\b",
    r"/api/drafts",
    r"approve_and_send",
    r"\bauto_send\b",
    r"render_outbound_actions_to_drafts",
    r"apply_outbound_action_plan",
    r"maybe_auto_send",
    r'__tablename__\s*=\s*"drafts"',
    r"MESSAGE_BREAK_MARKER",
    r"MEDIA_MARKER",
    r"split_outbound_text",
    r"split_outbound_parts",
]

COMPILED = [(pattern, re.compile(pattern)) for pattern in PATTERNS]


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in SEARCH_PATHS:
        if path.is_file():
            files.append(path)
            continue
        for child in path.rglob("*"):
            if child.is_file() and child.suffix in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                files.append(child)
    return files


def main() -> int:
    findings: list[str] = []
    for path in iter_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for pattern, regex in COMPILED:
                if regex.search(line):
                    relative = path.relative_to(ROOT_DIR)
                    findings.append(f"{relative}:{line_number}: matched {pattern!r}: {line.strip()}")

    if findings:
        print("Legacy template/draft runtime surface found:", file=sys.stderr)
        for finding in findings:
            print(finding, file=sys.stderr)
        return 1

    print("Legacy template/draft runtime surface check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
