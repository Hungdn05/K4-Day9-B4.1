"""Small diagnostics CLI for the project foundation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import read_case_request, required_case_paths
from .batch import run_batch
from .config import project_paths
from .data_loader import OlistDataLoader
from .pipeline import DisputeCoordinator
from .repository import OlistRepository


def main() -> int:
    parser = argparse.ArgumentParser(prog="ecommerce_dispute")
    subcommands = parser.add_subparsers(dest="command", required=True)
    check_data = subcommands.add_parser("check-data", help="Validate supplied CSV headers")
    check_data.add_argument("--data-dir", type=Path, default=Path("data"))
    check_input = subcommands.add_parser("check-input", help="Validate one input JSON")
    check_input.add_argument("path", type=Path)
    check_inputs = subcommands.add_parser("check-inputs", help="Validate the required 50 input JSON files")
    check_inputs.add_argument("--input-dir", type=Path, default=Path("input"))
    investigate = subcommands.add_parser("investigate", help="Resolve one validated input case")
    investigate.add_argument("--data-dir", type=Path, default=Path("data"))
    investigate.add_argument("--input", type=Path, required=True)
    investigate.add_argument("--output", type=Path, required=True)
    batch = subcommands.add_parser("batch", help="Run all 50 cases and create submission artifacts")
    batch.add_argument("--root", type=Path, default=Path("."))
    batch.add_argument("--archive", type=Path, default=Path("output.zip"))
    batch.add_argument("--use-model", action="store_true", help="Audit every handoff with configured OpenAI model")
    batch.add_argument("--model-workers", type=int, default=10)
    args = parser.parse_args()

    if args.command == "check-data":
        reports = OlistDataLoader(args.data_dir).preflight()
        print(f"Validated {len(reports)} Olist datasets.")
        return 0
    if args.command == "investigate":
        request = read_case_request(args.input)
        output, _handoffs = DisputeCoordinator(OlistRepository.from_data_dir(args.data_dir)).investigate(request)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.output}.")
        return 0
    if args.command == "batch":
        result = run_batch(
            project_paths(args.root), args.archive.resolve(),
            use_model=args.use_model, model_workers=args.model_workers,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "check-inputs":
        paths = required_case_paths(args.input_dir)
        for path in paths:
            read_case_request(path)
        print(f"Validated {len(paths)} input cases.")
        return 0
    request = read_case_request(args.path)
    print(f"Validated {request.case_id} for order {request.claimed_order_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
