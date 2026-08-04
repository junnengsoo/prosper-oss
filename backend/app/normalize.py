import re

DEFAULT_AUTO_GREETING_TEXT = "Thank you for contacting the property assistant. Please let us know how we can help you."


def normalize_comparable_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_configured_auto_greeting(message_text: str, configured_text: str) -> bool:
    if not message_text:
        return False
    normalized_message = normalize_comparable_text(message_text)
    configured_candidates = [configured_text, DEFAULT_AUTO_GREETING_TEXT]
    return any(
        normalize_comparable_text(candidate) == normalized_message
        for candidate in configured_candidates
        if candidate
    )


def extract_propertyguru_listing_id(value: str | None) -> str:
    if not value:
        return ""

    patterns = [
        r"propertyguru\.com\.sg/(?:listing/[^?\s#]+|l)/(?P<id>\d{6,})",
        r"propertyguru\.com\.sg/[^?\s#]*-(?P<id>\d{6,})(?:[/?#\s]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group("id")
    return ""
