"""AsyncOpenAI wrapper around Token Factory. One function, chat_json, is the
only thing in the codebase that is allowed to call the SDK directly
(section 5.1).

Verified empirically against the live Token Factory catalog on 2026-10-27:
- Nano, Super, and Ultra all accept response_format as a json_schema and
  return clean JSON directly in message.content — no code-fence stripping
  needed on the happy path. _strip_code_fences below is a defensive
  fallback for whatever doesn't follow that, not the primary path.
- Not just Ultra — Super and Nano are reasoning models too. All three can
  spend completion tokens on hidden chain-of-thought before content.
  Reasoning-token usage is stochastic, not a fixed overhead: the identical
  persona-batch prompt at max_tokens=8000 finished cleanly with
  reasoning_tokens=3350 on one call and truncated with zero content on an
  immediate retry of the same request. So finish_reason == "length" is
  reported distinctly from "content didn't validate" (ChatJsonTruncatedError
  vs ValidationError) and chat_json retries it a couple of times at a
  doubled budget rather than failing immediately — a validation-repair
  retry would just waste a call on this failure mode.
- Nano specifically: confirmed under load (2026-10-27, panel/react.py's
  REACTION_MAX_TOKENS) that section 5.2's stated max_tokens=400 for
  reaction calls is wrong — it truncated ~every call. 3000 is the
  measured-safe budget (observed usage topped out at 2092).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import TypeVar

from openai import AsyncOpenAI, InternalServerError, RateLimitError
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_BACKOFF_SECONDS = [0.5, 1.0, 2.0, 4.0]


def _load_dotenv_fallback() -> dict[str, str]:
    """Reads repo-root .env for any var not already in os.environ.

    panel/config.py (stage 7) is the real env-loading home; this is a
    narrow stand-in so this module and its callers work before that
    lands.
    """
    env: dict[str, str] = {}
    root = os.path.join(os.path.dirname(__file__), "..")
    path = os.path.join(root, ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_DOTENV = _load_dotenv_fallback()


def env(key: str, default: str | None = None) -> str | None:
    """Reads an env var, falling back to the repo-root .env. Public so
    other modules can look up MODEL_NANO/MODEL_SUPER/etc. before
    panel/config.py (stage 7) exists.
    """
    return os.environ.get(key) or _DOTENV.get(key) or default


TF_API_KEY = env("TF_API_KEY")
TF_BASE_URL = env("TF_BASE_URL", "https://api.tokenfactory.nebius.com/v1")

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=TF_API_KEY, base_url=TF_BASE_URL)
    return _client


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    elif lines and lines[-1].strip().endswith("```"):
        lines[-1] = lines[-1].rsplit("```", 1)[0]
    return "\n".join(lines).strip()


def _json_schema_for(schema: type[BaseModel]) -> dict:
    # strict=False: pydantic's optional-field representation (anyOf with
    # null) doesn't satisfy OpenAI-style strict-mode constraints
    # (additionalProperties: false + every property required). We get the
    # schema shape from the model name and validate the result with
    # pydantic afterward anyway, so provider-side strict enforcement adds
    # nothing but rejected requests.
    return {
        "name": schema.__name__,
        "schema": schema.model_json_schema(),
        "strict": False,
    }


@dataclass
class ChatJsonResult:
    data: BaseModel
    latency_ms: int
    attempts: int


class ChatJsonError(Exception):
    def __init__(self, message: str, latency_ms: int):
        super().__init__(message)
        self.latency_ms = latency_ms


class ChatJsonTruncatedError(ChatJsonError):
    """finish_reason == "length": ran out of max_tokens before content."""


async def _call_with_backoff(client: AsyncOpenAI, **kwargs):
    last_exc: Exception | None = None
    for attempt, delay in enumerate(_BACKOFF_SECONDS):
        if attempt > 0:
            await asyncio.sleep(delay)
        try:
            return await client.chat.completions.create(**kwargs)
        except (RateLimitError, InternalServerError) as e:
            last_exc = e
            continue
    raise last_exc  # type: ignore[misc]


async def chat_json(
    model: str,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.7,
    seed: int | None = None,
    max_tokens: int = 400,
) -> ChatJsonResult:
    """Calls model, enforces schema, retries once on validation failure
    (with the error appended to the prompt), retries on 429/5xx with
    exponential backoff. Raises ChatJsonError (or ChatJsonTruncatedError)
    if it never gets a valid result.
    """
    client = get_client()
    response_format = {"type": "json_schema", "json_schema": _json_schema_for(schema)}
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    start = time.monotonic()
    validation_retried = False
    truncation_retries = 0
    current_max_tokens = max_tokens

    while True:
        resp = await _call_with_backoff(
            client,
            model=model,
            messages=messages,
            temperature=temperature,
            seed=seed,
            max_tokens=current_max_tokens,
            response_format=response_format,
        )

        choice = resp.choices[0]
        content = choice.message.content or ""
        latency_ms = int((time.monotonic() - start) * 1000)

        if choice.finish_reason == "length":
            # Empirically, Super and Ultra are both reasoning models that
            # burn a *stochastic* share of the token budget on hidden
            # reasoning_content before ever emitting content — the same
            # prompt at the same max_tokens can finish cleanly one call
            # and truncate with zero content the next (observed: Super,
            # 10-persona batch, max_tokens=8000, reasoning_tokens ranged
            # from ~3350 on a success to consuming the entire budget on a
            # truncated retry). So this isn't "the budget was wrong," it's
            # "this budget wasn't enough this time" — worth a couple of
            # retries at a bigger budget rather than failing immediately.
            if truncation_retries >= 2:
                raise ChatJsonTruncatedError(
                    f"truncated at max_tokens={current_max_tokens} before content "
                    f"(finish_reason=length) after {truncation_retries} retries; "
                    f"raw content so far: {content[:200]!r}",
                    latency_ms,
                )
            truncation_retries += 1
            current_max_tokens *= 2
            continue

        try:
            parsed_raw = json.loads(_strip_code_fences(content))
            data = schema.model_validate(parsed_raw)
        except (json.JSONDecodeError, ValidationError) as e:
            if validation_retried:
                raise ChatJsonError(
                    f"schema validation failed twice: {e}", latency_ms
                ) from e
            validation_retried = True
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That did not match the required schema. Error: "
                        f"{e}. Return only corrected JSON, no prose, no code fences."
                    ),
                }
            )
            continue

        return ChatJsonResult(
            data=data,
            latency_ms=latency_ms,
            attempts=1 + int(validation_retried) + truncation_retries,
        )
