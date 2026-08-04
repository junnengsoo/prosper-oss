import re

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
