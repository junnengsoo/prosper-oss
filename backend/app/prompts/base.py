from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplate:
    stage: str
    system: str
    user_template: str

    def render(self, **values: str) -> str:
        rendered = self.user_template
        for key, value in values.items():
            rendered = rendered.replace(f"{{{key}}}", value)
        return rendered
