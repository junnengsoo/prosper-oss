import asyncio
import json
import os
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


def _normalize_langfuse_env(settings: Any) -> None:
    public_key = (settings.langfuse_public_key or "").strip()
    secret_key = (settings.langfuse_secret_key or "").strip()
    base_url = (settings.langfuse_base_url or settings.langfuse_host or "").strip()
    if public_key and not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
    if secret_key and not os.environ.get("LANGFUSE_SECRET_KEY"):
        os.environ["LANGFUSE_SECRET_KEY"] = secret_key
    if base_url and not os.environ.get("LANGFUSE_BASE_URL"):
        os.environ["LANGFUSE_BASE_URL"] = base_url
    if base_url and not os.environ.get("LANGFUSE_HOST"):
        os.environ["LANGFUSE_HOST"] = base_url


def has_langfuse_config(settings: Any) -> bool:
    _normalize_langfuse_env(settings)
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
        and os.environ.get("LANGFUSE_BASE_URL")
    )


def _get_langfuse_modules(settings: Any):
    _normalize_langfuse_env(settings)
    from langfuse import get_client, propagate_attributes

    return get_client, propagate_attributes


def usage_details(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") or {}
    details = {
        "input": usage.get("prompt_tokens") or usage.get("input_tokens"),
        "output": usage.get("completion_tokens") or usage.get("output_tokens"),
        "total": usage.get("total_tokens"),
    }
    return {key: value for key, value in details.items() if isinstance(value, int)}


def flush_langfuse() -> None:
    settings = get_settings()
    if not has_langfuse_config(settings):
        return
    try:
        get_client, _ = _get_langfuse_modules(settings)
        get_client().flush()
    except Exception:
        pass


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
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise LlmNotConfiguredError("DEEPSEEK_API_KEY is not configured")

    context = context or {}
    stage = str(context.get("stage") or "llm")
    metadata = {
        "feature": "whatsapp_pa",
        "stage": stage,
        **(context.get("metadata") or {}),
    }
    conversation_id = context.get("conversation_id")
    property_id = context.get("property_id")
    if conversation_id is not None:
        metadata["conversation_id"] = conversation_id
    if property_id:
        metadata["property_id"] = property_id

    langfuse = None
    propagate_attributes = None
    if has_langfuse_config(settings):
        try:
            get_client, propagate_attributes = _get_langfuse_modules(settings)
            langfuse = get_client()
        except Exception:
            langfuse = None
            propagate_attributes = None

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
        if not langfuse:
            result, _ = await call_provider()
            return result

        tags = ["whatsapp-pa", stage]
        session_id = str(conversation_id) if conversation_id is not None else None
        with propagate_attributes(session_id=session_id, tags=tags, metadata=metadata):
            with langfuse.start_as_current_observation(
                as_type="generation",
                name=f"whatsapp-pa-{stage}",
                model=settings.deepseek_model,
            ) as generation:
                generation.update(
                    input=messages,
                    metadata=metadata,
                )
                try:
                    result, body = await call_provider()
                    generation.update(output=result, usage_details=usage_details(body))
                    return result
                except Exception as error:
                    generation.update(level="ERROR", status_message=str(error))
                    raise
    except httpx.HTTPError as error:
        raise LlmProviderError(f"LLM provider request failed: {error}") from error
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise LlmProviderError(f"LLM provider returned an unexpected response shape: {error}") from error
