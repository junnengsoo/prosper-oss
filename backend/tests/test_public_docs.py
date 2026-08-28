from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
README = (ROOT_DIR / "README.md").read_text()


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
