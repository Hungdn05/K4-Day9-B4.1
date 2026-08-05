"""Thin OpenRouter chat-completions client with tool-calling support.

Built on `requests` so the project needs no extra SDK. Every call is retried on
transient failures and every call's token usage is accumulated, which CP6 reports
in metadata.json.
"""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from src import config


class LLMError(RuntimeError):
    """Raised when the provider fails in a way retrying will not fix."""


class LLMFatalError(LLMError):
    """Auth or billing failure: every subsequent call will fail the same way.

    Kept distinct so a run aborts on the first one instead of grinding through the
    remaining cases producing empty files -- which is exactly what happened when the
    account ran out of credits mid-run.
    """


@dataclass
class Usage:
    """Token counters. Guarded by a lock because the supervisor fans specialists out
    across threads and `+=` on an int is not atomic under the GIL."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retries: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, prompt: int, completion: int) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion

    def record_retry(self) -> None:
        with self._lock:
            self.retries += 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "retries": self.retries,
        }


@dataclass
class LLMClient:
    model: str = config.MODEL_NAME
    temperature: float = config.TEMPERATURE
    max_tokens: int = config.MAX_TOKENS
    usage: Usage = field(default_factory=Usage)

    # One HTTP session per thread: requests.Session is not thread-safe, and the
    # supervisor runs specialists concurrently.
    _local: threading.local = field(default_factory=threading.local, repr=False)

    @property
    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
        return session

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
        reasoning: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Return the assistant message dict (`content`, maybe `tool_calls`)."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "top_p": config.TOP_P,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "seed": config.SEED,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format
        if reasoning:
            # qwen3 is a hybrid model; only spend thinking tokens where it pays off.
            payload["reasoning"] = {"effort": "medium"}
        else:
            payload["reasoning"] = {"enabled": False}

        data = self._post("/chat/completions", payload)

        try:
            choice = data["choices"][0]
        except (KeyError, IndexError) as exc:  # pragma: no cover - provider contract
            raise LLMError(f"Malformed provider response: {json.dumps(data)[:500]}") from exc

        usage = data.get("usage") or {}
        self.usage.record(
            int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        )

        message = choice.get("message") or {}
        message.setdefault("role", "assistant")
        message.setdefault("content", "")
        return message

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {config.api_key()}",
            "Content-Type": "application/json",
            "X-Title": "K4-Day9-Multi-Agent-A2A",
        }
        url = f"{config.API_BASE}{path}"
        last_error: str = ""

        for attempt in range(config.MAX_RETRIES):
            try:
                resp = self.session.post(
                    url, headers=headers, json=payload, timeout=config.REQUEST_TIMEOUT_S
                )
            except requests.RequestException as exc:
                last_error = f"transport error: {exc}"
            else:
                if resp.status_code == 200:
                    body = resp.json()
                    # OpenRouter reports upstream failures inside a 200 body.
                    if "error" in body and not body.get("choices"):
                        last_error = f"provider error: {json.dumps(body['error'])[:300]}"
                    else:
                        return body
                elif resp.status_code in (401, 402, 403):
                    raise LLMFatalError(f"HTTP {resp.status_code}: {resp.text[:400]}")
                elif resp.status_code in (408, 409, 429, 500, 502, 503, 504):
                    last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                else:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")

            self.usage.record_retry()
            if attempt < config.MAX_RETRIES - 1:
                delay = config.RETRY_BASE_DELAY_S * (2**attempt) + random.uniform(0, 1)
                time.sleep(delay)

        raise LLMError(f"Exhausted {config.MAX_RETRIES} attempts. Last error: {last_error}")


_shared: LLMClient | None = None


def shared_client() -> LLMClient:
    """One client per process so token usage aggregates across all agents."""
    global _shared
    if _shared is None:
        _shared = LLMClient()
    return _shared
