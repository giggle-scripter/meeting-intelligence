"""XLM-R dual-head BIO and clause-level action span student."""

from __future__ import annotations

from dataclasses import dataclass
import os
import random
from typing import Any

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


def set_deterministic_seed(seed: int) -> None:
    os.environ["PYTHONHASHSEED"] = str(seed)
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False


class ActionSpanModel(nn.Module):
    def __init__(self, backbone: nn.Module, hidden_size: int) -> None:
        super().__init__()
        self.backbone = backbone
        self.token_classifier = nn.Linear(hidden_size, 3)
        self.clause_classifier = nn.Linear(hidden_size, 1)

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None) -> tuple[torch.Tensor, torch.Tensor]:
        outputs = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
        hidden = outputs.last_hidden_state
        token_logits = self.token_classifier(hidden)
        clause_logits = self.clause_classifier(hidden[:, 0]).squeeze(-1)
        return token_logits, clause_logits


def load_span_model(model_name: str) -> tuple[ActionSpanModel, Any]:
    try:
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
        if not tokenizer.is_fast:
            raise RuntimeError("span model requires a fast tokenizer")
        backbone = AutoModel.from_pretrained(model_name)
    except Exception as error:
        raise RuntimeError("STOP_MODEL_UNAVAILABLE") from error
    hidden_size = int(backbone.config.hidden_size)
    return ActionSpanModel(backbone, hidden_size), tokenizer


def capped_token_class_weights(labels: torch.Tensor, cap: float = 20.0) -> torch.Tensor:
    valid = labels[labels >= 0]
    counts = torch.bincount(valid, minlength=3).to(dtype=torch.float32)
    base = counts[0].clamp_min(1.0)
    weights = torch.ones(3, dtype=torch.float32, device=labels.device)
    for index in (1, 2):
        weights[index] = torch.clamp(base / counts[index].clamp_min(1.0), max=cap)
    return weights


def combined_span_loss(
    token_logits: torch.Tensor,
    clause_logits: torch.Tensor,
    token_labels: torch.Tensor,
    clause_labels: torch.Tensor,
    example_weights: torch.Tensor,
    token_class_weights: torch.Tensor,
) -> torch.Tensor:
    token_loss = F.cross_entropy(
        token_logits.transpose(1, 2),
        token_labels,
        weight=token_class_weights,
        ignore_index=-100,
        reduction="none",
    )
    valid = token_labels.ne(-100)
    per_example_token = (token_loss * valid).sum(dim=1) / valid.sum(dim=1).clamp_min(1)
    clause_loss = F.binary_cross_entropy_with_logits(clause_logits, clause_labels.float(), reduction="none")
    return ((per_example_token + 0.30 * clause_loss) * example_weights).sum() / example_weights.sum().clamp_min(1e-8)


@dataclass(frozen=True)
class ResourceSettings:
    device: str
    train_batch_size: int
    eval_batch_size: int
    gradient_accumulation_steps: int
    fp16: bool


def resource_settings() -> ResourceSettings:
    if torch.cuda.is_available():
        fp16 = bool(torch.cuda.is_bf16_supported() or torch.cuda.get_device_capability()[0] >= 7)
        return ResourceSettings("cuda", 16, 32, 1, fp16)
    return ResourceSettings("cpu", 4, 8, 4, False)
