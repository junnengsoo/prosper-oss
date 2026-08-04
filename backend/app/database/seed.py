import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from .models import AppConfig, Property
from ..playbooks import ensure_starter_property_playbook


SEED_DATA_DIR = Path(__file__).resolve().parent / "seed_data"
LISTINGS_VIEW_SEED_FILE = SEED_DATA_DIR / "demo_listings.json"

DEFAULT_PROFILE_FORM = """Budget:
No. of people staying:
Relationship between people staying:
Nationality:
Race:
Occupation:
Type of Pass:
Move In Date:
Lease:
Furnishing requirement (Fully / Partial / Unfurnished):
Any pet:
Smokes:"""

DEFAULT_TEST_PLAYBOOK_PROPERTY_IDS = {
    "PROP-001",
    "PROP-002",
    "PROP-003",
    "PROP-004",
}


def seed_app_config(session: Session) -> None:
    defaults = {
        "pause_ai": "false",
        "send_lock": "false",
        "profile_form": DEFAULT_PROFILE_FORM,
    }
    for key, value in defaults.items():
        existing = session.scalar(select(AppConfig).where(AppConfig.key == key))
        if existing:
            continue
        session.add(AppConfig(key=key, value=value))


def extract_property_rows_from_unit_matching_prompt(prompt_text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw_line in prompt_text.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line.startswith("{") or not line.endswith("}"):
            continue

        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        if {"property_id", "property_name", "full_address"} <= set(row):
            rows.append(
                {
                    "property_id": str(row.get("property_id") or "").strip(),
                    "property_name": str(row.get("property_name") or "").strip(),
                    "full_address": str(row.get("full_address") or "").strip(),
                    "propertyguru_listing_id": str(row.get("propertyguru_listing_id") or "").strip(),
                }
            )

    return [row for row in rows if row["property_id"] and row["property_name"]]


def normalize_listing_status(value: str) -> str:
    return "available" if value.strip().lower() == "available" else "unavailable"


def listing_full_address(row: dict) -> str:
    name = str(row.get("project_name") or "").strip()
    unit = str(row.get("unit_number") or "").strip()
    return f"{name}, {unit}" if unit else name


def load_listings_view_seed_rows(seed_file: Path = LISTINGS_VIEW_SEED_FILE) -> list[dict]:
    if not seed_file.exists():
        return []
    data = json.loads(seed_file.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Listings View seed must be a list")
    return [row for row in data if isinstance(row, dict)]


def property_kwargs_from_listing_seed(row: dict) -> dict:
    return {
        "property_id": str(row.get("property_id") or "").strip(),
        "property_name": str(row.get("project_name") or "").strip(),
        "status": normalize_listing_status(str(row.get("listing_status") or "")),
        "bedrooms": row.get("bedrooms"),
        "bathrooms": row.get("bathrooms"),
        "asking_rent": row.get("asking_rent"),
        "available_from": str(row.get("available_date") or "").strip() or None,
        "full_address": listing_full_address(row),
        "propertyguru_listing_id": str(row.get("propertyguru_listing_id") or "").strip() or None,
        "landlord_profile_requirements": str(row.get("preferred_tenant_profile") or "").strip(),
        "tenant_facing_caveats": str(row.get("tenant_facing_caveats") or "").strip(),
    }


def seed_listings_view_properties(session: Session, seed_file: Path = LISTINGS_VIEW_SEED_FILE) -> None:
    for row in load_listings_view_seed_rows(seed_file):
        values = property_kwargs_from_listing_seed(row)
        if not values["property_id"] or not values["property_name"]:
            continue
        existing = session.scalar(select(Property).where(Property.property_id == values["property_id"]))
        if existing:
            continue
        session.add(Property(**values))


def seed_properties(session: Session) -> None:
    if not get_settings().seed_properties:
        return
    seed_listings_view_properties(session)


def seed_property_playbooks(session: Session, property_ids: set[str] | None = None) -> None:
    target_property_ids = property_ids or DEFAULT_TEST_PLAYBOOK_PROPERTY_IDS
    properties = session.scalars(
        select(Property)
        .where(Property.property_id.in_(target_property_ids))
        .order_by(Property.property_id)
    ).all()
    for property_ in properties:
        ensure_starter_property_playbook(session, property_.property_id)


def seed_all(session: Session) -> None:
    seed_app_config(session)
    seed_properties(session)
    session.commit()
