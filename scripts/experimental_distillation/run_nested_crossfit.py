from __future__ import annotations

import argparse
from pathlib import Path

from experiments.distilled_proposal_ranker.crossfit import run_crossfit
from experiments.distilled_proposal_ranker.hashing import require_model_output, require_runtime_output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--folds", type=Path, required=True)
    parser.add_argument("--teacher-cache", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    report = run_crossfit(
        root, args.protocol, args.folds,
        require_runtime_output(root, args.teacher_cache),
        require_runtime_output(root, args.output_root),
        require_model_output(root, args.model_root),
        resume=args.resume,
    )
    print(report["status"])


if __name__ == "__main__":
    main()
