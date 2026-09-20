from __future__ import annotations

import argparse
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import load_protocol
from experiments.distilled_proposal_ranker.folds import assert_no_fold_leakage, build_fold_manifest
from experiments.distilled_proposal_ranker.hashing import atomic_write_json, require_runtime_output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo_root = Path.cwd().resolve()
    output = require_runtime_output(repo_root, args.output)
    manifest = build_fold_manifest(repo_root, load_protocol(args.protocol))
    assert_no_fold_leakage(manifest)
    digest = atomic_write_json(output, manifest)
    print(digest)


if __name__ == "__main__":
    main()
