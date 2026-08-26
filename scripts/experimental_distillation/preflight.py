"""Run the locked evidence, trace, corpus, and Q2 drift gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import load_protocol
from experiments.distilled_proposal_ranker.hashing import require_runtime_output
from experiments.distilled_proposal_ranker.inventory import run_preflight


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    repo_root = Path.cwd().resolve()
    output_root = require_runtime_output(repo_root, args.output_root)
    report = run_preflight(repo_root, load_protocol(args.protocol), output_root)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
