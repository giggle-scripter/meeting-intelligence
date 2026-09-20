"""XLM-R proposal cross-encoder and locked BCE/pairwise objective."""

from __future__ import annotations

from typing import Any

import torch
from torch import nn
from torch.nn import functional as F


class ProposalReranker(nn.Module):
    def __init__(self, backbone: nn.Module, hidden_size: int) -> None:
        super().__init__()
        self.backbone = backbone
        self.classifier = nn.Linear(hidden_size, 1)

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None) -> torch.Tensor:
        output = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
        return self.classifier(output.last_hidden_state[:, 0]).squeeze(-1)


def load_reranker(model_name: str) -> tuple[ProposalReranker, Any]:
    try:
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
        backbone = AutoModel.from_pretrained(model_name)
    except Exception as error:
        raise RuntimeError("STOP_MODEL_UNAVAILABLE") from error
    return ProposalReranker(backbone, int(backbone.config.hidden_size)), tokenizer


def capped_pos_weight(labels: torch.Tensor, cap: float = 20.0) -> torch.Tensor:
    positives = labels.sum().clamp_min(1.0)
    negatives = (labels.numel() - labels.sum()).clamp_min(1.0)
    return torch.clamp(negatives / positives, max=cap)


def reranker_loss(
    logits: torch.Tensor,
    labels: torch.Tensor,
    sample_weights: torch.Tensor,
    pos_weight: torch.Tensor,
    hard_pairs: list[tuple[int, int]],
) -> torch.Tensor:
    bce = F.binary_cross_entropy_with_logits(logits, labels.float(), pos_weight=pos_weight, reduction="none")
    main = (bce * sample_weights).sum() / sample_weights.sum().clamp_min(1e-8)
    if not hard_pairs:
        return main
    probabilities = torch.sigmoid(logits)
    pair_losses = [torch.clamp(0.20 - probabilities[positive] + probabilities[negative], min=0.0) for positive, negative in hard_pairs]
    return main + 0.20 * torch.stack(pair_losses).mean()
