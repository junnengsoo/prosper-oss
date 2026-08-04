from __future__ import annotations

from .base import PromptTemplate
from .qualification import QUALIFICATION_PROMPT
from .swinging import SWINGING_PROMPT
from .triage import TRIAGE_PROMPT
from .unit_matching import UNIT_MATCHING_PROMPT


PROMPTS: dict[str, PromptTemplate] = {
    TRIAGE_PROMPT.stage: TRIAGE_PROMPT,
    UNIT_MATCHING_PROMPT.stage: UNIT_MATCHING_PROMPT,
    QUALIFICATION_PROMPT.stage: QUALIFICATION_PROMPT,
    SWINGING_PROMPT.stage: SWINGING_PROMPT,
}


def get_prompt(stage: str) -> PromptTemplate:
    try:
        return PROMPTS[stage]
    except KeyError as error:
        raise ValueError(f"Prompt mapping missing for stage {stage}") from error
