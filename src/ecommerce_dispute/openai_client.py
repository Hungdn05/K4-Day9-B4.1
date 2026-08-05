"""Minimal Responses API client used to audit each agent handoff."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from http.client import RemoteDisconnected
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import MODEL_CONFIG


class OpenAIConfigurationError(ValueError):
    """Raised when the local API credential is absent or malformed."""


class OpenAIRequestError(RuntimeError):
    """Raised after a Responses API request cannot be completed safely."""


@dataclass(frozen=True)
class ModelAudit:
    response_id: str
    text: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


def read_openai_api_key(env_path: Path) -> str:
    """Read only OPENAI_API_KEY without logging or exporting the secret."""

    if not env_path.is_file():
        raise OpenAIConfigurationError(f"Missing dotenv file: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "OPENAI_API_KEY":
            value = value.strip().strip('"').strip("'")
            if value:
                return value
            break
    raise OpenAIConfigurationError("OPENAI_API_KEY is empty in .env")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


class OpenAIResponsesClient:
    """Call gpt-4o-mini with bounded prompts and no server-side storage."""

    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, timeout_seconds: float = 30.0) -> None:
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds

    def audit_handoff(self, event: dict[str, Any]) -> ModelAudit:
        agent = event["agent"]
        document = {
            "case_id": event["case_id"],
            "agent": agent,
            "payload": event["payload"],
            "source_record_ids": event["source_record_ids"],
        }
        body = {
            "model": MODEL_CONFIG["name"],
            "instructions": (
                f"You are the {agent} agent receiving one deterministic Olist handoff. "
                "The CSV joins, date arithmetic, Decimal arithmetic, policy, and evidence have already been verified "
                "by deterministic Python and must not be recalculated or changed. Do not invent facts. Review the "
                "domain fields in the payload and acknowledge the handoff in one short sentence using exactly this "
                "format: ACCEPTED: <name the domain content received>."
            ),
            "input": json.dumps(document, ensure_ascii=False, default=_json_default),
            "max_output_tokens": 80,
            "store": False,
        }
        request = Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        response_document: dict[str, Any] | None = None
        max_attempts = 6
        for attempt in range(max_attempts):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    response_document = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == max_attempts - 1:
                    raise OpenAIRequestError(f"Responses API returned HTTP {exc.code}") from exc
                retry_after = exc.headers.get("Retry-After")
                if retry_after:
                    try:
                        time.sleep(min(float(retry_after), 30.0))
                        continue
                    except ValueError:
                        pass
            except (URLError, TimeoutError, RemoteDisconnected, json.JSONDecodeError) as exc:
                if attempt == max_attempts - 1:
                    raise OpenAIRequestError(f"Responses API request failed: {type(exc).__name__}") from exc
            time.sleep(min(2 ** attempt, 20))
        if response_document is None:
            raise OpenAIRequestError("Responses API returned no document")
        text_parts = [
            content.get("text", "")
            for item in response_document.get("output", [])
            for content in item.get("content", [])
            if content.get("type") == "output_text"
        ]
        text = " ".join(part.strip() for part in text_parts if part.strip())
        if not text:
            raise OpenAIRequestError("Responses API returned no output text")
        usage = response_document.get("usage", {})
        return ModelAudit(
            response_id=response_document.get("id", ""),
            text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
        )
