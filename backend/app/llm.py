import asyncio
import json
from typing import Any, Literal, TypedDict

import httpx

from .config import get_settings


class LlmNotConfiguredError(RuntimeError):
    pass


class LlmProviderError(RuntimeError):
    pass


class LlmMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


LLM_MAX_ATTEMPTS = 3
LLM_RETRY_DELAYS_SECONDS = (0.5, 1.5)


def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("LLM output must be a JSON object")
    return parsed


async def generate_json(
    messages: list[LlmMessage],
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise LlmNotConfiguredError("DEEPSEEK_API_KEY is not configured")

    async def call_provider_once() -> tuple[dict[str, Any], dict[str, Any]]:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                json={
                    "model": settings.deepseek_model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                },
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                detail = response.text[:1200]
                raise LlmProviderError(f"LLM provider request failed with HTTP {response.status_code}: {detail}") from error
        try:
            body = response.json()
        except json.JSONDecodeError as error:
            detail = response.text[:1200]
            raise LlmProviderError(
                f"LLM provider returned non-JSON HTTP response ({response.status_code}): {detail!r}"
            ) from error
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise LlmProviderError("LLM provider returned non-text content")
        return parse_json_object(content), body

    async def call_provider() -> tuple[dict[str, Any], dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(LLM_MAX_ATTEMPTS):
            try:
                return await call_provider_once()
            except (httpx.HTTPError, LlmProviderError, KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as error:
                last_error = error
                if attempt >= LLM_MAX_ATTEMPTS - 1:
                    break
                await asyncio.sleep(LLM_RETRY_DELAYS_SECONDS[min(attempt, len(LLM_RETRY_DELAYS_SECONDS) - 1)])
        if last_error:
            raise last_error
        raise LlmProviderError("LLM provider failed without an error")

    try:
        result, _ = await call_provider()
        return result
    except httpx.HTTPError as error:
        raise LlmProviderError(f"LLM provider request failed: {error}") from error
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise LlmProviderError(f"LLM provider returned an unexpected response shape: {error}") from error
