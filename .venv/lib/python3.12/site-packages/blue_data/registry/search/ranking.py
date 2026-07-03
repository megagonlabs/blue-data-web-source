from __future__ import annotations

from blue_data.core.utils.similarity_utils import normalize_bm25_scores  # noqa: F401 — re-exported


def combine_scores(
    bm25_weight: float,
    bm25_score: float,
    vector_weight: float,
    vector_score: float,
    value_weight: float = 0.0,
    value_score: float = 0.0,
) -> float:
    """Weighted combination of BM25, vector, and value-relevance scores."""
    return (
        bm25_weight * bm25_score
        + vector_weight * vector_score
        + value_weight * value_score
    )


def normalize_weights(
    bm25_weight: float,
    vector_weight: float,
    value_weight: float = 0.0,
) -> tuple[float, float, float]:
    """Normalize weights so they sum to 1.0."""
    if bm25_weight < 0 or vector_weight < 0 or value_weight < 0:
        raise ValueError("Weights must be non-negative")
    total = bm25_weight + vector_weight + value_weight
    if total == 0:
        return bm25_weight, vector_weight, value_weight
    return bm25_weight / total, vector_weight / total, value_weight / total


def aggregate_value_scores(
    node_ids: set,
    hierarchy: dict,
    mode: str = "topk",
    top_k: int = 3,
    decay: float = 0.9,
) -> float:
    """Aggregate value-relevance scores across a set of hierarchy nodes.

    mode options: 'max', 'sum', 'topk', 'decayed'
    """
    scores = []
    for node_id in node_ids:
        if node_id not in hierarchy:
            continue
        record = hierarchy[node_id]["record"]
        s = max(
            getattr(record, "value_relevance_score", 0.0),
            getattr(record, "aggregated_value_relevance", 0.0),
        )
        if s > 0:
            scores.append(min(1.0, s))

    if not scores:
        return 0.0

    if mode == "max":
        return max(scores)
    if mode == "sum":
        return sum(scores)
    if mode == "topk":
        scores.sort(reverse=True)
        k = min(top_k, len(scores))
        return sum(scores[:k]) / k
    if mode == "decayed":
        scores.sort(reverse=True)
        return sum(decay ** i * s for i, s in enumerate(scores))

    return max(scores)


def find_best_score_among_nodes(
    node_ids: set,
    hierarchy: dict,
    params: dict,
) -> tuple:
    """Find the best-scoring node in a set.

    Returns (best_inverted_score, best_record, best_values, best_value_score).
    best_inverted_score: lower is better.
    """
    best_score = 1.0
    best_record = None
    best_values: list = []
    best_value_score = 0.0

    for node_id in node_ids:
        if node_id not in hierarchy:
            continue

        record = hierarchy[node_id]["record"]
        norm_bm25 = getattr(record, "normalized_bm25_score", 0.0)
        vector_score = getattr(record, "vector_score", 0.0)
        value_norm = min(1.0, max(
            getattr(record, "value_relevance_score", 0.0),
            getattr(record, "aggregated_value_relevance", 0.0),
        ))
        value_ok = value_norm >= params.get("value_threshold", 0.0)

        combined = combine_scores(
            params["bm25_weight"], norm_bm25,
            params["vector_weight"], vector_score,
            params["value_weight"], value_norm,
        )
        inverted = 1.0 - combined

        if not value_ok and (
            norm_bm25 < params["bm25_threshold"]
            or vector_score < params["vector_threshold"]
        ):
            continue

        if inverted > (1.0 - params["combined_threshold"]):
            continue

        better = (
            best_record is None
            or inverted < best_score
            or (inverted == best_score and value_norm > best_value_score)
        )
        if better:
            best_score = inverted
            best_record = record
            best_values = getattr(record, "most_relevant_values", [])
            best_value_score = value_norm

    return best_score, best_record, best_values, best_value_score
