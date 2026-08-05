"""CP5 batch execution, trace capture, metadata, and submission archive."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import platform
from shutil import copy2
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from .config import MODEL_CONFIG, POLICY_VERSION, ProjectPaths
from .contracts import read_case_request, required_case_paths, validate_case_output
from .openai_client import OpenAIResponsesClient, read_openai_api_key
from .pipeline import DisputeCoordinator
from .repository import OlistRepository


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _write_json(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def create_submission_archive(paths: ProjectPaths, archive_path: Path) -> dict[str, Any]:
    """Stage SUBMISSION/output and zip exactly output/EC_001..EC_050.json.

    The upload validator requires the ``output/`` prefix on every entry and
    rejects anything else, so the archive carries that prefix and nothing else:
    no Finder side-car files, no staging directory, no bare file names.
    """

    expected_names = [f"EC_{number:03d}.json" for number in range(1, 51)]
    expected_entries = [f"output/{name}" for name in expected_names]
    source_paths = [paths.output_dir / name for name in expected_names]
    missing = [path.name for path in source_paths if not path.is_file()]
    if missing:
        raise ValueError(f"Cannot package submission; missing outputs: {missing}")

    submission_output_dir = paths.root / "SUBMISSION" / "output"
    submission_output_dir.mkdir(parents=True, exist_ok=True)
    unexpected = [path.name for path in submission_output_dir.glob("*.json") if path.name not in expected_names]
    if unexpected:
        raise ValueError(f"Unexpected JSON files in SUBMISSION/output: {unexpected}")
    staged_paths: list[Path] = []
    for source_path in source_paths:
        staged_path = submission_output_dir / source_path.name
        copy2(source_path, staged_path)
        staged_paths.append(staged_path)

    archive_temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    with ZipFile(archive_temporary, "w", compression=ZIP_DEFLATED) as archive:
        for staged_path in staged_paths:
            archive.write(staged_path, arcname=f"output/{staged_path.name}")
    with ZipFile(archive_temporary) as archive:
        written = archive.namelist()
    if written != expected_entries:
        archive_temporary.unlink(missing_ok=True)
        raise ValueError(f"Archive layout is not the required layout: {written}")
    archive_temporary.replace(archive_path)
    return {
        "submission_dir": str(paths.root / "SUBMISSION"),
        "archive": str(archive_path),
        "archive_entry_count": len(written),
        "archive_entries": f"{written[0]}..{written[-1]}",
    }


def run_batch(
    paths: ProjectPaths,
    archive_path: Path,
    use_model: bool = False,
    model_workers: int = 10,
) -> dict[str, Any]:
    """Process exactly EC_001..EC_050 and overwrite only their run artifacts."""

    started_at = datetime.now(UTC)
    input_paths = required_case_paths(paths.input_dir)
    requests = [read_case_request(path) for path in input_paths]
    for path, request in zip(input_paths, requests, strict=True):
        if request.case_id != path.stem:
            raise ValueError(f"{path.name}: case_id must equal filename stem")

    repository = OlistRepository.from_data_dir(paths.data_dir)
    coordinator = DisputeCoordinator(repository)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.logging_dir.mkdir(parents=True, exist_ok=True)

    expected_names = {path.name for path in input_paths}
    unexpected_outputs = [path for path in paths.output_dir.glob("*.json") if path.name not in expected_names]
    if unexpected_outputs:
        raise ValueError(f"Unexpected output JSON files present: {[path.name for path in unexpected_outputs]}")

    trace_events: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()
    output_paths: list[Path] = []
    for input_path, request in zip(input_paths, requests, strict=True):
        output, handoffs = coordinator.investigate(request)
        validate_case_output(output, request.case_id)
        output_path = paths.output_dir / input_path.name
        _write_json(output_path, output)
        output_paths.append(output_path)
        issue_counts[output["case_assessment"]["primary_issue"]] += 1
        for sequence, handoff in enumerate(handoffs, start=1):
            trace_events.append({
                "event": "agent_handoff",
                "sequence_in_case": sequence,
                **handoff.as_json(),
            })

    model_usage = {"invocation_count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    if use_model:
        client = OpenAIResponsesClient(read_openai_api_key(paths.root / ".env"))
        with ThreadPoolExecutor(max_workers=model_workers, thread_name_prefix="openai-audit") as executor:
            audits = list(executor.map(client.audit_handoff, trace_events))
        for event, audit in zip(trace_events, audits, strict=True):
            event["model_audit"] = {
                "model": MODEL_CONFIG["name"],
                "response_id": audit.response_id,
                "text": audit.text,
                "usage": {
                    "input_tokens": audit.input_tokens,
                    "output_tokens": audit.output_tokens,
                    "total_tokens": audit.total_tokens,
                },
            }
            model_usage["invocation_count"] += 1
            model_usage["input_tokens"] += audit.input_tokens
            model_usage["output_tokens"] += audit.output_tokens
            model_usage["total_tokens"] += audit.total_tokens

    trace_path = paths.logging_dir / "trace.jsonl"
    trace_temporary = trace_path.with_suffix(".jsonl.tmp")
    trace_temporary.write_text(
        "".join(json.dumps(event, ensure_ascii=False, default=_json_default) + "\n" for event in trace_events),
        encoding="utf-8",
    )
    trace_temporary.replace(trace_path)

    completed_at = datetime.now(UTC)
    metadata = {
        "model": dict(MODEL_CONFIG),
        "policy_version": POLICY_VERSION,
        "framework": {
            "name": "custom-python-multi-agent",
            "orchestration": "ThreadPoolExecutor domain workers with typed handoffs",
            "decision_mode": "deterministic CSV joins, arithmetic, and policy rules",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
        "run": {
            "started_at_utc": started_at.isoformat(),
            "completed_at_utc": completed_at.isoformat(),
            "case_count": len(output_paths),
            "trace_event_count": len(trace_events),
            "primary_issue_counts": dict(sorted(issue_counts.items())),
            "model_usage": model_usage,
        },
    }
    _write_json(paths.logging_dir / "metadata.json", metadata)

    submission = create_submission_archive(paths, archive_path)

    return {
        "output_count": len(output_paths),
        "trace_event_count": len(trace_events),
        **submission,
        "primary_issue_counts": dict(sorted(issue_counts.items())),
        "model_usage": model_usage,
    }
