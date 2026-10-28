"""AsyncOpenAI wrapper around Token Factory. chat_json is the only function
in the codebase that calls the SDK directly.

Behaviour of the Nemotron models this is built around (measured against
the live Token Factory catalog, 2026-10-27/28):
- Nano, Super and Ultra all accept response_format as a json_schema and
  return clean JSON in message.content. _strip_code_fences is a defensive
  fallback, not the primary path.
- All three are reasoning models: they spend a stochastic share of the
  completion budget on hidden reasoning before emitting any content. The
  identical prompt at the same max_tokens can finish cleanly on one call
  and truncate with zero content on the next. So finish_reason == "length"
  is reported separately from "content didn't validate"
  (ChatJsonTruncatedError vs ValidationError) and is retried at a doubled
  budget; a validation-repair retry would just waste a call on it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeVar

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# Sleeps taken between attempts: the first attempt is immediate, so there
# are len(_BACKOFF_SECONDS) + 1 attempts in total.
_BACKOFF_SECONDS = [0.5, 1.0, 2.0, 4.0]

# Transient at 50 concurrent calls against a hosted API; retrying beats
# losing a persona's reaction to a dropped socket.
_RETRYABLE = (RateLimitError, InternalServerError, APIConnectionError, APITimeoutError)

# Truncation retries, each at double the previous budget, so a fully
# failed call spends ~7x its starting budget while reaching 4x the initial
# ceiling -- where the measured success rate actually improves.
MAX_TRUNCATION_RETRIES = 2

# Per-run tally of completed requests by model. A run sets a fresh Counter
# with track_calls() at the start of its task; asyncio copies the context
# into every task created underneath, so the fan-out, digest and synthesis
# calls all land in the same Counter.
_call_counts: ContextVar[Counter | None] = ContextVar("tf_call_counts", default=None)


def track_calls() -> Counter:
    counts: Counter = Counter()
    _call_counts.set(counts)
    return counts


def _load_dotenv_fallback() -> dict[str, str]:
    """Reads repo-root .env for any var not already in os.environ. The
    codebase's only .env reader; config.py imports env() from here because
    tf_client is the bottom of the import graph."""
    values: dict[str, str] = {}
    path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


_DOTENV = _load_dotenv_fallback()


def env(key: str, default: str | None = None) -> str | None:
    """Reads an env var, falling back to the repo-root .env."""
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
    lines = text.split("\n")[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    elif lines and lines[-1].strip().endswith("```"):
        lines[-1] = lines[-1].rsplit("```", 1)[0]
    return "\n".join(lines).strip()


def _json_schema_for(schema: type[BaseModel]) -> dict:
    # strict=False: pydantic's optional fields (anyOf with null) don't
    # satisfy OpenAI-style strict mode, and the result is validated with
    # pydantic afterwards anyway.
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
    for attempt in range(len(_BACKOFF_SECONDS) + 1):
        if attempt > 0:
            await asyncio.sleep(_BACKOFF_SECONDS[attempt - 1])
        try:
            return await client.chat.completions.create(**kwargs)
        except _RETRYABLE as e:
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
    # The measured-safe floor for the smallest schema in the codebase; 400
    # truncates essentially every Nano call. Every call site passes an
    # explicit budget anyway.
    max_tokens: int = 3000,
) -> ChatJsonResult:
    """Calls model, enforces schema, retries once on validation failure
    (with the error appended to the prompt), retries 429/5xx/connection
    errors with backoff, and retries truncation at a doubled budget.
    Raises ChatJsonError (or ChatJsonTruncatedError) if it never gets a
    valid result.
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
        counts = _call_counts.get()
        if counts is not None:
            counts[model] += 1

        choice = resp.choices[0]
        content = choice.message.content or ""
        latency_ms = int((time.monotonic() - start) * 1000)

        if choice.finish_reason == "length":
            # A bigger ceiling genuinely helps (Nano, one fixed prompt, 10
            # trials per budget, scripts/probe_nemotron.py):
            #
            #    8000  3/10 ok, successes used 3.3-6.8k
            #   16000  3/10 ok, successes used 6.0-11.5k
            #   24000  5/10 ok, successes used 6.3-16.2k
            #   48000  7/10 ok, successes used 4.1-8.9k
            #
            # Empty content can't distinguish "ran away" from "needed a bit
            # more", because content only starts after reasoning finishes,
            # so every truncation is retried the same way.
            if truncation_retries >= MAX_TRUNCATION_RETRIES:
                raise ChatJsonTruncatedError(
                    f"truncated at max_tokens={current_max_tokens} "
                    f"after {truncation_retries} retries; "
                    f"raw content so far: {content[:200]!r}",
                    latency_ms,
                )
            truncation_retries += 1
            # Logged because each retry is a full extra call at double the
            # budget; silently, a stage running at 2-3x cost just looks slow.
            print(
                f"tf_client: {model} truncated at max_tokens={current_max_tokens} "
                f"({len(content)} chars of content); retrying at "
                f"{current_max_tokens * 2} "
                f"(retry {truncation_retries} of {MAX_TRUNCATION_RETRIES})",
                flush=True,
            )
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
