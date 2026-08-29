from __future__ import annotations

from .base import PromptTemplate
from .rental_listing_matching import RENTAL_LISTING_MATCHING_PROMPT
from .triage import TRIAGE_PROMPT


PROMPTS: dict[str, PromptTemplate] = {
    TRIAGE_PROMPT.stage: TRIAGE_PROMPT,
    RENTAL_LISTING_MATCHING_PROMPT.stage: RENTAL_LISTING_MATCHING_PROMPT,
}


def get_prompt(stage: str) -> PromptTemplate:
    try:
        return PROMPTS[stage]
    except KeyError as error:
        raise ValueError(f"Prompt mapping missing for stage {stage}") from error
