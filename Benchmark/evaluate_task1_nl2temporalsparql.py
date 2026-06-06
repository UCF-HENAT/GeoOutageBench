"""Evaluate Task 1: NL2TemporalSPARQL.

Prediction file formats accepted:

1. Dict keyed by qid:
   {
     "1000000": {"sparql_query": "SELECT ..."},
     "1000002": {"sparql_queries": ["SELECT ...", "SELECT ..."]}
   }

2. List of records:
   [
     {"qid": 1000000, "sparql_query": "SELECT ..."},
     {"qid": 1000002, "sparql_queries": ["SELECT ...", "SELECT ..."]},
     {"qid": 1000003, "sparql_interpretations": [{"label": "...", "sparql_query": "SELECT ..."}]}
   ]

The script evaluates exact match, ambiguity-aware interpretation recall, schema
linking, spatial/temporal constraint recovery, syntax validity, and optional
executability. Schema and constraint F1s are computed against the best matching
gold interpretation for each predicted interpretation set.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from geooutage_eval_utils import (
    coarse_sparql_syntax_ok,
    extract_classes,
    extract_properties,
    extract_spatial_constraints,
    extract_temporal_constraints,
    get_gold_interpretations,
    get_gold_sparqls,
    get_original_gold_sparql,
    get_predicted_query_records,
    get_predicted_queries,
    get_prediction_map,
    harmonic_mean,
    load_benchmark,
    macro_average,
    normalize_sparql,
    precision_recall_f1,
    query_endpoint,
    query_rdflib_graph,
    safe_mean,
    summarize_by,
    write_csv,
    write_json,
)


DEFAULT_BENCHMARK = Path(__file__).with_name("geooutagebench_questions.json")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("eval_outputs") / "task1_nl2temporalsparql"

# These are the structural F1 components used to choose the best predicted/gold
# interpretation pair. Exact string match is still reported separately.
PAIRWISE_F1_KEYS = [
    "class_f1",
    "property_f1",
    "spatial_constraint_f1",
    "temporal_constraint_f1",
]


def syntax_validity(queries: list[str]) -> float:
    """Return the fraction of predicted queries that look like SELECT/WHERE or ASK SPARQL."""
    if not queries:
        return 0.0
    return safe_mean(1.0 if coarse_sparql_syntax_ok(query) else 0.0 for query in queries)


def executability(
    queries: list[str],
    endpoint: str | None,
    rdf_files: list[str],
    timeout: int,
) -> tuple[float, str | None]:
    """Check whether at least one predicted query can run against the configured KG."""
    if not endpoint and not rdf_files:
        return 0.0, None
    if not queries:
        return 0.0, "no predicted query"

    errors = []
    for query in queries:
        try:
            if endpoint:
                query_endpoint(endpoint, query, timeout=timeout)
            else:
                query_rdflib_graph(rdf_files, query)
            return 1.0, None
        except RuntimeError as exc:
            errors.append(str(exc))
    return 0.0, errors[0] if errors else "execution failed"


def score_query_pair(predicted_query: str, gold_query: str) -> dict[str, float]:
    """Score one predicted SPARQL interpretation against one gold interpretation.

    The benchmark treats schema linking and spatiotemporal constraints as
    extractable query features. This pairwise score prevents a valid alternate
    interpretation from being penalized for not matching every gold variant at
    once.
    """
    class_scores = precision_recall_f1(extract_classes(predicted_query), extract_classes(gold_query))
    property_scores = precision_recall_f1(extract_properties(predicted_query), extract_properties(gold_query))
    temporal_scores = precision_recall_f1(
        extract_temporal_constraints(predicted_query),
        extract_temporal_constraints(gold_query),
    )
    spatial_scores = precision_recall_f1(
        extract_spatial_constraints(predicted_query),
        extract_spatial_constraints(gold_query),
    )
    schema_linking_f1 = harmonic_mean(class_scores["f1"], property_scores["f1"])
    st_constraint_f1 = harmonic_mean(spatial_scores["f1"], temporal_scores["f1"])
    exact_pair_match = 1.0 if normalize_sparql(predicted_query) == normalize_sparql(gold_query) else 0.0

    return {
        "exact_pair_match": exact_pair_match,
        "class_precision": class_scores["precision"],
        "class_recall": class_scores["recall"],
        "class_f1": class_scores["f1"],
        "property_precision": property_scores["precision"],
        "property_recall": property_scores["recall"],
        "property_f1": property_scores["f1"],
        "schema_linking_f1": schema_linking_f1,
        "spatial_constraint_f1": spatial_scores["f1"],
        "temporal_constraint_f1": temporal_scores["f1"],
        "spatiotemporal_constraint_f1": st_constraint_f1,
    }


def select_best_interpretation_pair(
    predicted_records: list[dict[str, Any]],
    gold_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the best scoring predicted/gold interpretation alignment.

    A question can have multiple acceptable readings. We therefore score every
    predicted query against every gold interpretation and keep the strongest
    alignment for the per-question metrics.
    """
    if not predicted_records or not gold_records:
        empty_scores = score_query_pair("", gold_records[0]["sparql_query"] if gold_records else "")
        empty_scores.update(
            {
                "interpretation_alignment_score": 0.0,
                "selected_predicted_query_index": None,
                "selected_gold_interpretation_index": None,
                "selected_gold_interpretation_id": None,
                "selected_gold_interpretation_label": None,
            }
        )
        return empty_scores

    scored_pairs = []
    for pred_index, pred_record in enumerate(predicted_records):
        for gold_index, gold_record in enumerate(gold_records):
            scores = score_query_pair(pred_record["sparql_query"], gold_record["sparql_query"])
            # Use a simple macro average across ontology and constraint F1s as
            # the interpretation-selection objective.
            scores["interpretation_alignment_score"] = safe_mean(scores[key] for key in PAIRWISE_F1_KEYS)
            scores["selected_predicted_query_index"] = pred_record.get("prediction_index", pred_index)
            scores["selected_gold_interpretation_index"] = gold_index
            scores["selected_gold_interpretation_id"] = gold_record.get("interpretation_id")
            scores["selected_gold_interpretation_label"] = gold_record.get("label")
            scored_pairs.append(scores)

    return max(
        scored_pairs,
        key=lambda scores: (
            scores["interpretation_alignment_score"],
            scores["exact_pair_match"],
            scores["schema_linking_f1"],
            scores["spatiotemporal_constraint_f1"],
        ),
    )


def evaluate_question(
    question: dict[str, Any],
    prediction: Any,
    endpoint: str | None,
    rdf_files: list[str],
    timeout: int,
) -> dict[str, Any]:
    """Evaluate one benchmark question and return a flat CSV/JSON-ready row."""
    predicted_records = get_predicted_query_records(prediction)
    predicted_queries = get_predicted_queries(prediction)
    gold_records = get_gold_interpretations(question)
    gold_queries = get_gold_sparqls(question)
    original_gold = get_original_gold_sparql(question)

    normalized_gold_all = {normalize_sparql(query) for query in gold_queries}
    normalized_pred = {normalize_sparql(query) for query in predicted_queries}
    normalized_original = normalize_sparql(original_gold)

    # Exact-match metrics remain useful for oracle baselines and regression
    # checks, even though the main structural scores are interpretation-aware.
    exact_original_match = 1.0 if normalized_original and normalized_original in normalized_pred else 0.0
    any_interpretation_match = 1.0 if normalized_gold_all & normalized_pred else 0.0
    interpretation_recall = (
        len(normalized_gold_all & normalized_pred) / len(normalized_gold_all)
        if normalized_gold_all
        else 0.0
    )

    best_pair = select_best_interpretation_pair(predicted_records, gold_records)

    executable, execution_error = executability(predicted_queries, endpoint, rdf_files, timeout)

    return {
        "qid": question.get("qid"),
        "template_id": question.get("template_id"),
        "template_family": question.get("template_family"),
        "primary_category": question.get("primary_category"),
        "query_complexity": question.get("query_complexity"),
        "spatiotemporal_axis": question.get("spatiotemporal_axis"),
        "num_gold_interpretations": len(normalized_gold_all),
        "num_predicted_queries": len(predicted_queries),
        "syntax_validity": syntax_validity(predicted_queries),
        "exact_original_match": exact_original_match,
        "any_interpretation_match": any_interpretation_match,
        "interpretation_recall": interpretation_recall,
        "interpretation_alignment_score": best_pair["interpretation_alignment_score"],
        "selected_predicted_query_index": best_pair["selected_predicted_query_index"],
        "selected_gold_interpretation_index": best_pair["selected_gold_interpretation_index"],
        "selected_gold_interpretation_id": best_pair["selected_gold_interpretation_id"],
        "selected_gold_interpretation_label": best_pair["selected_gold_interpretation_label"],
        "class_precision": best_pair["class_precision"],
        "class_recall": best_pair["class_recall"],
        "class_f1": best_pair["class_f1"],
        "property_precision": best_pair["property_precision"],
        "property_recall": best_pair["property_recall"],
        "property_f1": best_pair["property_f1"],
        "schema_linking_f1": best_pair["schema_linking_f1"],
        "spatial_constraint_f1": best_pair["spatial_constraint_f1"],
        "temporal_constraint_f1": best_pair["temporal_constraint_f1"],
        "spatiotemporal_constraint_f1": best_pair["spatiotemporal_constraint_f1"],
        "executability": executable,
        "execution_error": execution_error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate GeoOutageBench Task 1: NL2TemporalSPARQL.")
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK), help="Path to geooutagebench_questions.json.")
    parser.add_argument("--predictions", required=True, help="JSON file containing predicted SPARQL queries.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for CSV/JSON outputs.")
    parser.add_argument("--endpoint", default=None, help="Optional SPARQL endpoint for executability checks.")
    parser.add_argument("--rdf-file", action="append", default=[], help="Optional local RDF file. Can be repeated.")
    parser.add_argument("--timeout", type=int, default=60, help="Endpoint timeout in seconds.")
    parser.add_argument("--model-output-dir", type=str, default="", help="Name of model to use as output directory.")
    args = parser.parse_args()

    questions = load_benchmark(args.benchmark)
    predictions = get_prediction_map(args.predictions)

    rows = [
        evaluate_question(
            question=question,
            prediction=predictions.get(str(question.get("qid"))),
            endpoint=args.endpoint,
            rdf_files=args.rdf_file,
            timeout=args.timeout,
        )
        for question in questions
    ]

    metric_keys = [
        "syntax_validity",
        "exact_original_match",
        "any_interpretation_match",
        "interpretation_recall",
        "interpretation_alignment_score",
        "class_f1",
        "property_f1",
        "schema_linking_f1",
        "spatial_constraint_f1",
        "temporal_constraint_f1",
        "spatiotemporal_constraint_f1",
        "executability",
    ]
    macro_metrics = macro_average(rows, metric_keys)
    macro_metrics["macro_spatial_temporal_hmean"] = harmonic_mean(
        macro_metrics["spatial_constraint_f1"],
        macro_metrics["temporal_constraint_f1"],
    )

    summary = {
        "task": "Task 1: NL2TemporalSPARQL",
        "benchmark": str(args.benchmark),
        "predictions": str(args.predictions),
        "question_count": len(rows),
        "macro": macro_metrics,
        "by_spatiotemporal_axis": summarize_by(rows, "spatiotemporal_axis", metric_keys),
        "by_template_family": summarize_by(rows, "template_family", metric_keys),
        "by_query_complexity": summarize_by(rows, "query_complexity", metric_keys),
    }

    output_dir = Path(args.output_dir)
    if args.model_output_dir:
        output_dir = output_dir / args.model_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "task1_per_question.csv", rows)
    write_json(output_dir / "task1_per_question.json", rows)
    write_json(output_dir / "task1_summary.json", summary)

    print(f"Wrote Task 1 results to {output_dir}")
    print("Macro metrics:")
    for key, value in summary["macro"].items():
        print(f"  {key}: {value:.4f}")


if __name__ == "__main__":
    main()
