"""A2A trace writer.

One JSON object per line recording every message that crosses an agent boundary:
what the supervisor decided to do, what each specialist did with its tools, what the
verifier said, and how the case ended. README section 8 wants the latest run only, so
the file is truncated when a run starts rather than appended to.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import config


class TraceWriter:
    def __init__(self, path: Path | None = None, truncate: bool = True) -> None:
        self.path = path or config.TRACE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        if truncate:
            self.path.write_text("")

    def emit(self, event: str, case_id: str | None = None, **fields: Any) -> None:
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "event": event,
                "case_id": case_id,
                **fields,
            }
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


class NullTrace(TraceWriter):
    """Used when running ad-hoc experiments that should not clobber the real trace."""

    def __init__(self) -> None:  # noqa: D107
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, event: str, case_id: str | None = None, **fields: Any) -> None:
        return
