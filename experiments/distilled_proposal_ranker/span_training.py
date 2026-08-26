"""Deterministic local training loop and checkpoint manifest utilities."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import torch
from torch.optim import AdamW
from torch.optim.lr_scheduler import LambdaLR
from torch.utils.data import DataLoader, Dataset

from .contracts import SpanExample
from .hashing import atomic_write_json, sha256_file
from .metrics import token_prf
from .span_dataset import bio_labels
from .span_model import ActionSpanModel, capped_token_class_weights, combined_span_loss, resource_settings, set_deterministic_seed


class EncodedSpanDataset(Dataset):
    def __init__(self, examples: list[SpanExample], tokenizer: Any, max_length: int) -> None:
        self.rows = []
        for example in examples:
            encoded = tokenizer(
                example.context,
                truncation=True,
                max_length=max_length,
                return_offsets_mapping=True,
                padding="max_length",
            )
            labels = bio_labels([tuple(item) for item in encoded.pop("offset_mapping")], example)
            self.rows.append(
                {
                    "input_ids": torch.tensor(encoded["input_ids"], dtype=torch.long),
                    "attention_mask": torch.tensor(encoded["attention_mask"], dtype=torch.long),
                    "labels": torch.tensor(labels, dtype=torch.long),
                    "clause_label": torch.tensor(bool(example.gold_spans), dtype=torch.float32),
                    "weight": torch.tensor(example.weight, dtype=torch.float32),
                }
            )

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        return self.rows[index]


@dataclass(frozen=True)
class SpanTrainingResult:
    epoch: int
    token_f1: float
    exact_recall: float
    checkpoint_hash: str
    manifest_path: str


def _evaluate(model: ActionSpanModel, loader: DataLoader, device: torch.device) -> tuple[float, float]:
    model.eval()
    predictions: list[list[int]] = []
    labels: list[list[int]] = []
    exact = total_positive = 0
    with torch.no_grad():
        for batch in loader:
            token_logits, _ = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
            predicted = token_logits.argmax(dim=-1).cpu()
            gold = batch["labels"]
            predictions.extend(predicted.tolist())
            labels.extend(gold.tolist())
            for left, right in zip(predicted.tolist(), gold.tolist(), strict=True):
                cleaned_left = [a for a, b in zip(left, right, strict=True) if b != -100]
                cleaned_right = [b for b in right if b != -100]
                if any(item in (1, 2) for item in cleaned_right):
                    total_positive += 1
                    exact += int(cleaned_left == cleaned_right)
    return token_prf(predictions, labels)["f1"], exact / total_positive if total_positive else 0.0


def train_span_model(
    model: ActionSpanModel,
    tokenizer: Any,
    train_examples: list[SpanExample],
    valid_examples: list[SpanExample],
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
) -> SpanTrainingResult:
    set_deterministic_seed(seed)
    settings = resource_settings()
    device = torch.device(settings.device)
    model.to(device)
    train_dataset = EncodedSpanDataset(train_examples, tokenizer, max_length)
    valid_dataset = EncodedSpanDataset(valid_examples, tokenizer, max_length)
    generator = torch.Generator().manual_seed(seed)
    train_loader = DataLoader(train_dataset, batch_size=settings.train_batch_size, shuffle=True, generator=generator)
    valid_loader = DataLoader(valid_dataset, batch_size=settings.eval_batch_size)
    all_labels = torch.cat([row["labels"] for row in train_dataset.rows])
    token_weights = capped_token_class_weights(all_labels).to(device)
    optimizer = AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    update_steps_per_epoch = max(1, math.ceil(len(train_loader) / settings.gradient_accumulation_steps))
    total_steps = update_steps_per_epoch * max_epochs
    warmup_steps = int(total_steps * warmup_ratio)

    def schedule(step: int) -> float:
        if warmup_steps and step < warmup_steps:
            return float(step + 1) / warmup_steps
        return max(0.0, float(total_steps - step) / max(1, total_steps - warmup_steps))

    scheduler = LambdaLR(optimizer, schedule)
    scaler = torch.amp.GradScaler("cuda", enabled=settings.fp16)
    best: tuple[float, float, int] | None = None
    best_state: dict[str, torch.Tensor] | None = None
    stale = 0
    optimizer.zero_grad(set_to_none=True)
    for epoch in range(1, max_epochs + 1):
        model.train()
        for step, batch in enumerate(train_loader, start=1):
            with torch.autocast(device_type=settings.device, enabled=settings.fp16):
                token_logits, clause_logits = model(batch["input_ids"].to(device), batch["attention_mask"].to(device))
                loss = combined_span_loss(
                    token_logits,
                    clause_logits,
                    batch["labels"].to(device),
                    batch["clause_label"].to(device),
                    batch["weight"].to(device),
                    token_weights,
                ) / settings.gradient_accumulation_steps
            scaler.scale(loss).backward()
            if step % settings.gradient_accumulation_steps == 0 or step == len(train_loader):
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), gradient_clip_norm)
                scaler.step(optimizer)
                scaler.update()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
        token_f1, exact_recall = _evaluate(model, valid_loader, device)
        score = (token_f1, exact_recall, -epoch)
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
    epoch = -best[2]
    manifest = {
        "schema_version": "span-checkpoint-manifest-v1",
        **manifest_values,
        "seed": seed,
        "learning_rate": learning_rate,
        "epoch": epoch,
        "token_f1": best[0],
        "exact_recall": best[1],
        "checkpoint_hash": checkpoint_hash,
        "device": settings.device,
    }
    manifest_path = checkpoint_dir / "manifest.json"
    atomic_write_json(manifest_path, manifest)
    return SpanTrainingResult(epoch, best[0], best[1], checkpoint_hash, str(manifest_path))
