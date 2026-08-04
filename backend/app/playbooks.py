from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Property, PropertyPlaybook
from .schemas import PlaybookBlock, PropertyPlaybookIn
from .app_config import get_config_value


PLAYBOOK_PLACEHOLDERS = {
    "unit_info",
    "tenant_notes",
    "tenant_facing_caveats",
    "profile_form",
    "property_name",
    "rent",
    "available_date",
    "property_guru_listing",
}

PLAYBOOK_BLOCK_FIELDS = (
    "initial_reply_blocks",
    "qualification_suitable_blocks",
    "qualification_not_suitable_blocks",
)

MAX_PLAYBOOK_DELAY_SECONDS = 30

STARTER_INITIAL_REPLY_BLOCKS = [
    {
        "type": "message",
        "text": "Hi, yes this unit is still available.",
    },
    {"type": "delay", "seconds": 0.5},
    {
        "type": "message",
        "text": (
            "To help me better serve you, could you kindly fill in the details below? 😊\n\n"
            "姓名 Name\n"
            "年龄 Age\n"
            "预算 Budget\n"
            "入住人数 No. of people staying\n"
            "入住关系 Relationship between pax\n"
            "性别 Gender\n"
            "国籍 Nationality\n"
            "种族 Race\n"
            "职业 Occupation\n"
            "工作准证类型 Type of Pass\n"
            "准证过期日 Pass Expiry Date\n"
            "入住日期 Move In Date\n"
            "租赁期 Lease\n"
            "是否需要家具 / Furnishing requirement (Fully / Partial / Unfurnished)\n"
            "宠物 Any pet\n"
            "抽烟 Smokes"
        ),
    },
    {"type": "gallery", "mode": "enabled_property_gallery"},
]

STARTER_QUALIFICATION_SUITABLE_BLOCKS = [
    {"type": "message", "text": "Thanks, let me check with the landlord and update you."},
]

STARTER_QUALIFICATION_NOT_SUITABLE_BLOCKS = [
    {"type": "message", "text": "Sorry, profile may not be suitable for this unit."},
]

LEGACY_INITIAL_REPLY_TEXTS = {
    "Hi, thanks for enquiring about {unit_info}. I'm the listing agent.",
    "Hi there! Thank you for inquiring about {unit_info}. I'm the listing agent.",
}

STOCK_GALLERY_CAPTIONS = {
    "Here are some photos of the unit.",
}


@dataclass(frozen=True)
class RenderedPlaybookPart:
    type: str
    text: str = ""
    seconds: float = 0


def playbook_blocks(value: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []


def get_property_playbook(session: Session, property_id: str) -> PropertyPlaybook | None:
    return session.scalar(select(PropertyPlaybook).where(PropertyPlaybook.property_id == property_id))


def ensure_starter_property_playbook(session: Session, property_id: str) -> PropertyPlaybook:
    """Create an explicit starter playbook for a property if no saved playbook exists."""
    existing = get_property_playbook(session, property_id)
    if existing:
        return existing

    playbook = PropertyPlaybook(
        property_id=property_id,
        initial_reply_blocks=list(STARTER_INITIAL_REPLY_BLOCKS),
        qualification_suitable_blocks=list(STARTER_QUALIFICATION_SUITABLE_BLOCKS),
        qualification_not_suitable_blocks=list(STARTER_QUALIFICATION_NOT_SUITABLE_BLOCKS),
        enabled=True,
    )
    session.add(playbook)
    session.flush()
    return playbook


def list_property_playbooks(session: Session) -> list[PropertyPlaybook]:
    return list(session.scalars(select(PropertyPlaybook).order_by(PropertyPlaybook.property_id)).all())


def upsert_property_playbook(session: Session, property_id: str, payload: PropertyPlaybookIn) -> PropertyPlaybook:
    validate_playbook_payload(payload)
    property_ = session.scalar(select(Property).where(Property.property_id == property_id))
    if not property_:
        raise ValueError("Property not found")

    playbook = get_property_playbook(session, property_id)
    values = {
        field: [block.model_dump(exclude_none=True) for block in getattr(payload, field)]
        for field in PLAYBOOK_BLOCK_FIELDS
    }
    values["enabled"] = payload.enabled
    if playbook:
        for key, value in values.items():
            setattr(playbook, key, value)
        return playbook

    playbook = PropertyPlaybook(property_id=property_id, **values)
    session.add(playbook)
    session.flush()
    return playbook


def validate_playbook_payload(payload: PropertyPlaybookIn) -> None:
    for field in PLAYBOOK_BLOCK_FIELDS:
        validate_playbook_blocks(getattr(payload, field), field)


def validate_playbook_blocks(blocks: list[PlaybookBlock], field_name: str = "playbook") -> None:
    for index, block in enumerate(blocks):
        label = f"{field_name}[{index}]"
        if block.type == "message":
            if not block.text or not block.text.strip():
                raise ValueError(f"{label}: message block text must not be blank")
            validate_placeholders(block.text, label)
            continue
        if block.type == "delay":
            if block.seconds is None:
                raise ValueError(f"{label}: delay block seconds is required")
            if block.seconds < 0 or block.seconds > MAX_PLAYBOOK_DELAY_SECONDS:
                raise ValueError(f"{label}: delay seconds must be between 0 and {MAX_PLAYBOOK_DELAY_SECONDS}")
            continue
        if block.type == "gallery":
            if block.mode != "enabled_property_gallery":
                raise ValueError(f"{label}: gallery mode must be enabled_property_gallery")
            continue
        if block.type == "profile_form":
            continue
        raise ValueError(f"{label}: unsupported block type")


def validate_placeholders(text: str, label: str) -> None:
    try:
        parsed_fields = [field_name for _, field_name, _, _ in Formatter().parse(text) if field_name]
    except ValueError as error:
        raise ValueError(f"{label}: invalid placeholder syntax: {error}") from error
    placeholders = {field_name.split(".", 1)[0].split("[", 1)[0] for field_name in parsed_fields}
    unsupported = sorted(placeholders - PLAYBOOK_PLACEHOLDERS)
    if unsupported:
        raise ValueError(f"{label}: unsupported placeholder(s): {', '.join('{' + item + '}' for item in unsupported)}")


def unit_info(property_: Property | None) -> str:
    if not property_:
        return ""
    return property_.full_address or property_.property_name


def rent_text(property_: Property | None) -> str:
    if not property_ or property_.asking_rent is None:
        return ""
    value = int(property_.asking_rent) if float(property_.asking_rent).is_integer() else property_.asking_rent
    return str(value)


def render_playbook_blocks(
    session: Session,
    blocks: list[dict[str, Any]],
    *,
    property_: Property | None = None,
) -> list[RenderedPlaybookPart]:
    display_property = property_
    tenant_notes = (display_property.tenant_facing_caveats if display_property else "").strip()
    context = {
        "unit_info": unit_info(display_property),
        "tenant_notes": tenant_notes,
        "tenant_facing_caveats": tenant_notes,
        "profile_form": get_config_value(session, "profile_form"),
        "property_name": display_property.property_name if display_property else "",
        "rent": rent_text(display_property),
        "available_date": display_property.available_from if display_property and display_property.available_from else "",
        "property_guru_listing": display_property.property_url if display_property and display_property.property_url else "",
    }
    rendered: list[RenderedPlaybookPart] = []
    for raw_block in blocks:
        block = PlaybookBlock.model_validate(raw_block)
        if block.type == "message":
            text = (block.text or "").format(**context).strip()
            if text:
                rendered.append(RenderedPlaybookPart("text", text=text))
        elif block.type == "profile_form":
            text = get_config_value(session, "profile_form").strip()
            if text:
                rendered.append(RenderedPlaybookPart("text", text=text))
        elif block.type == "delay":
            rendered.append(RenderedPlaybookPart("delay", seconds=float(block.seconds or 0)))
        elif block.type == "gallery":
            rendered.append(RenderedPlaybookPart("gallery"))
    return rendered


def _message_texts(blocks: list[dict[str, Any]]) -> list[str]:
    return [str(block.get("text") or "").strip() for block in blocks if block.get("type") == "message"]


def strip_stock_gallery_caption_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        block
        for block in blocks
        if not (block.get("type") == "message" and str(block.get("text") or "").strip() in STOCK_GALLERY_CAPTIONS)
    ]


def normalize_legacy_stock_blocks(field_name: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map old seeded MVP playbook copy to the current simplified send sequence."""
    texts = _message_texts(blocks)
    if field_name == "initial_reply_blocks":
        has_legacy_profile_prompt = any("{profile_form}" in text for text in texts)
        has_legacy_gallery_caption = any(text == "Here are some photos of the unit." for text in texts)
        if texts and texts[0] in LEGACY_INITIAL_REPLY_TEXTS and (has_legacy_profile_prompt or has_legacy_gallery_caption):
            return list(STARTER_INITIAL_REPLY_BLOCKS)

    return strip_stock_gallery_caption_blocks(blocks)


def enabled_blocks_for_stage(playbook: PropertyPlaybook | None, field_name: str) -> list[dict[str, Any]]:
    if not playbook or not playbook.enabled:
        return []
    blocks = playbook_blocks(getattr(playbook, field_name, None))
    return normalize_legacy_stock_blocks(field_name, blocks)
