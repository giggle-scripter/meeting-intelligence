from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.distilled_proposal_ranker.contracts import load_protocol
from experiments.distilled_proposal_ranker.hashing import atomic_write_json, require_runtime_output, sha256_file
from experiments.distilled_proposal_ranker.reports import evaluate_run, render_markdown


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    run_root = require_runtime_output(root, args.run_root)
    output = require_runtime_output(root, args.output)
    markdown = (root / args.markdown).resolve() if not args.markdown.is_absolute() else args.markdown.resolve()
    allowed_markdown = (root / "docs/experiments/distilled-proposal-ranker-v1-results.md").resolve()
    if markdown != allowed_markdown:
        raise ValueError("aggregate Markdown output path is not locked")
    report = evaluate_run(root, load_protocol(args.protocol), run_root)
    final_hash = atomic_write_json(output, report)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    temporary = markdown.with_suffix(".tmp")
    temporary.write_text(render_markdown(report), encoding="utf-8")
    temporary.replace(markdown)
    manifest_path = run_root / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    manifest["aggregate_reports"] = {
        output.relative_to(root).as_posix(): final_hash,
        markdown.relative_to(root).as_posix(): sha256_file(markdown),
    }
    atomic_write_json(manifest_path, manifest)
    print(f"{report['readiness']} {report['decision']}")


if __name__ == "__main__":
    main()
