from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
README = (ROOT_DIR / "README.md").read_text()
PROJECT_BRIEFING = (ROOT_DIR / "docs" / "project-presentation.md").read_text()
TRADEOFF_INVENTORY = (ROOT_DIR / "docs" / "tradeoff-inventory.md").read_text()
DOMAIN_GLOSSARY = (ROOT_DIR / "docs" / "domain-glossary.md").read_text()
PUBLIC_BOUNDARY_ADR = (ROOT_DIR / "docs" / "adr" / "0001-public-reference-boundaries.md").read_text()
PUBLIC_DOCS = [
    ROOT_DIR / "README.md",
    *sorted((ROOT_DIR / "docs").glob("**/*.md")),
]


def assert_contains_all(text: str, phrases: list[str]):
    for phrase in phrases:
        assert phrase in text


def test_readme_uses_experimental_single_user_positioning_without_release_overclaiming():
    lowered = README.lower()

    assert "experimental rental-enquiry app" in lowered
    assert "single-user workflow" in lowered
    assert "open-source" not in lowered
    assert "open source" not in lowered
    assert "production-ready" not in lowered
    assert "production ready" not in lowered
    assert "reference implementation" not in lowered
    assert "reviewing company" not in lowered
    assert "founding engineer" not in lowered


def test_readme_documents_supported_setup_and_deepseek_happy_path():
    assert "Python 3.11" in README
    assert "Node.js 22" in README
    assert "DEEPSEEK_API_KEY" in README
    assert "scripts/dev.sh" in README
    assert "http://127.0.0.1:5173" in README


def test_public_docs_prefer_prosper_bridge_configuration():
    assert "PROSPER_BRIDGE_TOKEN" in README
    assert "PROSPER_BRIDGE_BASE_URL" in README
    assert "WHATSAPP_PA_*" in README

    env_example = (ROOT_DIR / ".env.example").read_text()
    assert "PROSPER_BRIDGE_TOKEN=" in env_example
    assert "PROSPER_BRIDGE_BASE_URL=" in env_example
    assert "WHATSAPP_PA_BRIDGE_TOKEN=" not in env_example


def test_public_metadata_identifies_prosper():
    assert 'name = "prosper"' in (ROOT_DIR / "pyproject.toml").read_text()
    assert '"name": "prosper-dashboard"' in (ROOT_DIR / "frontend/package.json").read_text()
    assert '"name": "prosper-bridge"' in (ROOT_DIR / "bridge/package.json").read_text()
    assert "<title>Prosper Dashboard</title>" in (ROOT_DIR / "frontend/index.html").read_text()


def test_public_docs_avoid_old_product_and_review_positioning_language():
    banned_phrases = [
        "whatsapp pa mvp",
        "whatsapp pa dashboard",
        "whatsapp pa backend",
        "whatsapp-pa",
        " mvp",
        "interview",
        "founding-engineer",
        "founding engineer",
        "demo-positioning",
        "demo positioning",
        "review demos",
    ]

    for path in PUBLIC_DOCS:
        text = path.read_text().lower()
        for phrase in banned_phrases:
            assert phrase not in text, f"{path.relative_to(ROOT_DIR)} contains {phrase!r}"


def test_reviewer_briefing_covers_review_surface_without_brittle_positioning():
    required_phrases = [
        "architecture",
        "workflow",
        "safety decisions",
        "tradeoffs",
        "setup",
        "what to inspect",
        "Pipeline",
        "Stage Runs",
        "Playbook / Auto Replies",
        "Simulator Conversation",
        "reset-only SQLite",
        "Manual Review",
        "optional authenticated WhatsApp Bridge",
        "scripts/test.sh",
    ]

    assert_contains_all(PROJECT_BRIEFING.lower(), [phrase.lower() for phrase in required_phrases])

    assert "docs/tradeoff-inventory.md" not in PROJECT_BRIEFING
    assert "[Tradeoff Inventory](tradeoff-inventory.md)" in PROJECT_BRIEFING


def test_tradeoff_inventory_is_present_before_final_briefing_concepts():
    assert_contains_all(TRADEOFF_INVENTORY, ["## Kept", "## Removed", "## Deferred", "## Why"])

    assert_contains_all(
        TRADEOFF_INVENTORY,
        [
            "Stage Runs as the audit story",
            "Optional authenticated WhatsApp Bridge",
            "Managed operations",
            "Broader product scope",
        ],
    )


def test_readme_documents_manual_walkthrough_and_release_boundaries():
    required_phrases = [
        "Rental Listing",
        "Playbook / Auto Replies",
        "Simulator Conversation",
        "Prosper Audit",
        "authenticated WhatsApp Bridge",
        "reset-only SQLite",
        "best-effort outbound delivery",
        "inbound deduplication",
        "docs/project-presentation.md",
        "docs/tradeoff-inventory.md",
        "docs/domain-glossary.md",
        "single-user workflow",
        "experimental scope",
        "Manual Review",
    ]

    assert_contains_all(README, required_phrases)


def test_schema_boundary_docs_explain_local_sqlite_production_boundaries():
    assert_contains_all(
        PUBLIC_BOUNDARY_ADR,
        [
            "Prosper uses local SQLite for this experimental repo",
            "disposable local testing data",
            "not production storage",
            "production deployment would need a deliberate database redesign",
            "schema changes are handled by resetting the local database",
            "string listing identifiers",
            "`property_type` field stays",
            "intentional extension point",
            "one active Conversation per Contact",
            "Latest-message display state is derived from Messages",
            "`conversation_id` is nullable",
            "full input snapshots",
            "raw-type marker records source event provenance",
            "internal outbound action markers",
            "only keys explicitly allowed by the backend contract may be written",
            "auditability, provenance, local resettable storage",
        ],
    )


def test_domain_glossary_explains_clean_schema_terms():
    assert_contains_all(
        DOMAIN_GLOSSARY,
        [
            "string Rental Listing ID",
            "rental-focused in this release",
            "listing `property_type` field is retained as intentional extensibility",
            "`chat_jid` is the Contact identity",
            "`display_name` and `phone` are optional channel metadata",
            "phone metadata is not treated as tenant identity",
            "A Contact can have historical Conversations",
            "only one active Conversation per Contact",
            "New Conversations for the same Contact are created only after the prior active Conversation is closed",
            "`status` records lifecycle",
            "`current_stage` records where the active workflow is routed",
            "Lifecycle status and current pipeline stage are separate concepts",
            "`conversation_id` is nullable by design",
            "Pre-conversation triage",
            "source event marker retained with a Message",
            "supports provenance and auto-reply dedupe",
            "Runtime Config has an allowed-key boundary",
            "currently `pause_ai` and `send_lock`",
            "Unknown config keys are rejected",
        ],
    )
