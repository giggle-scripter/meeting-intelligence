"""Deterministic proposal-reranker training with inner PR-AUC early stopping."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import torch
from torch.optim import AdamW
from torch.optim.lr_scheduler import LambdaLR
from torch.utils.data import DataLoader, Dataset

from .contracts import RerankerExample
from .hashing import atomic_write_json, sha256_file
from .metrics import ranking_metrics
from .reranker_model import ProposalReranker, capped_pos_weight, reranker_loss
from .span_model import resource_settings, set_deterministic_seed


class EncodedRerankerDataset(Dataset):
    def __init__(self, examples: list[RerankerExample], tokenizer: Any, max_length: int) -> None:
        self.examples = examples
        self.index_by_id = {item.proposal_id: index for index, item in enumerate(examples)}
        self.rows = []
        for item in examples:
            encoded = tokenizer(item.serialized_input, truncation=True, max_length=max_length, padding="max_length")
            self.rows.append({
                "input_ids": torch.tensor(encoded["input_ids"], dtype=torch.long),
                "attention_mask": torch.tensor(encoded["attention_mask"], dtype=torch.long),
                "label": torch.tensor(item.label, dtype=torch.float32),
                "weight": torch.tensor(item.sample_weight, dtype=torch.float32),
            })

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        row = dict(self.rows[index])
        row["index"] = torch.tensor(index)
        return row


@dataclass(frozen=True)
class RerankerTrainingResult:
    epoch: int
    pr_auc: float
    recall_at_30: float
    checkpoint_hash: str
    manifest_path: str


def _evaluate(model: ProposalReranker, dataset: EncodedRerankerDataset, loader: DataLoader, device: torch.device, expected_positive_count: int | None) -> tuple[float, float]:
    model.eval()
    logits = []
    labels = []
    with torch.no_grad():
        for batch in loader:
            logits.extend(model(batch["input_ids"].to(device), batch["attention_mask"].to(device)).cpu().tolist())
            labels.extend(batch["label"].tolist())
    probabilities = torch.sigmoid(torch.tensor(logits)).tolist()
    values = ranking_metrics(
        [item.case_id for item in dataset.examples], probabilities, labels,
        expected_positive_count=expected_positive_count,
    )
    return float(values["pr_auc"] or 0.0), float(values["recall_at_30"] or 0.0)


def train_reranker(
    model: ProposalReranker,
    tokenizer: Any,
    train_examples: list[RerankerExample],
    valid_examples: list[RerankerExample],
    *,
    seed: int,
    learning_rate: float,
    weight_decay: float,
    warmup_ratio: float,
    max_epochs: int,
    patience: int,
    gradient_clip_norm: float,
    max_length: int,
    checkpoint_dir: Path,
    manifest_values: dict[str, Any],
    select_best_checkpoint: bool = True,
    valid_expected_positive_count: int | None = None,
) -> RerankerTrainingResult:
    set_deterministic_seed(seed)
    settings = resource_settings()
    device = torch.device(settings.device)
    model.to(device)
    train_data = EncodedRerankerDataset(train_examples, tokenizer, max_length)
    valid_data = EncodedRerankerDataset(valid_examples, tokenizer, max_length)
    generator = torch.Generator().manual_seed(seed)
    train_loader = DataLoader(train_data, batch_size=settings.train_batch_size, shuffle=True, generator=generator)
    valid_loader = DataLoader(valid_data, batch_size=settings.eval_batch_size)
    labels = torch.tensor([item.label for item in train_examples], dtype=torch.float32)
    pos_weight = capped_pos_weight(labels).to(device)
    optimizer = AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    total_steps = max(1, math.ceil(len(train_loader) / settings.gradient_accumulation_steps) * max_epochs)
    warmup_steps = int(total_steps * warmup_ratio)
    scheduler = LambdaLR(optimizer, lambda step: (step + 1) / warmup_steps if warmup_steps and step < warmup_steps else max(0.0, (total_steps - step) / max(1, total_steps - warmup_steps)))
    scaler = torch.amp.GradScaler("cuda", enabled=settings.fp16)
    best = None
    best_state = None
    stale = 0
    optimizer.zero_grad(set_to_none=True)
    for epoch in range(1, max_epochs + 1):
        model.train()
        for step, batch in enumerate(train_loader, start=1):
            with torch.autocast(device_type=settings.device, enabled=settings.fp16):
                logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
                local_indices = batch["index"].tolist()
                local_by_global = {global_index: local for local, global_index in enumerate(local_indices)}
                pairs = []
                for local, global_index in enumerate(local_indices):
                    for negative_id in train_examples[global_index].paired_negative_ids:
                        negative_global = train_data.index_by_id.get(negative_id)
                        if negative_global in local_by_global:
                            pairs.append((local, local_by_global[negative_global]))
                loss = reranker_loss(logits, batch["label"].to(device), batch["weight"].to(device), pos_weight, pairs) / settings.gradient_accumulation_steps
            scaler.scale(loss).backward()
            if step % settings.gradient_accumulation_steps == 0 or step == len(train_loader):
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip_norm)
                scaler.step(optimizer)
                scaler.update()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
        pr_auc, recall = _evaluate(model, valid_data, valid_loader, device, valid_expected_positive_count)
        score = (pr_auc, recall, -epoch)
        if not select_best_checkpoint:
            best = score
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            continue
        if best is None or score > best:
            best = score
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= patience:
                break
    assert best is not None and best_state is not None
    model.load_state_dict(best_state)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    weights_path = checkpoint_dir / "model.pt"
    torch.save(best_state, weights_path)
    checkpoint_hash = sha256_file(weights_path)
    manifest = {"schema_version": "reranker-checkpoint-manifest-v1", **manifest_values, "seed": seed, "learning_rate": learning_rate, "epoch": -best[2], "pr_auc": best[0], "recall_at_30": best[1], "checkpoint_hash": checkpoint_hash, "device": settings.device, "selection": "inner_validation" if select_best_checkpoint else "fixed_epoch", "requested_max_epochs": max_epochs, "status": "complete"}
    manifest_path = checkpoint_dir / "manifest.json"
    atomic_write_json(manifest_path, manifest)
    return RerankerTrainingResult(-best[2], best[0], best[1], checkpoint_hash, str(manifest_path))


def reuse_reranker_checkpoint(
    model: ProposalReranker,
    checkpoint_dir: Path,
    required_manifest_values: dict[str, Any],
) -> RerankerTrainingResult | None:
    manifest_path = checkpoint_dir / "manifest.json"
    weights_path = checkpoint_dir / "model.pt"
    if not manifest_path.exists() or not weights_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get("status") != "complete" or any(
        manifest.get(key) != value for key, value in required_manifest_values.items()
    ):
        return None
    checkpoint_hash = sha256_file(weights_path)
    if checkpoint_hash != manifest.get("checkpoint_hash"):
        return None
    model.load_state_dict(torch.load(weights_path, map_location="cpu", weights_only=True))
    return RerankerTrainingResult(
        int(manifest["epoch"]),
        float(manifest["pr_auc"]),
        float(manifest["recall_at_30"]),
        checkpoint_hash,
        str(manifest_path),
    )


def predict_reranker_logits(
    model: ProposalReranker,
    tokenizer: Any,
    examples: list[RerankerExample],
    *,
    max_length: int,
) -> list[float]:
    settings = resource_settings()
    device = torch.device(settings.device)
    model.to(device)
    dataset = EncodedRerankerDataset(examples, tokenizer, max_length)
    loader = DataLoader(dataset, batch_size=settings.eval_batch_size)
    logits: list[float] = []
    model.eval()
    with torch.no_grad():
        for batch in loader:
            logits.extend(
                model(batch["input_ids"].to(device), batch["attention_mask"].to(device)).cpu().tolist()
            )
    return logits
