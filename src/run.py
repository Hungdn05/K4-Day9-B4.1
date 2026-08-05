"""Entry point: run the multi-agent investigation over one, some, or all 50 cases.

  python -m src.run --all
  python -m src.run --cases EC_001 EC_002
  python -m src.run --all --sequential      # no concurrent specialist dispatch
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from src import config
from src.agents.supervisor import SupervisorAgent
from src.data.store import get_store
from src.llm.client import LLMFatalError, shared_client
from src.schema import validate_structure
from src.trace import TraceWriter


def load_case(case_id: str) -> dict:
    return json.loads((config.INPUT_DIR / f"{case_id}.json").read_text(encoding="utf-8"))


def all_case_ids() -> list[str]:
    return sorted(path.stem for path in config.INPUT_DIR.glob("EC_*.json"))


def already_done(case_id: str) -> bool:
    """True when a previous run left a structurally valid file for this case."""
    path = config.OUTPUT_DIR / f"{case_id}.json"
    if not path.exists():
        return False
    try:
        return not validate_structure(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return False


def write_output(case_id: str, output: dict) -> None:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / f"{case_id}.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-agent e-commerce dispute resolution")
    parser.add_argument("--cases", nargs="*", help="Case ids to run, e.g. EC_001 EC_002")
    parser.add_argument("--all", action="store_true", help="Run all 50 cases")
    parser.add_argument("--sequential", action="store_true", help="Disable concurrent dispatch")
    parser.add_argument("--no-trace", action="store_true", help="Do not write logging/trace.jsonl")
    parser.add_argument(
        "--append-trace",
        action="store_true",
        help="Keep the existing trace instead of truncating it",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Investigate this many cases at once. Cases are independent; each still "
        "gets its own supervisor deciding its own course.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override config.MODEL_NAME for this run. Must stay within the 10B limit; "
        "whatever is used ends up recorded in logging/metadata.json.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip cases that already have a schema-valid output file. Implies "
        "--append-trace so an interrupted run can be continued without losing its trace.",
    )
    args = parser.parse_args()

    if args.model:
        shared_client().model = args.model
        print(f"model override: {args.model}\n")

    case_ids = all_case_ids() if args.all else (args.cases or ["EC_001"])

    if args.resume:
        args.append_trace = True
        pending = [cid for cid in case_ids if not already_done(cid)]
        skipped = len(case_ids) - len(pending)
        if skipped:
            print(f"resuming: {skipped} case(s) already complete, {len(pending)} to go\n")
        case_ids = pending
        if not case_ids:
            print("nothing to do -- every case already has a valid output file")
            return 0

    trace = TraceWriter(truncate=not args.append_trace) if not args.no_trace else None
    supervisor = SupervisorAgent(trace=trace, parallel=not args.sequential)

    get_store()  # warm the CSV cache before any thread touches it

    started = time.time()
    runs = []
    issues: Counter[str] = Counter()
    done = 0

    def report(run) -> None:
        nonlocal done
        done += 1
        if run.output:
            write_output(run.case_id, run.output)
            issues[run.output["case_assessment"]["primary_issue"]] += 1

        flags = []
        if run.fallback_applied:
            flags.append("FALLBACK")
        if run.degraded_reason:
            flags.append("DEGRADED")
        primary = (run.output or {}).get("case_assessment", {}).get("primary_issue", "-")
        print(
            f"[{done:>2}/{len(case_ids)}] {run.case_id}  {'ok ' if run.ok else 'XX '}"
            f"{primary:24s} steps={run.steps:<2} verify={run.verify_count} "
            f"{run.seconds:>6.1f}s  {' '.join(flags)}",
            flush=True,
        )
        if run.degraded_reason:
            print(f"          reason: {run.degraded_reason}", flush=True)

    aborted: str | None = None
    try:
        if args.workers > 1:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(supervisor.run, load_case(cid)): cid for cid in case_ids}
                try:
                    for future in as_completed(futures):
                        run = future.result()
                        runs.append(run)
                        report(run)
                except LLMFatalError:
                    for future in futures:
                        future.cancel()
                    raise
            runs.sort(key=lambda r: r.case_id)
        else:
            for case_id in case_ids:
                run = supervisor.run(load_case(case_id))
                runs.append(run)
                report(run)
    except LLMFatalError as exc:
        aborted = str(exc)
        print(f"\n!! RUN ABORTED -- the provider rejected the request:\n   {aborted[:400]}")
        print(f"   {len(runs)}/{len(case_ids)} cases finished before the abort.")

    elapsed = time.time() - started
    clean = sum(1 for r in runs if r.ok)
    fallback = sum(1 for r in runs if r.fallback_applied)
    degraded = sum(1 for r in runs if r.degraded_reason)
    retried = sum(1 for r in runs if r.verify_count > 1)

    print(f"\n{'=' * 76}")
    print(f"cases              : {len(runs)}")
    print(f"verifier clean     : {clean}/{len(runs)}")
    print(f"needed a re-ruling : {retried}")
    print(f"needed fallback    : {fallback}")
    print(f"degraded           : {degraded}")
    print(f"elapsed            : {elapsed:.1f}s  ({elapsed / max(len(runs), 1):.1f}s per case)")
    print(f"usage              : {json.dumps(shared_client().usage.as_dict())}")
    print("primary issues     :")
    for name, count in issues.most_common():
        print(f"   {count:>3}  {name}")

    if trace:
        print(f"trace              : {config.TRACE_PATH.relative_to(config.ROOT)}")
    if aborted:
        return 2
    return 0 if clean == len(runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
