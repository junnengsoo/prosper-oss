from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
README = (ROOT_DIR / "README.md").read_text()
PUBLIC_DOCS = [
    ROOT_DIR / "README.md",
    *sorted((ROOT_DIR / "docs").glob("**/*.md")),
]


def test_readme_uses_public_reference_positioning_without_release_overclaiming():
    lowered = README.lower()

    assert "public rental-enquiry reference implementation" in lowered
    assert "open-source" not in lowered
    assert "open source" not in lowered
    assert "production-ready" not in lowered
    assert "production ready" not in lowered


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
        "docs/domain-glossary.md",
        "docs/adr/0001-public-reference-boundaries.md",
        "history squash",
    ]

    for phrase in required_phrases:
        assert phrase in README
