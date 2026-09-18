"""Inference safe cross clause proposal ranking for V2.19.

This module keeps the V2.17 occurrence to task bridge as the only decoder.  It
adds a small, deterministic feature layer over runtime trace data so a fold
trained ranker can prefer task identities with evidence elsewhere in a
meeting.  Gold occurrences are intentionally absent from every feature
builder; callers may use them only as fold local targets.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Any, Iterable, Sequence

from .protocol_bridge_v217 import Occurrence, _lexical_score, _normalise
from .protocol_bridge_v218 import RuntimeProposal, decode_proposals, runtime_proposals


FEATURE_DIMENSIONS = 768
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
DATE_RE = re.compile(
    r"(?:\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|\b\d{1,2}\s*(?:giờ|h|phút|ngày|tháng)\b|"
    r"\b(?:hôm nay|ngày mai|ngày kia|tuần này|tuần sau|thứ [2-8]|cuối tuần|sáng mai|chiều mai)\b)",
    re.IGNORECASE,
)
OWNER_TERMS = frozenset("tôi mình em anh chị bạn chúng tôi bên em bên anh phụ trách đầu mối owner assigned giao cho".split())
AUTHORITY_TERMS = frozenset("giao cần phải nhờ đồng ý đồng thuận chốt quyết định confirm xác nhận thống nhất duyệt được phép yêu cầu".split())
DEADLINE_TERMS = frozenset("hạn deadline trước vào hoàn thành xong".split())
ACTION_TERMS = frozenset("backup bắt đầu chạy code confirm cập nhật deploy đưa duy trì giám gửi hoàn hiển hỗ trợ integration kiểm lập làm merge monitor monitoring nhận phân rà regression review seed soạn sửa setup tập thiết theo tiếp test tích tối triển viết xử xong capacity container fix tài liệu".split())


def _tokens(value: Any) -> list[str]:
    return [match.group().casefold() for match in TOKEN_RE.finditer(str(value or ""))]


def _hash_index(value: str, dimensions: int = FEATURE_DIMENSIONS) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=4).digest(), "little") % dimensions


def _hashed_ngrams(text: str, dimensions: int = FEATURE_DIMENSIONS) -> dict[int, float]:
    tokens = _tokens(text)
    result: dict[int, float] = {}
    for size in (1, 2, 3):
        for start in range(max(0, len(tokens) - size + 1)):
            index = _hash_index("ng=" + " ".join(tokens[start : start + size]), dimensions)
            result[index] = result.get(index, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in result.values())) or 1.0
    return {index: value / norm for index, value in result.items()}


def _numeric_features(values: dict[str, float], dimensions: int = FEATURE_DIMENSIONS) -> dict[int, float]:
    return {_hash_index("num=" + key, dimensions): float(value) for key, value in values.items()}


def _clause_id(item: dict[str, Any]) -> str:
    return str(item.get("clause_id") or "")


def _speaker(item: dict[str, Any]) -> str:
    return str(item.get("speaker_name") or item.get("speaker_id") or item.get("speaker") or "UNKNOWN")


def _clause_order(item: dict[str, Any], fallback: int) -> int:
    try:
        return int(item.get("order_index", item.get("order", fallback)))
    except (TypeError, ValueError):
        return fallback


def _mention_kinds(text: str, speaker_names: Iterable[str] = ()) -> dict[str, bool]:
    value = text.casefold()
    tokens = set(_tokens(text))
    owner = bool(tokens & OWNER_TERMS) or any(name and name.casefold() in value for name in speaker_names)
    authority = bool(tokens & AUTHORITY_TERMS) or any(phrase in value for phrase in ("ok chốt", "đã thống nhất", "được rồi"))
    deadline = bool(DATE_RE.search(value)) or bool(tokens & DEADLINE_TERMS)
    action = bool(tokens & ACTION_TERMS)
    return {"owner": owner, "authority": authority, "deadline": deadline, "action": action}


def _events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in trace.get("events_after_deduplication", []) if isinstance(item, dict)]


def _event_for_proposal(trace: dict[str, Any], proposal: RuntimeProposal) -> dict[str, Any] | None:
    values = _events(trace)
    if proposal.event_id:
        exact = [item for item in values if str(item.get("event_id", "")) == proposal.event_id]
        if exact:
            return max(exact, key=lambda item: (float(item.get("confidence", 0.0) or 0.0), -int(item.get("order_index", 0) or 0)))
    linked = [item for item in values if proposal.occurrence.clause_id in {str(x) for x in item.get("source_clause_ids", [])}]
    if linked:
        return max(linked, key=lambda item: (float(item.get("confidence", 0.0) or 0.0), -int(item.get("order_index", 0) or 0)))
    return None


def _section(item: dict[str, Any]) -> str:
    for key in ("section", "section_id", "topic", "heading", "segment_id"):
        value = item.get(key)
        if value:
            return str(value)
    return "UNKNOWN"


def _proposal_cache_key(proposal: RuntimeProposal) -> tuple[Any, ...]:
    occurrence = proposal.occurrence
    return (
        occurrence.clause_id,
        occurrence.start,
        occurrence.end,
        occurrence.text,
        proposal.event_id,
        proposal.cluster_id,
        proposal.ranking_score,
        proposal.confidence,
        proposal.lexical_score,
        proposal.retrieval,
    )


def _pool_fingerprint(pool: Sequence[RuntimeProposal]) -> tuple[Any, ...]:
    if not pool:
        return (0,)
    return (len(pool), _proposal_cache_key(pool[0]), _proposal_cache_key(pool[-1]))


def _pool_rank_maps(trace: dict[str, Any], pool: Sequence[RuntimeProposal]) -> dict[str, dict[float, int]]:
    """Cache O(pool) rank lookups used by every candidate feature vector."""

    fingerprint = _pool_fingerprint(pool)
    cache = trace.setdefault("_v219_pool_rank_cache", {})
    cached = cache.get(fingerprint)
    if cached is not None:
        return cached
    values = {
        "quality": [item.quality for item in pool],
        "runtime_score": [item.ranking_score for item in pool],
        "confidence": [item.confidence for item in pool],
    }
    result = {
        name: {value: 1 + sum(other > value for other in numbers) for value in set(numbers)}
        for name, numbers in values.items()
    }
    cache[fingerprint] = result
    return result


def meeting_summary(trace: dict[str, Any]) -> dict[str, Any]:
    """Summarize clauses with no label or evidence access."""

    clauses = [item for item in trace.get("clauses", []) if isinstance(item, dict)]
    ordered = sorted(enumerate(clauses), key=lambda pair: (_clause_order(pair[1], pair[0]), _clause_id(pair[1])))
    names = sorted({_speaker(item) for _, item in ordered})
    kinds = [_mention_kinds(str(item.get("text_raw", "")), names) for _, item in ordered]
    return {
        "clauses": [item for _, item in ordered],
        "n_clauses": len(ordered),
        "n_speakers": len(names),
        "names": names,
        "kinds": kinds,
        "all_text": " ".join(str(item.get("text_raw", "")) for _, item in ordered),
        "action_count": sum(kind["action"] for kind in kinds),
        "owner_count": sum(kind["owner"] for kind in kinds),
        "authority_count": sum(kind["authority"] for kind in kinds),
        "deadline_count": sum(kind["deadline"] for kind in kinds),
    }


def cross_clause_features(
    trace: dict[str, Any], proposal: RuntimeProposal, *, summary: dict[str, Any] | None = None,
    pool: Sequence[RuntimeProposal] | None = None, max_repetition_clauses: int | None = None,
) -> dict[int, float]:
    """Build runtime-only evidence, repetition, and meeting-relative features."""

    summary = summary or meeting_summary(trace)
    clauses = summary["clauses"]
    occurrence = proposal.occurrence
    if pool is None:
        pool = runtime_proposals(trace, ranking_threshold=0.0, confidence_threshold=0.0, allow_lexical=True, lexical_threshold=0.22)
    pool = list(pool)
    feature_cache = trace.setdefault("_v219_feature_cache", {})
    cache_key = (_pool_fingerprint(pool), _proposal_cache_key(proposal), max_repetition_clauses)
    cached = feature_cache.get(cache_key)
    if cached is not None:
        return dict(cached)
    rank_maps = _pool_rank_maps(trace, pool)
    by_id = {_clause_id(item): item for item in clauses}
    candidate_clause = by_id.get(occurrence.clause_id, {})
    index = next((i for i, item in enumerate(clauses) if _clause_id(item) == occurrence.clause_id), 0)
    kinds = summary["kinds"]
    kind = kinds[index] if index < len(kinds) else _mention_kinds(occurrence.text, summary["names"])
    event = _event_for_proposal(trace, proposal)
    event_sources = {str(x) for x in (event or {}).get("source_clause_ids", []) if x}
    support_ids = event_sources or {occurrence.clause_id}
    support_positions = [i for i, item in enumerate(clauses) if _clause_id(item) in support_ids]
    mention_positions = {name: [i for i, value in enumerate(kinds) if value[name]] for name in ("owner", "authority", "deadline")}
    distances = {name: min((abs(index - pos) for pos in positions), default=len(clauses)) / max(1, len(clauses)) for name, positions in mention_positions.items()}
    action_text = str((event or {}).get("action_text") or occurrence.text)
    repetition_cache = trace.setdefault("_v219_repetition_cache", {})
    repetition_key = (action_text, occurrence.clause_id, max_repetition_clauses)
    repetition = repetition_cache.get(repetition_key)
    if repetition is None:
        repetition_clauses = [(position, item) for position, item in enumerate(clauses) if _clause_id(item) != occurrence.clause_id]
        if max_repetition_clauses is not None:
            repetition_clauses.sort(key=lambda pair: (abs(pair[0] - index), pair[0]))
            repetition_clauses = repetition_clauses[: max(0, int(max_repetition_clauses))]
        repetition = [_lexical_score(action_text, str(item.get("text_raw", ""))) for _position, item in repetition_clauses]
        repetition_cache[repetition_key] = repetition
    repeated = [score for score in repetition if score >= 0.55]
    paraphrased = [score for score in repetition if score >= 0.35]
    rank_by_quality = rank_maps["quality"][proposal.quality]
    rank_by_score = rank_maps["runtime_score"][proposal.ranking_score]
    rank_by_conf = rank_maps["confidence"][proposal.confidence]
    order = _clause_order(candidate_clause, index)
    values: dict[str, float] = {
        "ranking_score": max(0.0, min(1.0, proposal.ranking_score)),
        "event_confidence": max(0.0, min(1.0, proposal.confidence)),
        "lexical_retrieval_score": max(0.0, min(1.0, proposal.lexical_score)),
        "direct_event": float(proposal.retrieval == "direct_event"),
        "has_event": float(event is not None),
        "event_source_count": min(len(event_sources), 8) / 8.0,
        "support_clause_count": min(len(support_ids), 8) / 8.0,
        "support_has_other_clause": float(any(cid != occurrence.clause_id for cid in support_ids)),
        "owner_support_anywhere": float(bool((event or {}).get("assignee")) or summary["owner_count"] > 0),
        "authority_support_anywhere": float(summary["authority_count"] > 0),
        "deadline_support_anywhere": float(bool((event or {}).get("deadline_mention_id")) or summary["deadline_count"] > 0),
        "owner_distance": distances["owner"],
        "authority_distance": distances["authority"],
        "deadline_distance": distances["deadline"],
        "owner_before": sum(pos < index for pos in mention_positions["owner"]) / max(1, len(clauses)),
        "authority_before": sum(pos < index for pos in mention_positions["authority"]) / max(1, len(clauses)),
        "deadline_after": sum(pos > index for pos in mention_positions["deadline"]) / max(1, len(clauses)),
        "same_clause_owner": float(kind["owner"]),
        "same_clause_authority": float(kind["authority"]),
        "same_clause_deadline": float(kind["deadline"]),
        "same_clause_action": float(kind["action"]),
        "repetition_max": max(repetition, default=0.0),
        "repetition_count": min(len(repeated), 8) / 8.0,
        "paraphrase_max": max(paraphrased, default=0.0),
        "paraphrase_count": min(len(paraphrased), 8) / 8.0,
        "rank_quality": 1.0 / rank_by_quality,
        "rank_runtime_score": 1.0 / rank_by_score,
        "rank_confidence": 1.0 / rank_by_conf,
        "pool_size": min(len(pool), 32) / 32.0,
        "candidate_density": len(pool) / max(1, len(clauses)),
        "order_fraction": order / max(1, summary["n_clauses"] - 1),
        "is_first": float(index == 0),
        "is_last": float(index == len(clauses) - 1),
        "meeting_clause_count": min(summary["n_clauses"], 100) / 100.0,
        "meeting_speaker_count": min(summary["n_speakers"], 20) / 20.0,
        "meeting_action_density": summary["action_count"] / max(1, len(clauses)),
        "meeting_owner_density": summary["owner_count"] / max(1, len(clauses)),
        "meeting_authority_density": summary["authority_count"] / max(1, len(clauses)),
        "meeting_deadline_density": summary["deadline_count"] / max(1, len(clauses)),
        "support_nearest_distance": min((abs(index - pos) for pos in support_positions), default=len(clauses)) / max(1, len(clauses)),
    }
    flags = set(str(x) for x in (trace.get("annotations", {}).get(occurrence.clause_id, {}) or {}).get("flags", []))
    flags.update(str(x) for x in occurrence.negative_signals)
    for flag in sorted(flags):
        values["flag=" + flag] = 1.0
    values["speaker=" + _speaker(candidate_clause).casefold()] = 1.0
    values["section=" + _section(candidate_clause).casefold()] = 1.0
    result = _numeric_features(values)
    for key, amount in _hashed_ngrams(action_text).items():
        result[key] = result.get(key, 0.0) + amount
    feature_cache[cache_key] = dict(result)
    return result


@dataclass
class CrossClauseLogisticRanker:
    """Small deterministic group aware logistic scorer."""

    dimensions: int = FEATURE_DIMENSIONS
    l2: float = 0.03
    weights: list[float] | None = None
    bias: float = 0.0

    def __post_init__(self) -> None:
        if self.weights is None:
            self.weights = [0.0] * self.dimensions

    def fit(self, groups: Sequence[Sequence[tuple[dict[int, float], int]]], *, epochs: int = 22, learning_rate: float = 0.20) -> "CrossClauseLogisticRanker":
        self.weights = [0.0] * self.dimensions
        self.bias = 0.0
        for _ in range(epochs):
            for group in groups:
                if not group:
                    continue
                positives = sum(int(target) for _, target in group)
                negatives = len(group) - positives
                if not positives or not negatives:
                    continue
                positive_weight = min(4.0, negatives / max(1, positives))
                grad = [0.0] * self.dimensions
                bias_grad = 0.0
                total = 0.0
                for features, target in group:
                    logit = self.logit(features)
                    probability = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, logit))))
                    weight = positive_weight if int(target) else 1.0
                    error = (probability - float(target)) * weight
                    bias_grad += error
                    for index, value in features.items():
                        grad[index] += error * value
                    total += weight
                scale = 1.0 / max(1.0, total)
                for index in range(self.dimensions):
                    self.weights[index] -= learning_rate * (grad[index] * scale + self.l2 * self.weights[index])
                self.bias -= learning_rate * bias_grad * scale
        return self

    def logit(self, features: dict[int, float]) -> float:
        assert self.weights is not None
        return self.bias + sum(self.weights[index] * value for index, value in features.items())

    def probability(self, features: dict[int, float]) -> float:
        value = max(-40.0, min(40.0, self.logit(features)))
        return 1.0 / (1.0 + math.exp(-value))


def rank_runtime_proposals(
    trace: dict[str, Any], ranker: CrossClauseLogisticRanker | None = None, *, pool: Sequence[RuntimeProposal] | None = None,
) -> list[tuple[RuntimeProposal, float]]:
    """Return runtime proposals sorted by learned score and stable tie breaks."""

    values = list(pool) if pool is not None else runtime_proposals(trace, ranking_threshold=0.0, confidence_threshold=0.0, allow_lexical=True, lexical_threshold=0.22)
    summary = meeting_summary(trace)
    scorer = ranker or CrossClauseLogisticRanker()
    ranked = [(proposal, scorer.logit(cross_clause_features(trace, proposal, summary=summary, pool=values))) for proposal in values]
    return sorted(ranked, key=lambda item: (-item[1], -item[0].quality, item[0].occurrence.clause_id, item[0].occurrence.start, item[0].occurrence.end))


def decode_ranked_proposals(
    trace: dict[str, Any], ranked: Sequence[tuple[RuntimeProposal, float]], *, meeting_date: str,
    score_threshold: float = float("-inf"), meeting_budget: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply a learned score/budget and pass the result through V2.17."""

    selected = [proposal for proposal, score in ranked if score >= score_threshold]
    if meeting_budget is not None:
        selected = selected[: max(0, int(meeting_budget))]
    return decode_proposals(trace, selected, meeting_date=meeting_date)


__all__ = [
    "CrossClauseLogisticRanker", "FEATURE_DIMENSIONS", "RuntimeProposal", "cross_clause_features",
    "decode_proposals", "decode_ranked_proposals", "meeting_summary", "rank_runtime_proposals", "runtime_proposals",
]
