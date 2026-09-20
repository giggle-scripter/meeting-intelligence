"""Locked five-by-four nested cross-fit orchestration for three-seed ensembles."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import subprocess
import statistics
from typing import Any

import torch

from backend.app.evaluation import aggregate_results, compare_case

from .calibration import Selection, calibrate_probabilities, fit_temperature
from .candidate_pool import build_candidate_pool
from .contracts import GroundedSpan, ProposalRecord, RerankerExample, SpanExample, SpanPrediction, load_protocol
from .hashing import atomic_write_json, canonical_json_hash, completed_manifest_reusable, require_model_output, require_runtime_output, sha256_file
from .metrics import exact_span_prf, prf, ranking_metrics, token_prf
from .reranker_dataset import build_reranker_examples
from .reranker_model import load_reranker
from .reranker_training import predict_reranker_logits, reuse_reranker_checkpoint, train_reranker
from .replay import replay_case
from .safety import SafetyReport
from .span_dataset import bio_labels, build_span_examples, read_evidence
from .span_model import load_span_model, set_deterministic_seed
from .span_training import predict_span_examples, reuse_span_checkpoint, train_span_model
from .trace_reader import load_trace_set, records


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _gold_by_case(rows: list[dict[str, Any]]) -> dict[str, list[GroundedSpan]]:
    result: dict[str, list[GroundedSpan]] = defaultdict(list)
    for row in rows:
        result[row["case_id"]].append(GroundedSpan(**row["action_evidence"]))
    return result


def _exact_pairs(predictions: list[SpanPrediction], case_ids: set[str]) -> list[tuple[str, GroundedSpan]]:
    return [
        (item.case_id, GroundedSpan(clause_id=item.target_clause_id, start=item.start, end=item.end, text=item.text))
        for item in predictions
        if item.case_id in case_ids
    ]


def _ensemble_span_predictions(predictions: list[SpanPrediction], outer_fold: int) -> list[SpanPrediction]:
    grouped: dict[tuple[str, str, int, int, str], list[SpanPrediction]] = defaultdict(list)
    for item in predictions:
        grouped[(item.case_id, item.target_clause_id, item.start, item.end, item.text)].append(item)
    output = []
    for key in sorted(grouped):
        items = grouped[key]
        first = items[0]
        checkpoint_hash = canonical_json_hash(sorted(item.checkpoint_hash for item in items))
        output.append(
            first.model_copy(
                update={
                    "prediction_id": canonical_json_hash({"span": key, "outer_fold": outer_fold}),
                    "start_logit": sum(item.start_logit for item in items) / len(items),
                    "end_logit": sum(item.end_logit for item in items) / len(items),
                    "span_score": sum(item.span_score for item in items) / len(items),
                    "no_action_score": sum(item.no_action_score for item in items) / len(items),
                    "model_name": "FacebookAI/xlm-roberta-base::three-seed-union",
                    "training_seed": 0,
                    "checkpoint_hash": checkpoint_hash,
                }
            )
        )
    return output


def _token_metrics(examples: list[SpanExample], predictions: list[SpanPrediction], tokenizer: Any, max_length: int) -> dict[str, float]:
    by_clause: dict[tuple[str, str], list[SpanPrediction]] = defaultdict(list)
    for item in predictions:
        by_clause[(item.case_id, item.target_clause_id)].append(item)
    predicted_labels = []
    gold_labels = []
    for example in examples:
        encoded = tokenizer(example.context, truncation=True, max_length=max_length, return_offsets_mapping=True, padding="max_length")
        offsets = [tuple(item) for item in encoded["offset_mapping"]]
        gold = bio_labels(offsets, example)
        proposed = []
        spans = by_clause.get((example.case_id, example.target_clause_id), [])
        for start, end in offsets:
            if start == end or start < example.target_start_in_context or end > example.target_end_in_context:
                proposed.append(-100)
                continue
            raw_start = start - example.target_start_in_context
            raw_end = end - example.target_start_in_context
            label = 0
            for span in spans:
                if raw_start >= span.start and raw_end <= span.end:
                    label = 1 if raw_start == span.start else 2
                    break
            proposed.append(label)
        predicted_labels.append(proposed)
        gold_labels.append(gold)
    return token_prf(predicted_labels, gold_labels)


def _checkpoint_signature(code_sha: str, protocol_hash: str, fold_hash: str, data_hash: str, model_name: str, seed: int) -> dict[str, Any]:
    return {"code_sha": code_sha, "protocol_hash": protocol_hash, "fold_hash": fold_hash, "data_hash": data_hash, "model_name": model_name, "seed": seed}


def _grouped_exact_recall(
    evidence_rows: list[dict[str, Any]],
    predicted: set[tuple[str, str, int, int, str]],
    group_value: Any,
) -> dict[str, float]:
    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in evidence_rows:
        span = row["action_evidence"]
        key = (row["case_id"], span["clause_id"], span["start"], span["end"], span["text"])
        grouped[str(group_value(row))].append(key in predicted)
    return {key: sum(values) / len(values) for key, values in sorted(grouped.items())}


def _grounding_recall(
    gold_pairs: list[tuple[str, GroundedSpan]],
    predicted_pairs: list[tuple[str, GroundedSpan]],
) -> tuple[float, float]:
    source_hits = overlap_hits = 0
    for case_id, gold_span in gold_pairs:
        candidates = [
            span for predicted_case, span in predicted_pairs
            if predicted_case == case_id and span.clause_id == gold_span.clause_id
        ]
        source_hits += bool(candidates)
        overlap_hits += any(
            max(0, min(span.end, gold_span.end) - max(span.start, gold_span.start))
            / max(1, max(span.end, gold_span.end) - min(span.start, gold_span.start))
            >= 0.5
            for span in candidates
        )
    denominator = len(gold_pairs) or 1
    return source_hits / denominator, overlap_hits / denominator


def _choose_inner_task_identity_selection(
    repo_root: Path,
    case_ids: set[str],
    examples: list[RerankerExample],
    probabilities: list[float],
    proposals_by_case: dict[str, list[ProposalRecord]],
    baseline_traces: dict[str, dict[str, Any]],
    expected_outputs: dict[str, dict[str, Any]],
    threshold_grid: list[float],
    precision_floor: float,
) -> Selection:
    indices_by_case: dict[str, list[int]] = defaultdict(list)
    for index, example in enumerate(examples):
        indices_by_case[example.case_id].append(index)
    proposal_by_id = {
        proposal.proposal_id: proposal
        for proposals in proposals_by_case.values()
        for proposal in proposals
    }
    candidates: list[Selection] = []
    for top_k in (3, 5, 8):
        for threshold in threshold_grid:
            comparisons = []
            for case_id in sorted(case_ids):
                ranked = sorted(
                    indices_by_case.get(case_id, []),
                    key=lambda index: (-probabilities[index], examples[index].proposal_id),
                )
                selected = [
                    proposal_by_id[examples[index].proposal_id]
                    for index in ranked[:top_k]
                    if probabilities[index] >= threshold
                ]
                public, _ = replay_case(repo_root, case_id, baseline_traces[case_id], selected, SafetyReport())
                comparisons.append(compare_case(case_id, expected_outputs[case_id], public))
            metrics = aggregate_results(comparisons)
            candidates.append(
                Selection(
                    threshold=threshold,
                    top_k=top_k,
                    precision=metrics["task_identity_precision"],
                    recall=metrics["task_identity_recall"],
                    f1=metrics["task_identity_f1"],
                    precision_constraint_met=metrics["task_identity_precision"] >= precision_floor,
                )
            )
    valid = [item for item in candidates if item.precision_constraint_met]
    if valid:
        return max(valid, key=lambda item: (item.f1, item.recall, item.threshold, -item.top_k))
    return max(candidates, key=lambda item: (item.precision, item.recall, item.threshold, -item.top_k))


def run_crossfit(
    repo_root: Path,
    protocol_path: Path,
    folds_path: Path,
    teacher_cache: Path,
    output_root: Path,
    model_root: Path,
    *,
    resume: bool,
) -> dict[str, Any]:
    del teacher_cache  # cache-only labels are read from the teacher report; missing cache has zero weight
    output_root = require_runtime_output(repo_root, output_root)
    model_root = require_model_output(repo_root, model_root)
    snapshot = load_protocol(protocol_path)
    protocol = snapshot.protocol
    folds = json.loads(folds_path.read_text(encoding="utf-8-sig"))
    fold_hash = sha256_file(folds_path)
    if folds.get("protocol_sha256") != snapshot.sha256:
        raise ValueError("protocol/fold hash mismatch")
    code_sha = _git_sha()
    run_signature = {
        "code_sha": code_sha,
        "protocol_hash": snapshot.sha256,
        "data_hash": folds["dataset_hash"],
        "fold_hash": fold_hash,
        "trace_manifest_hash": folds["trace_manifest_sha256"],
        "baseline_trace_manifest_hash": folds["baseline_trace_manifest_sha256"],
        "branch": "experimental/distilled-proposal-ranker-v1",
        "base_commit": protocol.base_commit,
        "span_model_name": protocol.span_model_name,
        "reranker_model_name": protocol.reranker_model_name,
        "training_seeds": protocol.training_seeds,
    }
    final_manifest_path = output_root / "run-manifest.json"
    if resume and completed_manifest_reusable(final_manifest_path, run_signature):
        return json.loads(final_manifest_path.read_text(encoding="utf-8-sig"))

    traces = load_trace_set(repo_root / protocol.trace_path)
    baseline_traces = load_trace_set(repo_root / protocol.baseline_trace_path)
    expected_outputs = {
        case_id: json.loads((repo_root / "data/validation" / case_id / "expected_output.json").read_text(encoding="utf-8-sig"))
        for case_id in traces
    }
    evidence_rows = read_evidence(repo_root / protocol.evidence_path)
    gold = _gold_by_case(evidence_rows)
    all_gold_pairs = [(case_id, span) for case_id, spans in gold.items() for span in spans]
    all_outer_span_predictions: list[SpanPrediction] = []
    outer_span_predictions_by_seed: dict[int, list[SpanPrediction]] = {
        seed: [] for seed in protocol.training_seeds
    }
    all_outer_proposals: list[ProposalRecord] = []
    all_reranker_rows: list[dict[str, Any]] = []
    all_selected: list[ProposalRecord] = []
    per_fold = []
    safety = SafetyReport()
    committed_paths = subprocess.check_output(
        ["git", "diff", "--name-only", f"{protocol.base_commit}..HEAD"], text=True
    ).splitlines()
    for path in committed_paths:
        normalized = path.replace("\\", "/")
        if normalized.startswith("evaluation/runtime/") or normalized.startswith("artifacts/models/"):
            safety.violation("runtime_artifact_committed_count", normalized)
    challenger_outputs: dict[str, dict[str, Any]] = {}
    challenger_outputs_by_seed: dict[int, dict[str, dict[str, Any]]] = {
        seed: {} for seed in protocol.training_seeds
    }
    ranker_rows_by_seed: dict[int, list[dict[str, Any]]] = {
        seed: [] for seed in protocol.training_seeds
    }
    sidecars = {}

    for fold_record in folds["outer_folds"]:
        outer_id = int(fold_record["outer_fold_id"])
        outer_train = set(fold_record["train_case_ids"])
        outer_valid = set(fold_record["valid_case_ids"])
        if outer_train & outer_valid:
            safety.violation("cross_fold_training_leak_count", f"outer-{outer_id}")
            raise ValueError("cross-fold training leak")
        train_examples_all = build_span_examples(
            {case_id: traces[case_id] for case_id in sorted(outer_train)},
            evidence_rows,
            folds["outer_fold_by_case"],
            include_gold_case_ids=outer_train,
            label_weights=protocol.label_weights.model_dump(),
        )
        valid_examples_all = build_span_examples(
            {case_id: traces[case_id] for case_id in sorted(outer_valid)},
            evidence_rows,
            folds["outer_fold_by_case"],
            include_gold_case_ids=outer_valid,
            label_weights=protocol.label_weights.model_dump(),
        )
        inner_span_by_seed: dict[int, list[SpanPrediction]] = {}
        outer_span_by_seed: dict[int, list[SpanPrediction]] = {}
        selected_span_settings = {}
        for seed in protocol.training_seeds:
            lr_results = {}
            for learning_rate in protocol.span_learning_rates:
                predictions_for_lr: list[SpanPrediction] = []
                validation_scores = []
                epochs = []
                for inner_id in range(protocol.inner_folds):
                    inner_valid_ids = {case_id for case_id, value in fold_record["inner_fold_by_case"].items() if value == inner_id}
                    inner_train_examples = [item for item in train_examples_all if item.case_id not in inner_valid_ids]
                    inner_valid_examples = [item for item in train_examples_all if item.case_id in inner_valid_ids]
                    set_deterministic_seed(seed)
                    model, tokenizer = load_span_model(protocol.span_model_name)
                    checkpoint_dir = model_root / f"outer-{outer_id}" / f"span-seed-{seed}" / f"lr-{learning_rate}" / f"inner-{inner_id}"
                    signature = _checkpoint_signature(code_sha, snapshot.sha256, fold_hash, folds["dataset_hash"], protocol.span_model_name, seed)
                    checkpoint_values = {
                        **signature,
                        "outer_fold": outer_id,
                        "inner_fold": inner_id,
                        "learning_rate": learning_rate,
                        "requested_max_epochs": protocol.max_epochs,
                        "selection": "inner_validation",
                    }
                    result = reuse_span_checkpoint(model, checkpoint_dir, checkpoint_values) if resume else None
                    if result is None:
                        result = train_span_model(
                            model, tokenizer, inner_train_examples, inner_valid_examples,
                            seed=seed, learning_rate=learning_rate, weight_decay=protocol.weight_decay,
                            warmup_ratio=protocol.warmup_ratio, max_epochs=protocol.max_epochs,
                            patience=protocol.early_stopping_patience, gradient_clip_norm=protocol.gradient_clip_norm,
                            max_length=protocol.span_max_length, checkpoint_dir=checkpoint_dir,
                            manifest_values={**signature, "outer_fold": outer_id, "inner_fold": inner_id},
                        )
                    predictions_for_lr.extend(
                        predict_span_examples(
                            model, tokenizer, inner_valid_examples, max_length=protocol.span_max_length,
                            model_name=protocol.span_model_name, outer_fold=outer_id,
                            training_seed=seed, checkpoint_hash=result.checkpoint_hash,
                        )
                    )
                    validation_scores.append((result.token_f1, result.exact_recall))
                    epochs.append(result.epoch)
                average_token = sum(item[0] for item in validation_scores) / len(validation_scores)
                exact = exact_span_prf(_exact_pairs(predictions_for_lr, outer_train), [(case_id, span) for case_id in outer_train for span in gold.get(case_id, [])])["recall"]
                lr_results[learning_rate] = (average_token, exact, round(sum(epochs) / len(epochs)), predictions_for_lr)
            selected_lr = max(lr_results, key=lambda value: (lr_results[value][0], lr_results[value][1], -value))
            token_score, exact_score, selected_epoch, inner_predictions = lr_results[selected_lr]
            inner_span_by_seed[seed] = inner_predictions
            selected_span_settings[seed] = {"learning_rate": selected_lr, "epoch": selected_epoch, "inner_token_f1": token_score, "inner_exact_recall": exact_score}
            set_deterministic_seed(seed)
            model, tokenizer = load_span_model(protocol.span_model_name)
            outer_dir = model_root / f"outer-{outer_id}" / f"span-seed-{seed}" / "outer-train"
            outer_signature = _checkpoint_signature(code_sha, snapshot.sha256, fold_hash, folds["dataset_hash"], protocol.span_model_name, seed)
            outer_checkpoint_values = {
                **outer_signature,
                "outer_fold": outer_id,
                "inner_fold": None,
                "learning_rate": selected_lr,
                "requested_max_epochs": selected_epoch,
                "selection": "fixed_epoch",
            }
            result = reuse_span_checkpoint(model, outer_dir, outer_checkpoint_values) if resume else None
            if result is None:
                result = train_span_model(
                    model, tokenizer, train_examples_all, train_examples_all,
                    seed=seed, learning_rate=selected_lr, weight_decay=protocol.weight_decay,
                    warmup_ratio=protocol.warmup_ratio, max_epochs=selected_epoch,
                    patience=selected_epoch + 1, gradient_clip_norm=protocol.gradient_clip_norm,
                    max_length=protocol.span_max_length, checkpoint_dir=outer_dir,
                    manifest_values={**outer_signature, "outer_fold": outer_id, "inner_fold": None},
                    select_best_checkpoint=False,
                )
            outer_span_by_seed[seed] = predict_span_examples(
                model, tokenizer, valid_examples_all, max_length=protocol.span_max_length,
                model_name=protocol.span_model_name, outer_fold=outer_id,
                training_seed=seed, checkpoint_hash=result.checkpoint_hash,
            )

        inner_span_union = _ensemble_span_predictions(
            [item for seed in protocol.training_seeds for item in inner_span_by_seed[seed]], outer_id
        )
        outer_span_union = _ensemble_span_predictions(
            [item for seed in protocol.training_seeds for item in outer_span_by_seed[seed]], outer_id
        )
        all_outer_span_predictions.extend(outer_span_union)
        for seed in protocol.training_seeds:
            outer_span_predictions_by_seed[seed].extend(outer_span_by_seed[seed])
        reranker_model_probe, reranker_tokenizer = load_reranker(protocol.reranker_model_name)
        del reranker_model_probe
        train_proposals_by_case = {
            case_id: build_candidate_pool(case_id, traces[case_id], [item for item in inner_span_union if item.case_id == case_id], max_proposals=protocol.max_proposals_per_meeting)
            for case_id in sorted(outer_train)
        }
        valid_proposals_by_case = {
            case_id: build_candidate_pool(case_id, traces[case_id], [item for item in outer_span_union if item.case_id == case_id], max_proposals=protocol.max_proposals_per_meeting)
            for case_id in sorted(outer_valid)
        }
        train_reranker_examples = [
            example
            for case_id in sorted(outer_train)
            for example in build_reranker_examples(train_proposals_by_case[case_id], traces[case_id], gold.get(case_id, []), reranker_tokenizer, outer_fold=outer_id, max_length=protocol.reranker_max_length)
        ]
        valid_reranker_examples = [
            example
            for case_id in sorted(outer_valid)
            for example in build_reranker_examples(valid_proposals_by_case[case_id], traces[case_id], gold.get(case_id, []), reranker_tokenizer, outer_fold=outer_id, max_length=protocol.reranker_max_length)
        ]
        inner_logits_by_seed = {}
        outer_logits_by_seed = {}
        temperatures = {}
        selected_reranker_settings = {}
        for seed in protocol.training_seeds:
            lr_results = {}
            for learning_rate in protocol.reranker_learning_rates:
                oof_logits_by_id = {}
                epochs = []
                for inner_id in range(protocol.inner_folds):
                    inner_valid_ids = {case_id for case_id, value in fold_record["inner_fold_by_case"].items() if value == inner_id}
                    inner_train = [item for item in train_reranker_examples if item.case_id not in inner_valid_ids]
                    inner_valid = [item for item in train_reranker_examples if item.case_id in inner_valid_ids]
                    set_deterministic_seed(seed)
                    model, tokenizer = load_reranker(protocol.reranker_model_name)
                    checkpoint_dir = model_root / f"outer-{outer_id}" / f"reranker-seed-{seed}" / f"lr-{learning_rate}" / f"inner-{inner_id}"
                    signature = _checkpoint_signature(code_sha, snapshot.sha256, fold_hash, folds["dataset_hash"], protocol.reranker_model_name, seed)
                    checkpoint_values = {
                        **signature,
                        "outer_fold": outer_id,
                        "inner_fold": inner_id,
                        "learning_rate": learning_rate,
                        "requested_max_epochs": protocol.max_epochs,
                        "selection": "inner_validation",
                    }
                    result = reuse_reranker_checkpoint(model, checkpoint_dir, checkpoint_values) if resume else None
                    if result is None:
                        result = train_reranker(
                            model, tokenizer, inner_train, inner_valid, seed=seed,
                            learning_rate=learning_rate, weight_decay=protocol.weight_decay,
                            warmup_ratio=protocol.warmup_ratio, max_epochs=protocol.max_epochs,
                            patience=protocol.early_stopping_patience, gradient_clip_norm=protocol.gradient_clip_norm,
                            max_length=protocol.reranker_max_length, checkpoint_dir=checkpoint_dir,
                            manifest_values={**signature, "outer_fold": outer_id, "inner_fold": inner_id},
                            valid_expected_positive_count=sum(len(gold.get(case_id, [])) for case_id in inner_valid_ids),
                        )
                    logits = predict_reranker_logits(model, tokenizer, inner_valid, max_length=protocol.reranker_max_length)
                    oof_logits_by_id.update(zip((item.proposal_id for item in inner_valid), logits, strict=True))
                    epochs.append(result.epoch)
                ordered_logits = [oof_logits_by_id[item.proposal_id] for item in train_reranker_examples]
                metrics = ranking_metrics(
                    [item.case_id for item in train_reranker_examples],
                    torch.sigmoid(torch.tensor(ordered_logits)).tolist(),
                    [item.label for item in train_reranker_examples],
                    expected_positive_count=sum(len(gold.get(case_id, [])) for case_id in outer_train),
                )
                lr_results[learning_rate] = (float(metrics["pr_auc"] or 0.0), float(metrics["recall_at_30"] or 0.0), round(sum(epochs) / len(epochs)), ordered_logits)
            selected_lr = max(lr_results, key=lambda value: (lr_results[value][0], lr_results[value][1], -value))
            pr_auc, recall30, selected_epoch, inner_logits = lr_results[selected_lr]
            inner_logits_by_seed[seed] = inner_logits
            temperatures[seed] = fit_temperature(inner_logits, [item.label for item in train_reranker_examples])
            selected_reranker_settings[seed] = {"learning_rate": selected_lr, "epoch": selected_epoch, "inner_pr_auc": pr_auc, "inner_recall_at_30": recall30}
            set_deterministic_seed(seed)
            model, tokenizer = load_reranker(protocol.reranker_model_name)
            checkpoint_dir = model_root / f"outer-{outer_id}" / f"reranker-seed-{seed}" / "outer-train"
            outer_signature = _checkpoint_signature(code_sha, snapshot.sha256, fold_hash, folds["dataset_hash"], protocol.reranker_model_name, seed)
            outer_checkpoint_values = {
                **outer_signature,
                "outer_fold": outer_id,
                "inner_fold": None,
                "learning_rate": selected_lr,
                "requested_max_epochs": selected_epoch,
                "selection": "fixed_epoch",
            }
            result = reuse_reranker_checkpoint(model, checkpoint_dir, outer_checkpoint_values) if resume else None
            if result is None:
                result = train_reranker(
                    model, tokenizer, train_reranker_examples, train_reranker_examples,
                    seed=seed, learning_rate=selected_lr, weight_decay=protocol.weight_decay,
                    warmup_ratio=protocol.warmup_ratio, max_epochs=selected_epoch,
                    patience=selected_epoch + 1, gradient_clip_norm=protocol.gradient_clip_norm,
                    max_length=protocol.reranker_max_length, checkpoint_dir=checkpoint_dir,
                    manifest_values={**outer_signature, "outer_fold": outer_id, "inner_fold": None},
                    select_best_checkpoint=False,
                    valid_expected_positive_count=sum(len(gold.get(case_id, [])) for case_id in outer_train),
                )
            outer_logits_by_seed[seed] = predict_reranker_logits(model, tokenizer, valid_reranker_examples, max_length=protocol.reranker_max_length)
        ensemble_inner = [
            sum(calibrate_probabilities([inner_logits_by_seed[seed][index]], temperatures[seed])[0] for seed in protocol.training_seeds) / len(protocol.training_seeds)
            for index in range(len(train_reranker_examples))
        ]
        selection = _choose_inner_task_identity_selection(
            repo_root,
            outer_train,
            train_reranker_examples,
            ensemble_inner,
            train_proposals_by_case,
            baseline_traces,
            expected_outputs,
            protocol.threshold_grid,
            protocol.gates.task_identity_precision,
        )
        ensemble_outer = [
            sum(calibrate_probabilities([outer_logits_by_seed[seed][index]], temperatures[seed])[0] for seed in protocol.training_seeds) / len(protocol.training_seeds)
            for index in range(len(valid_reranker_examples))
        ]
        by_case_indices: dict[str, list[int]] = defaultdict(list)
        for index, example in enumerate(valid_reranker_examples):
            by_case_indices[example.case_id].append(index)
        selected_ids = set()
        for case_id, indices in by_case_indices.items():
            ranked = sorted(indices, key=lambda index: (-ensemble_outer[index], valid_reranker_examples[index].proposal_id))
            selected_ids.update(valid_reranker_examples[index].proposal_id for index in ranked[: selection.top_k] if ensemble_outer[index] >= selection.threshold)
        proposal_by_id = {item.proposal_id: item for values in valid_proposals_by_case.values() for item in values}
        selected_proposals = [proposal_by_id[proposal_id] for proposal_id in sorted(selected_ids)]
        all_outer_proposals.extend(proposal_by_id.values())
        all_selected.extend(selected_proposals)
        for example, score in zip(valid_reranker_examples, ensemble_outer, strict=True):
            all_reranker_rows.append({"case_id": example.case_id, "proposal_id": example.proposal_id, "score": score, "label": example.label, "outer_fold": outer_id, "selected": example.proposal_id in selected_ids})
        selected_ids_by_seed: dict[int, set[str]] = {}
        for seed in protocol.training_seeds:
            calibrated = calibrate_probabilities(outer_logits_by_seed[seed], temperatures[seed])
            seed_selected: set[str] = set()
            for indices in by_case_indices.values():
                ranked = sorted(indices, key=lambda index: (-calibrated[index], valid_reranker_examples[index].proposal_id))
                seed_selected.update(
                    valid_reranker_examples[index].proposal_id
                    for index in ranked[: selection.top_k]
                    if calibrated[index] >= selection.threshold
                )
            selected_ids_by_seed[seed] = seed_selected
            ranker_rows_by_seed[seed].extend(
                {
                    "case_id": example.case_id,
                    "proposal_id": example.proposal_id,
                    "score": score,
                    "label": example.label,
                    "outer_fold": outer_id,
                    "selected": example.proposal_id in seed_selected,
                }
                for example, score in zip(valid_reranker_examples, calibrated, strict=True)
            )
        for case_id in sorted(outer_valid):
            public, sidecar = replay_case(repo_root, case_id, baseline_traces[case_id], [item for item in selected_proposals if item.case_id == case_id], safety)
            challenger_outputs[case_id] = public
            sidecars[case_id] = sidecar
            for seed in protocol.training_seeds:
                seed_safety = SafetyReport()
                seed_public, _ = replay_case(
                    repo_root,
                    case_id,
                    baseline_traces[case_id],
                    [proposal_by_id[item] for item in sorted(selected_ids_by_seed[seed]) if proposal_by_id[item].case_id == case_id],
                    seed_safety,
                )
                challenger_outputs_by_seed[seed][case_id] = seed_public
        per_fold.append({"outer_fold": outer_id, "span_settings": selected_span_settings, "reranker_settings": selected_reranker_settings, "temperatures": temperatures, "selection": selection.__dict__})
        atomic_write_json(output_root / "oof" / f"outer-{outer_id}.json", {"span_predictions": [item.model_dump(mode="json") for item in outer_span_union], "proposals": [item.model_dump(mode="json") for item in proposal_by_id.values()], "ranker": [item for item in all_reranker_rows if item["outer_fold"] == outer_id], "selected_proposal_ids": sorted(selected_ids)})

    comparisons = [compare_case(case_id, expected_outputs[case_id], challenger_outputs[case_id]) for case_id in sorted(traces)]
    baseline_comparisons = [
        compare_case(case_id, expected_outputs[case_id], {"tasks": baseline_traces[case_id].get("final_tasks", [])})
        for case_id in sorted(traces)
    ]
    task_metrics = aggregate_results(comparisons)
    baseline_metrics = aggregate_results(baseline_comparisons)
    comparison_by_case = {item.case_id: item for item in comparisons}
    baseline_by_case = {item.case_id: item for item in baseline_comparisons}
    task_metrics_by_seed: dict[int, dict[str, Any]] = {}
    for seed in protocol.training_seeds:
        seed_comparisons = {
            case_id: compare_case(case_id, expected_outputs[case_id], challenger_outputs_by_seed[seed][case_id])
            for case_id in sorted(traces)
        }
        seed_per_fold = []
        for outer_id in range(protocol.outer_folds):
            case_ids = sorted(case_id for case_id in traces if folds["outer_fold_by_case"][case_id] == outer_id)
            seed_fold = aggregate_results([seed_comparisons[case_id] for case_id in case_ids])
            baseline_fold = aggregate_results([baseline_by_case[case_id] for case_id in case_ids])
            seed_per_fold.append({"outer_fold": outer_id, "metrics": seed_fold, "f1_delta": seed_fold["task_identity_f1"] - baseline_fold["task_identity_f1"]})
        task_metrics_by_seed[seed] = {
            "global": aggregate_results(list(seed_comparisons.values())),
            "per_fold": seed_per_fold,
        }
    per_case_counts = [
        {
            "case_id": case_id,
            "outer_fold": folds["outer_fold_by_case"][case_id],
            "expected": comparison_by_case[case_id].expected_task_count,
            "challenger_actual": comparison_by_case[case_id].actual_task_count,
            "challenger_matched": comparison_by_case[case_id].matched_task_count,
            "baseline_actual": baseline_by_case[case_id].actual_task_count,
            "baseline_matched": baseline_by_case[case_id].matched_task_count,
        }
        for case_id in sorted(traces)
    ]
    per_fold_task = []
    for outer_id in range(protocol.outer_folds):
        case_ids = {item["case_id"] for item in per_case_counts if item["outer_fold"] == outer_id}
        challenger_fold = aggregate_results([comparison_by_case[case_id] for case_id in sorted(case_ids)])
        baseline_fold = aggregate_results([baseline_by_case[case_id] for case_id in sorted(case_ids)])
        per_fold_task.append(
            {
                "outer_fold": outer_id,
                "challenger": challenger_fold,
                "baseline": baseline_fold,
                "f1_delta": challenger_fold["task_identity_f1"] - baseline_fold["task_identity_f1"],
            }
        )
    span_exact = exact_span_prf(_exact_pairs(all_outer_span_predictions, set(traces)), all_gold_pairs)
    _probe_model, span_tokenizer = load_span_model(protocol.span_model_name)
    all_valid_examples = build_span_examples(traces, evidence_rows, folds["outer_fold_by_case"], include_gold_case_ids=set(traces), label_weights=protocol.label_weights.model_dump())
    span_token = _token_metrics(all_valid_examples, all_outer_span_predictions, span_tokenizer, protocol.span_max_length)
    span_by_seed = {}
    for seed in protocol.training_seeds:
        seed_predictions = outer_span_predictions_by_seed[seed]
        span_by_seed[seed] = {
            "exact": exact_span_prf(_exact_pairs(seed_predictions, set(traces)), all_gold_pairs),
            "token": _token_metrics(all_valid_examples, seed_predictions, span_tokenizer, protocol.span_max_length),
        }
    neural_pairs = set((case_id, span.clause_id, span.start, span.end, span.text) for case_id, span in _exact_pairs(all_outer_span_predictions, set(traces)))
    neural_grounded_pairs = _exact_pairs(all_outer_span_predictions, set(traces))
    lattice_pairs = set()
    for case_id, trace in traces.items():
        for item in records(trace.get("proposal_span_identities_v3")):
            if item.get("action_span"):
                span = item["action_span"]
                lattice_pairs.add((case_id, span["clause_id"], span["start"], span["end"], span["text"]))
    gold_set = {(case_id, span.clause_id, span.start, span.end, span.text) for case_id, span in all_gold_pairs}
    span_source_recall = {"lattice": len(lattice_pairs & gold_set) / len(gold_set), "neural": len(neural_pairs & gold_set) / len(gold_set), "union": len((lattice_pairs | neural_pairs) & gold_set) / len(gold_set)}
    union_grounded_pairs = list(neural_grounded_pairs) + [
        (case_id, GroundedSpan(clause_id=clause_id, start=start, end=end, text=text))
        for case_id, clause_id, start, end, text in lattice_pairs - neural_pairs
    ]
    source_clause_recall, overlap_recall = _grounding_recall(all_gold_pairs, union_grounded_pairs)
    length_by_case = {item["case_id"]: item["length_class"] for item in folds["cases"]}
    stratified_recall = {
        "wave": _grouped_exact_recall(evidence_rows, neural_pairs, lambda row: row["case_id"].split("-", 1)[0]),
        "length_class": _grouped_exact_recall(evidence_rows, neural_pairs, lambda row: length_by_case[row["case_id"]]),
        "authority_type": _grouped_exact_recall(evidence_rows, neural_pairs, lambda row: row["authority_evidence"]["type"]),
    }
    proposal_top30 = set()
    for case_id in traces:
        case_proposals = [item for item in all_outer_proposals if item.case_id == case_id][: protocol.proposal_recall_k]
        proposal_top30.update((case_id, item.action_span.clause_id, item.action_span.start, item.action_span.end, item.action_span.text) for item in case_proposals)
    proposal_recall = len(proposal_top30 & gold_set) / len(gold_set)
    ranker_values = ranking_metrics(
        [item["case_id"] for item in all_reranker_rows],
        [item["score"] for item in all_reranker_rows],
        [item["label"] for item in all_reranker_rows],
        expected_positive_count=len(gold_set),
    )
    selected_ranker = [item for item in all_reranker_rows if item["selected"]]
    ranker_values.update(
        prf(
            sum(item["label"] >= 0.8 for item in selected_ranker),
            len(selected_ranker),
            len(gold_set),
        )
    )
    ranker_by_seed = {
        seed: ranking_metrics(
            [item["case_id"] for item in ranker_rows_by_seed[seed]],
            [item["score"] for item in ranker_rows_by_seed[seed]],
            [item["label"] for item in ranker_rows_by_seed[seed]],
            expected_positive_count=len(gold_set),
        )
        for seed in protocol.training_seeds
    }
    seed_f1_values = [task_metrics_by_seed[seed]["global"]["task_identity_f1"] for seed in protocol.training_seeds]
    seed_summary = {
        "individual": {str(seed): task_metrics_by_seed[seed]["global"] for seed in protocol.training_seeds},
        "task_identity_f1_mean": statistics.mean(seed_f1_values),
        "task_identity_f1_std": statistics.pstdev(seed_f1_values),
    }
    atomic_write_json(output_root / "oof" / "span-predictions.json", [item.model_dump(mode="json") for item in all_outer_span_predictions])
    atomic_write_json(output_root / "oof" / "proposal-predictions.json", all_reranker_rows)
    atomic_write_json(output_root / "oof" / "challenger-outputs.json", challenger_outputs)
    atomic_write_json(output_root / "oof" / "provenance-sidecars.json", sidecars)
    atomic_write_json(output_root / "reports" / "span.json", {"token": span_token, "exact": span_exact, "per_seed": span_by_seed, "source_clause_recall": source_clause_recall, "overlap_recall_iou_0_5": overlap_recall, "stratified_exact_recall": stratified_recall, "source_recall": span_source_recall, "invalid_prediction_count": 0, "duplicate_prediction_count": 0, "out_of_clause_prediction_count": 0})
    atomic_write_json(output_root / "reports" / "proposal.json", {"recall_at_30": proposal_recall, "candidate_count": len(all_outer_proposals)})
    atomic_write_json(output_root / "reports" / "ranker.json", {**ranker_values, "per_seed": ranker_by_seed, "selected_count": len(all_selected)})
    atomic_write_json(output_root / "reports" / "task.json", {"global": task_metrics, "baseline_global": baseline_metrics, "per_fold": per_fold_task, "per_seed": task_metrics_by_seed, "seed_summary": seed_summary, "per_case_counts": per_case_counts})
    atomic_write_json(output_root / "reports" / "safety.json", safety.model_dump())
    model_inventory = [
        {
            "path": path.relative_to(repo_root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "model_config_hash": canonical_json_hash(run_signature),
        }
        for path in sorted(model_root.rglob("*"))
        if path.is_file()
    ]
    model_inventory_hash = atomic_write_json(
        output_root / "reports" / "model-artifacts.json", model_inventory
    )
    manifest = {"schema_version": "distillation-run-manifest-v1", "status": "complete", "signature": run_signature, "case_count": len(challenger_outputs), "fold_count": len(per_fold), "per_fold": per_fold, "model_inventory_sha256": model_inventory_hash}
    atomic_write_json(final_manifest_path, manifest)
    return manifest
