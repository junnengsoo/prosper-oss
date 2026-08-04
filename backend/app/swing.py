from __future__ import annotations

from .models import Property


SWING_UNAVAILABLE_REASON = "configured_swing_property_unavailable"


def swing_property_current_status(property_: Property | None) -> str:
    """Return the latest configured status for a swing property."""
    return property_.status if property_ else "missing"


def swing_property_is_available(property_: Property | None) -> bool:
    """Allow swing sends only for properties currently marked available."""
    return bool(property_ and property_.status == "available")


def stale_swing_property_diagnostic(configured_swing_property_id: str, property_: Property | None) -> dict[str, str]:
    """Build structured diagnostics for a stale configured swing property."""
    return {
        "reason": SWING_UNAVAILABLE_REASON,
        "configured_swing_property_id": configured_swing_property_id,
        "current_status": swing_property_current_status(property_),
    }


def swing_candidate_validity(configured_swing_property_id: str, property_: Property | None) -> dict[str, str | bool]:
    """Describe whether a configured swing candidate is currently sendable."""
    available = swing_property_is_available(property_)
    return {
        "candidate_property_available": available,
        "candidate_property_status": swing_property_current_status(property_),
        "validity_reason": "ok" if available else SWING_UNAVAILABLE_REASON,
    }
