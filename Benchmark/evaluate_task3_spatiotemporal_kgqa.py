"""Evaluate Task 3: Spatiotemporal KGQA.

This script evaluates returned answers, not just SPARQL strings. It can compare
system answers against the benchmark's gold ``answer`` field, or execute gold
and predicted SPARQL queries against a local RDF graph/SPARQL endpoint.

Prediction file examples:

Answers:
  {"1000000": {"answers": [{"county": "gokg:MiamiDadeCountyFL", "maxOutages": 1777800}]}}

Queries:
  {"1000000": {"sparql_query": "SELECT ..."}}
  {"1000002": {"sparql_interpretations": [{"label": "...", "sparql_query": "SELECT ..."}]}}

When multiple predicted query interpretations are present, the script executes
each independently and reports the best scoring predicted/gold interpretation
pair for the question.

GraphDB execution:
  python evaluate_task3_spatiotemporal_kgqa.py \
    --predictions eval_outputs/nl2sparql_predictions_with_allowed.json \
    --prediction-kind queries \
    --gold-from-execution \
    --graphdb-url http://localhost:7200 \
    --graphdb-repository geooutage
"""

from __future__ import annotations

import argparse
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geooutage_eval_utils import (
    canonicalize_value,
    extract_spatial_constraints,
    extract_temporal_constraints,
    flatten_answer_rows,
    get_gold_interpretations,
    get_predicted_query_records,
    get_predicted_answers,
    get_predicted_queries,
    get_prediction_map,
    harmonic_mean,
    load_benchmark,
    macro_average,
    precision_recall_f1,
    query_endpoint,
    query_rdflib_graph,
    safe_mean,
    summarize_by,
    write_csv,
    write_json,
)


DEFAULT_BENCHMARK = Path(__file__).with_name("geooutagebench_questions.json")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("eval_outputs") / "task3_spatiotemporal_kgqa"
DEFAULT_GRAPHDB_URL = "http://localhost:7200"

# When multiple predicted and gold query interpretations are executed, this
# macro score chooses the answer-set pair that best represents the question.
ANSWER_PAIR_SELECTION_KEYS = [
    "answer_f1",
    "spatial_correctness",
    "temporal_correctness",
    "spatiotemporal_relevance_score",
]

# GraphDB/SPARQL JSON returns full IRIs. These prefixes align endpoint output
# with the compact qnames used in the benchmark answer JSON.
IRI_PREFIXES = {
    "https://ucf-henat.github.io/GeoOutageOnto/#": "goo:",
    "http://example.org/resource#": "gokg:",
    "http://www.w3.org/2001/XMLSchema#": "xsd:",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf:",
    "http://www.w3.org/2000/01/rdf-schema#": "rdfs:",
    "http://www.opengis.net/ont/geosparql#": "geosparql:",
    "http://www.opengis.net/def/function/geosparql/": "geof:",
}
TYPED_LITERAL_PATTERN = re.compile(r'^"?(.+?)"?\^\^(?:xsd:|<http://www\.w3\.org/2001/XMLSchema#>)([A-Za-z_][\w-]*)$')
LANG_LITERAL_PATTERN = re.compile(r'^"?(.+?)"?@[A-Za-z-]+$')
NUMERIC_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")
DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$")
ANSWER_COLUMN_ALIASES = {
    "image": "image",
    "ntl": "image",
    "ntlimage": "image",
    "map": "map",
    "outagemap": "map",
    "rec": "record",
    "record": "record",
    "event": "event",
    "county": "county",
    "state": "state",
    "date": "date",
    "recorddate": "date",
    "recorddatetime": "date",
    "acquisitiondate": "date",
    "acquisitiondatetime": "date",
    "num": "count",
    "numoutages": "count",
    "maxoutages": "count",
    "maxnumoutages": "count",
    "totaloutages": "count",
}
CORE_MATCH_COLUMNS = {"image", "map", "record", "event", "county", "state", "date", "count"}
SPATIAL_ANNOTATION_KEYS = (
    "spatial_correctness",
    "spatial_score",
    "annotated_spatial_correctness",
    "annotated_spatial",
)
TEMPORAL_ANNOTATION_KEYS = (
    "temporal_correctness",
    "temporal_score",
    "annotated_temporal_correctness",
    "annotated_temporal",
)


def build_graphdb_endpoint(base_url: str, repository: str) -> str:
    """Build the standard GraphDB repository SPARQL endpoint URL."""
    return f"{base_url.rstrip('/')}/repositories/{repository.strip('/')}"


def requires_spatial(question: dict[str, Any]) -> bool:
    """Infer whether spatial correctness should affect the relevance score."""
    axis = str(question.get("spatiotemporal_axis", "")).lower()
    categories = " ".join(
        [str(question.get("primary_category", ""))]
        + [str(item) for item in question.get("secondary_categories", [])]
    ).lower()
    return axis in {"spatial", "spatiotemporal"} or "spatial" in categories


def requires_temporal(question: dict[str, Any]) -> bool:
    """Infer whether temporal correctness should affect the relevance score."""
    axis = str(question.get("spatiotemporal_axis", "")).lower()
    categories = " ".join(
        [str(question.get("primary_category", ""))]
        + [str(item) for item in question.get("secondary_categories", [])]
    ).lower()
    return axis in {"temporal", "spatiotemporal"} or "event sequence" in categories or "temporal" in categories


def execute_queries(
    queries: list[str],
    endpoint: str | None,
    rdf_files: list[str],
    timeout: int,
    endpoint_user: str | None = None,
    endpoint_password: str | None = None,
) -> tuple[list[Any], str | None]:
    """Execute a list of SPARQL queries and concatenate their result rows.

    This helper is used for legacy single-query behavior. The interpretation
    aware path calls ``execute_query_records`` so it can preserve per-query
    metadata and select the best answer set later.
    """
    if not endpoint and not rdf_files:
        return [], "query execution requested but no --endpoint or --rdf-file was supplied"
    rows = []
    errors = []
    for query in queries:
        try:
            if endpoint:
                rows.extend(
                    query_endpoint(
                        endpoint,
                        query,
                        timeout=timeout,
                        username=endpoint_user,
                        password=endpoint_password,
                    )
                )
            else:
                rows.extend(query_rdflib_graph(rdf_files, query))
        except RuntimeError as exc:
            errors.append(str(exc))
    return rows, errors[0] if errors and not rows else None


def execute_query_records(
    query_records: list[dict[str, Any]],
    endpoint: str | None,
    rdf_files: list[str],
    timeout: int,
    endpoint_user: str | None = None,
    endpoint_password: str | None = None,
) -> list[dict[str, Any]]:
    """Execute each query interpretation separately and attach result rows."""
    executed = []
    for index, record in enumerate(query_records):
        rows, error = execute_queries(
            [record["sparql_query"]],
            endpoint=endpoint,
            rdf_files=rdf_files,
            timeout=timeout,
            endpoint_user=endpoint_user,
            endpoint_password=endpoint_password,
        )
        executed_record = dict(record)
        executed_record.setdefault("prediction_index", index)
        executed_record["rows"] = rows
        executed_record["execution_error"] = error
        executed.append(executed_record)
    return executed


def infer_prediction_kind(prediction: Any, requested_kind: str) -> str:
    """Resolve ``auto`` predictions into either answer rows or SPARQL queries."""
    if requested_kind != "auto":
        return requested_kind
    if isinstance(prediction, dict) and any(key in prediction for key in ("answers", "answer", "rows", "results")):
        return "answers"
    if get_predicted_queries(prediction):
        return "queries"
    return "answers"


def parse_skip_question_numbers(raw_values: list[list[str]]) -> set[int]:
    """Parse comma-separated or repeated --skip-questions arguments."""
    skip_numbers = set()
    for group in raw_values:
        for value in group:
            for token in value.split(","):
                stripped = token.strip()
                if not stripped:
                    continue
                if not stripped.isdigit():
                    raise ValueError(f"--skip-questions only accepts numeric question identifiers: {stripped!r}")
                skip_numbers.add(int(stripped))
    return skip_numbers


def filter_skipped_questions(
    questions: list[dict[str, Any]],
    skip_numbers: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split benchmark questions into evaluated and skipped groups."""
    if not skip_numbers:
        return questions, []

    kept_questions = []
    skipped_questions = []
    for position, question in enumerate(questions, start=1):
        qid = question.get("qid")
        try:
            qid_number = int(qid)
        except (TypeError, ValueError):
            qid_number = None
        if position in skip_numbers or qid_number in skip_numbers:
            skipped_questions.append(question)
        else:
            kept_questions.append(question)
    return kept_questions, skipped_questions


def is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """Return true when all needle columns appear in haystack in the same order."""
    haystack_index = 0
    for column in needle:
        while haystack_index < len(haystack) and haystack[haystack_index] != column:
            haystack_index += 1
        if haystack_index == len(haystack):
            return False
        haystack_index += 1
    return True


def values_subsequence_match(smaller: dict[str, Any], larger: dict[str, Any]) -> bool:
    """Fallback row comparison when equivalent answers use different variables."""
    smaller_values = [normalize_answer_value(value) for value in smaller.values()]
    larger_values = [normalize_answer_value(value) for value in larger.values()]
    larger_index = 0
    for value in smaller_values:
        while larger_index < len(larger_values) and larger_values[larger_index] != value:
            larger_index += 1
        if larger_index == len(larger_values):
            return False
        larger_index += 1
    return True


def canonical_column_name(column: str) -> str:
    """Map common SPARQL variable-name variants to canonical answer columns."""
    compact = re.sub(r"[^A-Za-z0-9]", "", column).lower()
    return ANSWER_COLUMN_ALIASES.get(compact, compact)


def canonicalized_row(row: dict[str, Any]) -> dict[str, str]:
    """Normalize both column names and cell values for answer matching."""
    canonical = {}
    for key, value in row.items():
        canonical[canonical_column_name(key)] = normalize_answer_value(value)
    return canonical


def shared_core_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Match rows by shared high-signal answer columns such as county or date."""
    left_canonical = canonicalized_row(left)
    right_canonical = canonicalized_row(right)
    shared_keys = [key for key in left_canonical.keys() if key in right_canonical and key in CORE_MATCH_COLUMNS]
    if not shared_keys:
        return False
    return all(left_canonical[key] == right_canonical[key] for key in shared_keys)


def normalize_answer_value(value: Any) -> str:
    """Normalize GraphDB/JSON answer values for comparison.

    GraphDB SPARQL JSON returns full IRIs and untyped lexical literal values,
    while the benchmark JSON often stores compact qnames or Python numbers.
    This function maps those surface forms into a common comparison string.
    """
    text = canonicalize_value(value).strip()
    if not text:
        return text
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]

    typed_match = TYPED_LITERAL_PATTERN.match(text)
    if typed_match:
        text = typed_match.group(1)
    lang_match = LANG_LITERAL_PATTERN.match(text)
    if lang_match:
        text = lang_match.group(1)

    for iri_prefix, qname_prefix in IRI_PREFIXES.items():
        if text.startswith(iri_prefix):
            text = qname_prefix + text[len(iri_prefix):]
            break

    if DATETIME_PATTERN.match(text):
        parse_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(parse_text)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc)
                return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            pass

    if NUMERIC_PATTERN.match(text):
        number = float(text)
        if number.is_integer():
            return str(int(number))
        return format(number, "g")
    return text


def rows_compatible(left: Any, right: Any) -> bool:
    """Compare answer rows while tolerating extra returned columns.

    If both rows are dictionaries, the row with fewer columns is matched against
    the larger row. The preferred match uses shared column names in order. If a
    generated query uses different but equivalent variable names, the fallback
    checks whether the smaller row's normalized values appear in the larger row
    in the same order.
    """
    if isinstance(left, dict) and isinstance(right, dict):
        left_keys = list(left.keys())
        right_keys = list(right.keys())
        if len(left_keys) <= len(right_keys):
            smaller, larger = left, right
            smaller_keys, larger_keys = left_keys, right_keys
        else:
            smaller, larger = right, left
            smaller_keys, larger_keys = right_keys, left_keys
        if is_subsequence(smaller_keys, larger_keys):
            return all(normalize_answer_value(smaller[key]) == normalize_answer_value(larger.get(key)) for key in smaller_keys)
        if shared_core_match(left, right):
            return True
        return values_subsequence_match(smaller, larger)
    return normalize_answer_value(left) == normalize_answer_value(right)


def count_compatible_matches(predicted: list[Any], gold: list[Any]) -> int:
    """Count one-to-one compatible predicted/gold row matches."""
    matched_gold_indexes = set()
    matches = 0
    for pred_row in predicted:
        for gold_index, gold_row in enumerate(gold):
            if gold_index in matched_gold_indexes:
                continue
            if rows_compatible(pred_row, gold_row):
                matched_gold_indexes.add(gold_index)
                matches += 1
                break
    return matches


def compatible_precision_recall_f1(predicted: list[Any], gold: list[Any]) -> dict[str, float]:
    """Compute row-level precision, recall, and F1 with tolerant row matching."""
    if not predicted and not gold:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not predicted:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    true_positive = count_compatible_matches(predicted, gold)
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def hits_at_k(predicted: list[Any], gold: list[Any], k: int) -> float:
    """Return 1 if any compatible gold row appears in the first k predictions."""
    return 1.0 if gold and any(rows_compatible(pred_row, gold_row) for pred_row in predicted[:k] for gold_row in gold) else 0.0


def reciprocal_rank(predicted: list[Any], gold: list[Any]) -> float:
    """Compute reciprocal rank of the first compatible predicted row."""
    for rank, item in enumerate(predicted, start=1):
        if any(rows_compatible(item, gold_row) for gold_row in gold):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(predicted: list[Any], gold: list[Any], k: int) -> float:
    """Compute binary-relevance nDCG@k over compatible answer rows."""
    matched_gold_indexes = set()
    gains = []
    for pred_row in predicted[:k]:
        gain = 0.0
        for gold_index, gold_row in enumerate(gold):
            if gold_index in matched_gold_indexes:
                continue
            if rows_compatible(pred_row, gold_row):
                matched_gold_indexes.add(gold_index)
                gain = 1.0
                break
        gains.append(gain)
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def average_annotation_score(rows: list[Any], key: str) -> float | None:
    """Average optional manually annotated spatial/temporal scores in answers."""
    scores = []
    for row in rows:
        if isinstance(row, dict) and key in row:
            try:
                scores.append(float(row[key]))
            except (TypeError, ValueError):
                continue
    return safe_mean(scores) if scores else None


def first_annotation_score(rows: list[Any], keys: tuple[str, ...]) -> float | None:
    """Return the first available annotation average for any known key alias."""
    for key in keys:
        score = average_annotation_score(rows, key)
        if score is not None:
            return score
    return None


def query_constraint_scores(
    predicted_set: dict[str, Any] | None,
    gold_set: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    """Score spatial/temporal constraint overlap for a selected query pair."""
    if not predicted_set or not gold_set:
        return None, None
    predicted_query = predicted_set.get("sparql_query")
    gold_query = gold_set.get("sparql_query")
    if not predicted_query or not gold_query:
        return None, None

    spatial_scores = precision_recall_f1(
        extract_spatial_constraints(predicted_query),
        extract_spatial_constraints(gold_query),
    )
    temporal_scores = precision_recall_f1(
        extract_temporal_constraints(predicted_query),
        extract_temporal_constraints(gold_query),
    )
    return spatial_scores["f1"], temporal_scores["f1"]


def spatiotemporal_scores(
    question: dict[str, Any],
    predicted_rows: list[Any],
    answer_f1: float,
    predicted_set: dict[str, Any] | None = None,
    gold_set: dict[str, Any] | None = None,
) -> tuple[float, float, float]:
    """Derive spatial, temporal, and combined relevance scores for a question.

    If answer rows carry explicit ``spatial_score`` or ``temporal_score``
    annotations, those are used. Query evaluations then fall back to
    predicted/gold SPARQL constraint overlap for required dimensions. Otherwise
    answer F1 is reused for required spatial/temporal dimensions and
    non-required dimensions receive 1.0. SRS is always the harmonic mean of the
    resulting spatial and temporal correctness values, regardless of which
    dimensions the question is annotated as using.
    """
    spatial_required = requires_spatial(question)
    temporal_required = requires_temporal(question)

    annotation_sources = list(predicted_rows)
    if predicted_set:
        annotation_sources.append(predicted_set)

    annotated_spatial = first_annotation_score(annotation_sources, SPATIAL_ANNOTATION_KEYS)
    annotated_temporal = first_annotation_score(annotation_sources, TEMPORAL_ANNOTATION_KEYS)
    query_spatial, query_temporal = query_constraint_scores(predicted_set, gold_set)

    spatial = annotated_spatial
    if spatial is None:
        spatial = (
            query_spatial
            if spatial_required and query_spatial is not None
            else (answer_f1 if spatial_required else 1.0)
        )

    temporal = annotated_temporal
    if temporal is None:
        temporal = (
            query_temporal
            if temporal_required and query_temporal is not None
            else (answer_f1 if temporal_required else 1.0)
        )

    srs = harmonic_mean(spatial, temporal)
    return spatial, temporal, srs


def json_gold_answer_set(question: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    """Build a gold answer set from the stored benchmark JSON answer field."""
    gold_interpretations = get_gold_interpretations(question)
    gold_interpretation = gold_interpretations[0] if gold_interpretations else {}
    return {
        "gold_index": None,
        "interpretation_id": "stored_json_answer",
        "label": "Stored benchmark answer fallback",
        "rows": flatten_answer_rows(question.get("answer")),
        "sparql_query": gold_interpretation.get("sparql_query") or question.get("sparql_query"),
        "execution_error": None,
        "gold_answer_source": "json_answer",
        "gold_fallback_reason": reason,
    }


def answer_pair_scores(
    question: dict[str, Any],
    predicted_rows: list[Any],
    gold_rows: list[Any],
    predicted_set: dict[str, Any] | None = None,
    gold_set: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Compute all Task 3 metrics for one predicted/gold answer-set pair."""
    answer_scores = compatible_precision_recall_f1(predicted_rows, gold_rows)
    spatial_score, temporal_score, srs = spatiotemporal_scores(
        question,
        predicted_rows,
        answer_scores["f1"],
        predicted_set=predicted_set,
        gold_set=gold_set,
    )
    return {
        "answer_precision": answer_scores["precision"],
        "answer_recall": answer_scores["recall"],
        "answer_f1": answer_scores["f1"],
        "hits_at_1": hits_at_k(predicted_rows, gold_rows, 1),
        "hits_at_5": hits_at_k(predicted_rows, gold_rows, 5),
        "hits_at_10": hits_at_k(predicted_rows, gold_rows, 10),
        "mrr": reciprocal_rank(predicted_rows, gold_rows),
        "ndcg_at_10": ndcg_at_k(predicted_rows, gold_rows, 10),
        "spatial_correctness": spatial_score,
        "temporal_correctness": temporal_score,
        "spatiotemporal_relevance_score": srs,
    }


def select_best_answer_pair(
    question: dict[str, Any],
    predicted_sets: list[dict[str, Any]],
    gold_sets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select the best predicted/gold answer-set pair for one question."""
    if not gold_sets:
        gold_sets = [{"gold_index": None, "rows": flatten_answer_rows(question.get("answer")), "execution_error": None}]
    if not predicted_sets:
        predicted_sets = [{"prediction_index": None, "rows": [], "execution_error": "no predicted answer/query"}]

    scored_pairs = []
    for pred_set in predicted_sets:
        for gold_index, gold_set in enumerate(gold_sets):
            scores = answer_pair_scores(
                question,
                pred_set.get("rows", []),
                gold_set.get("rows", []),
                predicted_set=pred_set,
                gold_set=gold_set,
            )
            # If a gold interpretation failed to execute and produced no rows,
            # do not allow empty predicted rows to score as a perfect match.
            if gold_set.get("execution_error") and not gold_set.get("rows"):
                scores = {key: 0.0 for key in scores}
            scores["interpretation_selection_score"] = safe_mean(scores[key] for key in ANSWER_PAIR_SELECTION_KEYS)
            scores["selected_predicted_query_index"] = pred_set.get("prediction_index")
            scores["selected_gold_interpretation_index"] = gold_set.get("gold_index", gold_index)
            scores["selected_gold_interpretation_id"] = gold_set.get("interpretation_id")
            scores["selected_gold_interpretation_label"] = gold_set.get("label")
            scores["gold_answer_source"] = gold_set.get("gold_answer_source", "query_execution")
            scores["gold_fallback_reason"] = gold_set.get("gold_fallback_reason")
            scores["gold_answer_count"] = len(gold_set.get("rows", []))
            scores["predicted_answer_count"] = len(pred_set.get("rows", []))
            scores["predicted_rows"] = pred_set.get("rows", [])
            scores["gold_rows"] = gold_set.get("rows", [])
            scores["execution_error"] = pred_set.get("execution_error") or gold_set.get("execution_error")
            scored_pairs.append(scores)

    return max(
        scored_pairs,
        key=lambda scores: (
            scores["interpretation_selection_score"],
            scores["answer_f1"],
            scores["spatiotemporal_relevance_score"],
            scores["hits_at_1"],
        ),
    )


def evaluate_question(
    question: dict[str, Any],
    prediction: Any,
    prediction_kind: str,
    gold_from_execution: bool,
    endpoint: str | None,
    rdf_files: list[str],
    timeout: int,
    endpoint_user: str | None = None,
    endpoint_password: str | None = None,
    include_debug_rows: bool = False,
    debug_row_limit: int = 3,
) -> dict[str, Any]:
    """Evaluate one KGQA question and return a flat CSV/JSON-ready row."""
    execution_error = None
    if gold_from_execution:
        # Ambiguity-aware mode: execute every gold interpretation independently
        # so predictions can be matched against the most compatible reading.
        gold_sets = execute_query_records(
            get_gold_interpretations(question),
            endpoint=endpoint,
            rdf_files=rdf_files,
            timeout=timeout,
            endpoint_user=endpoint_user,
            endpoint_password=endpoint_password,
        )
        for gold_index, gold_set in enumerate(gold_sets):
            gold_set["gold_index"] = gold_index
            gold_set["gold_answer_source"] = "query_execution"
        if not any(gold_set.get("rows") for gold_set in gold_sets):
            fallback_reason = "all executed gold interpretations returned zero rows"
            execution_errors = [gold_set.get("execution_error") for gold_set in gold_sets if gold_set.get("execution_error")]
            if execution_errors:
                fallback_reason = f"{fallback_reason}; first error: {execution_errors[0]}"
            gold_sets = [json_gold_answer_set(question, fallback_reason)]
    else:
        gold_sets = [json_gold_answer_set(question)]

    kind = infer_prediction_kind(prediction, prediction_kind)
    if kind == "queries":
        # Execute predicted interpretations independently; merging them first
        # would hide which reading produced the strongest answer set.
        predicted_query_records = get_predicted_query_records(prediction)
        predicted_sets = execute_query_records(
            predicted_query_records,
            endpoint=endpoint,
            rdf_files=rdf_files,
            timeout=timeout,
            endpoint_user=endpoint_user,
            endpoint_password=endpoint_password,
        )
    else:
        predicted_rows = get_predicted_answers(prediction)
        predicted_sets = [{"prediction_index": None, "rows": predicted_rows, "execution_error": None}]

    best_pair = select_best_answer_pair(question, predicted_sets, gold_sets)
    gold_rows = best_pair.pop("gold_rows")
    predicted_rows = best_pair.pop("predicted_rows")
    execution_error = best_pair.pop("execution_error")

    row = {
        "qid": question.get("qid"),
        "template_id": question.get("template_id"),
        "template_family": question.get("template_family"),
        "primary_category": question.get("primary_category"),
        "query_complexity": question.get("query_complexity"),
        "spatiotemporal_axis": question.get("spatiotemporal_axis"),
        "prediction_kind": kind,
        "num_gold_interpretations": len(gold_sets),
        "num_predicted_queries": len(get_predicted_queries(prediction)) if kind == "queries" else 0,
        "interpretation_selection_score": best_pair["interpretation_selection_score"],
        "selected_predicted_query_index": best_pair["selected_predicted_query_index"],
        "selected_gold_interpretation_index": best_pair["selected_gold_interpretation_index"],
        "selected_gold_interpretation_id": best_pair["selected_gold_interpretation_id"],
        "selected_gold_interpretation_label": best_pair["selected_gold_interpretation_label"],
        "gold_answer_source": best_pair["gold_answer_source"],
        "gold_fallback_reason": best_pair["gold_fallback_reason"],
        "gold_answer_count": len(gold_rows),
        "predicted_answer_count": len(predicted_rows),
        "answer_precision": best_pair["answer_precision"],
        "answer_recall": best_pair["answer_recall"],
        "answer_f1": best_pair["answer_f1"],
        "hits_at_1": best_pair["hits_at_1"],
        "hits_at_5": best_pair["hits_at_5"],
        "hits_at_10": best_pair["hits_at_10"],
        "mrr": best_pair["mrr"],
        "ndcg_at_10": best_pair["ndcg_at_10"],
        "spatial_correctness": best_pair["spatial_correctness"],
        "temporal_correctness": best_pair["temporal_correctness"],
        "spatiotemporal_relevance_score": best_pair["spatiotemporal_relevance_score"],
        "execution_error": execution_error,
    }
    if include_debug_rows:
        row["gold_columns"] = result_columns(gold_rows)
        row["predicted_columns"] = result_columns(predicted_rows)
        row["gold_sample_rows"] = sample_rows(gold_rows, debug_row_limit)
        row["predicted_sample_rows"] = sample_rows(predicted_rows, debug_row_limit)
        row["normalized_gold_sample_rows"] = sample_rows(gold_rows, debug_row_limit, normalize=True)
        row["normalized_predicted_sample_rows"] = sample_rows(predicted_rows, debug_row_limit, normalize=True)
    return row


def result_columns(rows: list[Any]) -> list[str]:
    """Return the first result row's column order for debugging output."""
    for row in rows:
        if isinstance(row, dict):
            return list(row.keys())
    return []


def normalize_row(row: Any) -> Any:
    """Normalize one debug row using the same value rules as scoring."""
    if isinstance(row, dict):
        return {key: normalize_answer_value(value) for key, value in row.items()}
    return normalize_answer_value(row)


def sample_rows(rows: list[Any], limit: int, normalize: bool = False) -> list[Any]:
    """Return a small row sample for optional human inspection."""
    selected = rows[:limit]
    if normalize:
        return [normalize_row(row) for row in selected]
    return selected


def print_question_result(index: int, total: int, row: dict[str, Any]) -> None:
    """Print compact per-question progress while long endpoint runs execute."""
    error = row.get("execution_error")
    status = "error" if error else "ok"
    print(
        f"Question {index}/{total} "
        f"QID={row.get('qid')} "
        f"[{status}] "
        f"kind={row.get('prediction_kind')} "
        f"gold={row.get('gold_answer_count')} "
        f"pred={row.get('predicted_answer_count')} "
        f"P={float(row.get('answer_precision', 0.0)):.4f} "
        f"R={float(row.get('answer_recall', 0.0)):.4f} "
        f"F1={float(row.get('answer_f1', 0.0)):.4f} "
        f"SRS={float(row.get('spatiotemporal_relevance_score', 0.0)):.4f}"
    )
    if error:
        print(f"  error: {str(error)[:300]}")
    if "gold_columns" in row or "predicted_columns" in row:
        print(f"  gold columns: {row.get('gold_columns', [])}")
        print(f"  pred columns: {row.get('predicted_columns', [])}")
        print(f"  gold sample: {row.get('normalized_gold_sample_rows', [])}")
        print(f"  pred sample: {row.get('normalized_predicted_sample_rows', [])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate GeoOutageBench Task 3: Spatiotemporal KGQA.")
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK), help="Path to geooutagebench_questions.json.")
    parser.add_argument("--predictions", required=True, help="JSON file containing predicted answers or queries.")
    parser.add_argument("--prediction-kind", choices=["auto", "answers", "queries"], default="queries")
    parser.add_argument("--gold-from-execution", action="store_true", help="Execute gold SPARQL instead of using JSON gold answers.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for CSV/JSON outputs.")
    parser.add_argument("--endpoint", default=None, help="Optional SPARQL endpoint.")
    parser.add_argument("--graphdb-url", default=DEFAULT_GRAPHDB_URL, help="GraphDB base URL, used with --graphdb-repository.")
    parser.add_argument("--graphdb-repository", default=None, help="GraphDB repository ID. Builds endpoint as <url>/repositories/<repo>.")
    parser.add_argument("--graphdb-user", default=os.getenv("GRAPHDB_USER"), help="Optional GraphDB username. Defaults to GRAPHDB_USER.")
    parser.add_argument("--graphdb-password", default=os.getenv("GRAPHDB_PASSWORD"), help="Optional GraphDB password. Defaults to GRAPHDB_PASSWORD.")
    parser.add_argument("--rdf-file", action="append", default=[], help="Optional local RDF file. Can be repeated.")
    parser.add_argument("--timeout", type=int, default=3600, help="Endpoint timeout in seconds.")
    parser.add_argument("--quiet", action="store_true", help="Do not print per-question progress/results.")
    parser.add_argument("--print-rows", action="store_true", help="Print gold/predicted columns and normalized sample rows for each question.")
    parser.add_argument("--print-row-limit", type=int, default=3, help="Number of gold/predicted sample rows to print with --print-rows.")
    parser.add_argument("--model-output-dir", type=str, default="", help="Name of model to use as output directory.")
    parser.add_argument(
        "--skip-questions",
        action="append",
        default=[],
        nargs="+",
        metavar="QUESTION",
        help=(
            "Question numbers to skip. Accepts benchmark qids or 1-based positions; "
            "can be repeated or comma-separated."
        ),
    )
    args = parser.parse_args()

    endpoint = args.endpoint
    if args.graphdb_repository:
        endpoint = build_graphdb_endpoint(args.graphdb_url, args.graphdb_repository)

    questions = load_benchmark(args.benchmark)
    predictions = get_prediction_map(args.predictions)
    try:
        skip_numbers = parse_skip_question_numbers(args.skip_questions)
    except ValueError as exc:
        parser.error(str(exc))

    questions_to_evaluate, skipped_questions = filter_skipped_questions(questions, skip_numbers)
    skipped_qids = [question.get("qid") for question in skipped_questions]

    if skipped_questions and not args.quiet:
        print(f"Skipping {len(skipped_questions)} question(s): {', '.join(str(qid) for qid in skipped_qids)}")

    rows = []
    for index, question in enumerate(questions_to_evaluate, start=1):
        row = evaluate_question(
            question=question,
            prediction=predictions.get(str(question.get("qid"))),
            prediction_kind=args.prediction_kind,
            gold_from_execution=args.gold_from_execution,
            endpoint=endpoint,
            rdf_files=args.rdf_file,
            timeout=args.timeout,
            endpoint_user=args.graphdb_user,
            endpoint_password=args.graphdb_password,
            include_debug_rows=args.print_rows,
            debug_row_limit=args.print_row_limit,
        )
        rows.append(row)
        if not args.quiet:
            print_question_result(index, len(questions_to_evaluate), row)

    metric_keys = [
        "answer_precision",
        "answer_recall",
        "answer_f1",
        "interpretation_selection_score",
        "hits_at_1",
        "hits_at_5",
        "hits_at_10",
        "mrr",
        "ndcg_at_10",
        "spatial_correctness",
        "temporal_correctness",
        "spatiotemporal_relevance_score",
    ]
    summary = {
        "task": "Task 3: Spatiotemporal KGQA",
        "benchmark": str(args.benchmark),
        "predictions": str(args.predictions),
        "endpoint": endpoint,
        "question_count": len(rows),
        "source_question_count": len(questions),
        "skipped_question_count": len(skipped_questions),
        "skipped_qids": skipped_qids,
        "macro": macro_average(rows, metric_keys),
        "by_spatiotemporal_axis": summarize_by(rows, "spatiotemporal_axis", metric_keys),
        "by_template_family": summarize_by(rows, "template_family", metric_keys),
        "by_query_complexity": summarize_by(rows, "query_complexity", metric_keys),
    }

    output_dir = Path(args.output_dir)
    if args.model_output_dir:
        output_dir = output_dir / args.model_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "task3_per_question.csv", rows)
    write_json(output_dir / "task3_per_question.json", rows)
    write_json(output_dir / "task3_summary.json", summary)

    print(f"Wrote Task 3 results to {output_dir}")
    print("Macro metrics:")
    for key, value in summary["macro"].items():
        print(f"  {key}: {value:.4f}")


if __name__ == "__main__":
    main()
