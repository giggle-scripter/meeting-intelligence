"""Create and verify the immutable V2.27 internal baseline freeze.

This command only reads the already persisted V2.27 DEV42 artifact and the
named V2.25/V2.26 source chain.  It never runs the experiment and never opens
diagnostic, final-dev, or outer-validation data.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
V227_ARTIFACT = Path(
    "evaluation/runtime/experimental-distillation-v2/"
    "v227-dev42-volume-adaptive-policy"
)
DEFAULT_OUTPUT = Path(
    "evaluation/runtime/experimental-distillation-v2/v227-canonical-freeze"
)
SPLIT = Path(
    "evaluation/runtime/experimental-distillation-v2/kaggle/"
    "v221-neural-meeting-ranker-smoke-01-repair-v3/dataset/snapshot/v29-split.json"
)

CANONICAL_ARTIFACTS = (
    "metrics.json",
    "coverage-ceiling.json",
    "fold-policies.json",
    "split-access-audit.json",
    "tests.json",
    "status.json",
    "report.md",
)
SOURCE_FILES = {
    "v225_source": Path("scripts/experimental_distillation/audit_v225_upstream_candidates.py"),
    "v226_source": Path("scripts/experimental_distillation/run_v226_dev42_template_family_holdout.py"),
    "v227_source": Path("scripts/experimental_distillation/run_v227_dev42_volume_adaptive_policy.py"),
    "ranker_dependency": Path("scripts/experimental_distillation/run_v222_task_proposal_reranker_oof.py"),
    "union_dependency": Path("scripts/experimental_distillation/run_v223_union_proposal_oof.py"),
    "evaluation_dependency": Path("backend/app/evaluation.py"),
    "bridge_dependency": Path("experiments/distilled_proposal_ranker/v2/protocol_bridge_v217.py"),
    "dependency_manifest": Path("pyproject.toml"),
    "split_manifest": SPLIT,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def source_hashes(root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name, relative in SOURCE_FILES.items():
        path = (root / relative).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        result[name] = {"path": relative.as_posix(), "sha256": sha256(path)}
    return result


def _assert_canonical(root: Path, artifact: Path) -> dict[str, Any]:
    metrics = load(artifact / "metrics.json")
    tests = load(artifact / "tests.json")
    status = load(artifact / "status.json")
    split_access = load(artifact / "split-access-audit.json")
    if metrics.get("status") != "complete":
        raise ValueError("STOP_V227_NOT_COMPLETE")
    if metrics.get("recommendation") != "PROMISING":
        raise ValueError("STOP_V227_NOT_PROMISING")
    if not metrics.get("gates", {}).get("passed") or not tests.get("passed"):
        raise ValueError("STOP_V227_GATE")
    scope = metrics.get("scope", {})
    if scope.get("development_meetings") != 42 or scope.get("folds") != 15:
        raise ValueError("STOP_V227_SCOPE")
    for key in ("diagnostic_opened", "final_dev_opened", "outer_validation_opened"):
        if scope.get(key):
            raise ValueError(f"STOP_OPENED_{key.upper()}")
    for key in ("teacher_calls", "provider_calls", "kaggle_calls"):
        if scope.get(key) != 0:
            raise ValueError(f"STOP_EXTERNAL_{key.upper()}")
    if split_access.get("diagnostic_case_ids_read"):
        raise ValueError("STOP_DIAGNOSTIC_READ")
    if split_access.get("final_dev_case_ids_read"):
        raise ValueError("STOP_FINAL_DEV_READ")
    if split_access.get("outer_validation_case_ids_read"):
        raise ValueError("STOP_OUTER_READ")
    method = metrics.get("method", {})
    if method.get("candidate_source") != "V2.26 final_plus_bridge_plus_intermediate":
        raise ValueError("STOP_CANDIDATE_SOURCE")
    if set(method.get("forbidden_policy_features", ())) != {
        "template", "family", "case_id", "expected labels"
    }:
        raise ValueError("STOP_POLICY_FEATURES")
    if status.get("final_dev_opened") or status.get("outer_validation_opened"):
        raise ValueError("STOP_STATUS_SPLIT_ACCESS")
    return metrics


def _summary(metrics: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v227-canonical-summary-v1",
        "classification": "PROMISING_INTERNAL_ONLY",
        "validation_boundary": (
            "DEV42 leave-template-out passed. Diagnostic, final-dev, and outer-validation "
            "results are not validation evidence for this frozen baseline."
        ),
        "scope": metrics["scope"],
        "method": metrics["method"],
        "metrics": {
            "identity_precision": metrics["reranked_oof"]["task_identity_precision"],
            "identity_recall": metrics["reranked_oof"]["task_identity_recall"],
            "identity_f1": metrics["reranked_oof"]["task_identity_f1"],
            "field_accuracy": metrics["reranked_oof"]["field_accuracy"],
            "supported_family_macro_f1": metrics["stability"]["family_metrics"]["macro_f1"],
            "supported_family_worst_f1": metrics["stability"]["family_metrics"]["worst_f1"],
            "supported_family_worst_family": metrics["stability"]["family_metrics"]["worst_family"],
        },
        "gates": metrics["gates"],
        "split_access": metrics["split_access"],
        "source_hashes": source,
    }


def _environment() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for name in ("numpy", "scikit-learn", "pytest"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "unavailable"
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "packages": packages,
    }


def _artifact_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def create(root: Path, output: Path, artifact_relative: Path = V227_ARTIFACT) -> dict[str, Any]:
    root, output = root.resolve(), output.resolve()
    artifact = (root / artifact_relative).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"immutable output already exists: {output}")
    if not artifact.is_dir():
        raise FileNotFoundError(artifact)
    metrics = _assert_canonical(root, artifact)
    sources = source_hashes(root)

    output.mkdir(parents=True, exist_ok=True)
    canonical = output / "canonical"
    canonical.mkdir()
    for name in CANONICAL_ARTIFACTS:
        source = artifact / name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, canonical / name)
    write_json(output / "source-hashes.json", sources)
    write_json(output / "canonical-summary.json", _summary(metrics, sources))
    write_json(output / "artifact-hashes.json", _artifact_hashes(output))
    (output / "closeout.md").write_text(
        "# V2.27 canonical internal baseline freeze\n\n"
        "Classification: **PROMISING_INTERNAL_ONLY**. DEV42 leave-template-out passed; "
        "diagnostic, final-dev, and outer-validation results are not validation evidence "
        "for this frozen baseline.\n\n"
        "This package copies only the compact persisted V2.27 artifacts. Its source chain "
        "covers V2.25 candidate construction, V2.26 nested template/family holdout, and "
        "V2.27 volume-adaptive policy. The package is immutable: rerunning create against "
        "a non-empty output is rejected.\n\n"
        "Verification command: `python scripts/experimental_distillation/freeze_v227_baseline.py "
        "--verify`. No training, provider, teacher, Kaggle, diagnostic, final-dev, or "
        "outer-validation access is performed.\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "v227-canonical-freeze-v1",
        "baseline": "V2.27_DEV42_VOLUME_ADAPTIVE_POLICY",
        "classification": "PROMISING_INTERNAL_ONLY",
        "immutable": True,
        "canonical_package": True,
        "source_artifact": artifact_relative.as_posix(),
        "source_hashes": sources,
        "artifact_hashes": _artifact_hashes(output),
        "environment": _environment(),
        "command": "python scripts/experimental_distillation/freeze_v227_baseline.py",
        "scope": metrics["scope"],
        "gates": metrics["gates"],
        "validation_boundary": (
            "DEV42 leave-template-out passed; diagnostic, final-dev, and outer-validation "
            "are closed and are not validation evidence for this baseline."
        ),
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def verify(output: Path, root: Path = ROOT) -> dict[str, Any]:
    output, root = output.resolve(), root.resolve()
    manifest = load(output / "manifest.json")
    if manifest.get("immutable") is not True or manifest.get("canonical_package") is not True:
        raise ValueError("STOP_NOT_CANONICAL_FREEZE")
    expected = manifest.get("artifact_hashes", {})
    actual = _artifact_hashes(output)
    if actual != expected:
        raise ValueError("STOP_ARTIFACT_HASH_MISMATCH")
    expected_sources = manifest.get("source_hashes", {})
    actual_sources = source_hashes(root)
    if actual_sources != expected_sources:
        raise ValueError("STOP_SOURCE_HASH_MISMATCH")
    summary = load(output / "canonical-summary.json")
    if summary.get("classification") != "PROMISING_INTERNAL_ONLY":
        raise ValueError("STOP_CLASSIFICATION")
    if summary.get("split_access", {}).get("final_dev_case_ids_read"):
        raise ValueError("STOP_FINAL_DEV_READ")
    if summary.get("split_access", {}).get("outer_validation_case_ids_read"):
        raise ValueError("STOP_OUTER_READ")
    return {"status": "verified", "package": str(output), "artifact_count": len(actual)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--artifact", type=Path, default=V227_ARTIFACT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        result = verify(args.output, args.root) if args.verify else create(args.root, args.output, args.artifact)
    except BaseException as error:
        print(json.dumps({"status": "STOP", "error": repr(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
