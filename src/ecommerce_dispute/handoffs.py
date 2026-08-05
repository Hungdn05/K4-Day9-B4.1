"""Auditable, JSON-compatible messages passed between the planned agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class AgentName(StrEnum):
    COORDINATOR = "coordinator"
    CUSTOMER = "customer"
    ORDER_PRODUCT = "order_product"
    PAYMENT = "payment"
    DELIVERY = "delivery"
    POLICY = "policy"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class AgentHandoff:
    """A bounded inter-agent message suitable for one JSONL trace event."""

    case_id: str
    agent: AgentName
    payload: dict[str, Any]
    source_record_ids: tuple[str, ...]
    created_at: str

    @classmethod
    def create(
        cls,
        case_id: str,
        agent: AgentName,
        payload: dict[str, Any],
        source_record_ids: tuple[str, ...] = (),
    ) -> "AgentHandoff":
        if not case_id:
            raise ValueError("case_id is required for every handoff")
        if any(not record_id for record_id in source_record_ids):
            raise ValueError("source_record_ids cannot contain empty values")
        return cls(
            case_id=case_id,
            agent=agent,
            payload=payload,
            source_record_ids=source_record_ids,
            created_at=datetime.now(UTC).isoformat(),
        )

    def as_json(self) -> dict[str, Any]:
        """Return a serializable form with a JSON array for source IDs."""

        document = asdict(self)
        document["agent"] = self.agent.value
        document["source_record_ids"] = list(self.source_record_ids)
        return document
