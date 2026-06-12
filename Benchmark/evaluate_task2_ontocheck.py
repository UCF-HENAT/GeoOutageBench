"""Evaluate Task 2: Ontology Utility with OntoCheck."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from geooutage_eval_utils import (
    get_gold_interpretations,
    get_predicted_query_records,
    get_prediction_map,
    load_benchmark,
    safe_mean,
    write_csv,
    write_json,
)


DEFAULT_BENCHMARK = Path(__file__).with_name("geooutagebench_questions.json")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("eval_outputs") / "task2_ontocheck"
DEFAULT_DOMAIN_PREFIXES = ["goo"]


def get_ontocheck_questions_arg(query_file: str | Path) -> str | list[str]:
    """Return a questions argument compatible with OntoCheck."""
    query_path = Path(query_file)
    if query_path.suffix.lower() != ".json":
        return str(query_path)

    with query_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            return str(query_path)
        return [
            item if isinstance(item, str) else item.get("sparql_query", "")
            for item in data
            if isinstance(item, str) or isinstance(item, dict)
        ]

    if isinstance(data, dict):
        if isinstance(data.get("sparql_query"), str):
            return [data["sparql_query"]]
        if isinstance(data.get("sparql_queries"), list):
            return [query for query in data["sparql_queries"] if isinstance(query, str)]

    return str(query_path)


def run_ontocheck_query_file(
    query_file: str | Path,
    ttl_file: str,
    domain_prefixes: list[str] | None = None,
) -> dict[str, float]:
    try:
        from ontocheck import run_task_based_assessment
    except ImportError as exc:
        raise RuntimeError("OntoCheck is required for Task 2 scoring. Install it before running evaluation.") from exc

    result = run_task_based_assessment(
        ttl_files=ttl_file,
        questions=get_ontocheck_questions_arg(query_file),
        domain_prefixes=domain_prefixes or DEFAULT_DOMAIN_PREFIXES,
    )
    return {
        "relevance": result["relevance"],
        "accuracy": result["accuracy"],
    }


def run_ontocheck_per_query(
    directory: str,
    ttl_file: str,
    domain_prefixes: list[str] | None = None,
) -> list[dict[str, Any]]:
    results = []
    # Mode 3: Single ontology vs. competency questions
    with os.scandir(directory) as files:
        query_files = sorted((file for file in files if file.is_file()), key=lambda file: file.name)
        for index, file in enumerate(query_files, start=1):
            result = run_ontocheck_query_file(file.path, ttl_file, domain_prefixes=domain_prefixes)
            results.append(
                {
                    "result_index": index,
                    "source": "directory",
                    "status": "scored",
                    "query_file": file.name,
                    "query_path": file.path,
                    "relevance": result["relevance"],
                    "accuracy": result["accuracy"],
                }
            )

    return results


def run_ontocheck_gold_interpretations(
    benchmark: str,
    ttl_file: str,
    domain_prefixes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Score every gold SPARQL interpretation stored in the benchmark JSON."""
    questions = load_benchmark(benchmark)
    results = []

    with tempfile.TemporaryDirectory(prefix="geooutagebench_task2_ontocheck_") as temp_dir:
        temp_dir_path = Path(temp_dir)
        for question_index, question in enumerate(questions, start=1):
            qid = question.get("qid")
            query_records = get_gold_interpretations(question)
            for interpretation_index, record in enumerate(query_records):
                query_file = temp_dir_path / f"qid_{qid}_gold_{interpretation_index + 1:02d}.json"
                write_json(query_file, [{"sparql_query": record["sparql_query"]}])
                result = run_ontocheck_query_file(query_file, ttl_file, domain_prefixes=domain_prefixes)
                results.append(
                    {
                        "result_index": len(results) + 1,
                        "source": "gold",
                        "status": "scored",
                        "qid": qid,
                        "question_index": question_index,
                        "template_id": question.get("template_id"),
                        "template_family": question.get("template_family"),
                        "primary_category": question.get("primary_category"),
                        "query_complexity": question.get("query_complexity"),
                        "spatiotemporal_axis": question.get("spatiotemporal_axis"),
                        "gold_interpretation_index": record.get("interpretation_index", interpretation_index),
                        "gold_interpretation_id": record.get("interpretation_id"),
                        "gold_interpretation_label": record.get("label"),
                        "is_original_source_query": record.get("is_original_source_query"),
                        "query_file": query_file.name,
                        "query_path": None,
                        "sparql_query": record["sparql_query"],
                        "relevance": result["relevance"],
                        "accuracy": result["accuracy"],
                    }
                )

    return results


def run_ontocheck_prediction_interpretations(
    benchmark: str,
    predictions_path: str,
    ttl_file: str,
    domain_prefixes: list[str] | None = None,
    count_missing_as_zero: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Score every predicted SPARQL interpretation in a Task 1-style file."""
    questions = load_benchmark(benchmark)
    predictions = get_prediction_map(predictions_path)
    results = []
    missing_predictions = []

    with tempfile.TemporaryDirectory(prefix="geooutagebench_task2_ontocheck_") as temp_dir:
        temp_dir_path = Path(temp_dir)
        for question_index, question in enumerate(questions, start=1):
            qid = question.get("qid")
            query_records = get_predicted_query_records(predictions.get(str(qid)))
            if not query_records:
                missing_record = {
                    "qid": qid,
                    "question_index": question_index,
                    "question": question.get("question"),
                    "reason": "no predicted SPARQL interpretation",
                }
                missing_predictions.append(missing_record)
                if count_missing_as_zero:
                    results.append(
                        {
                            "result_index": len(results) + 1,
                            "source": "predictions",
                            "status": "missing_prediction",
                            "qid": qid,
                            "question_index": question_index,
                            "template_id": question.get("template_id"),
                            "template_family": question.get("template_family"),
                            "primary_category": question.get("primary_category"),
                            "query_complexity": question.get("query_complexity"),
                            "spatiotemporal_axis": question.get("spatiotemporal_axis"),
                            "prediction_index": None,
                            "prediction_subindex": None,
                            "prediction_label": None,
                            "query_file": None,
                            "query_path": None,
                            "sparql_query": None,
                            "relevance": 0.0,
                            "accuracy": 0.0,
                        }
                    )
                continue

            for interpretation_index, record in enumerate(query_records):
                query_file = temp_dir_path / f"qid_{qid}_prediction_{interpretation_index + 1:02d}.json"
                write_json(query_file, [{"sparql_query": record["sparql_query"]}])
                result = run_ontocheck_query_file(query_file, ttl_file, domain_prefixes=domain_prefixes)
                results.append(
                    {
                        "result_index": len(results) + 1,
                        "source": "predictions",
                        "status": "scored",
                        "qid": qid,
                        "question_index": question_index,
                        "template_id": question.get("template_id"),
                        "template_family": question.get("template_family"),
                        "primary_category": question.get("primary_category"),
                        "query_complexity": question.get("query_complexity"),
                        "spatiotemporal_axis": question.get("spatiotemporal_axis"),
                        "prediction_index": record.get("prediction_index", interpretation_index),
                        "prediction_subindex": record.get("prediction_subindex"),
                        "prediction_label": record.get("label") or record.get("interpretation_label"),
                        "query_file": query_file.name,
                        "query_path": None,
                        "sparql_query": record["sparql_query"],
                        "relevance": result["relevance"],
                        "accuracy": result["accuracy"],
                    }
                )

    return results, missing_predictions


def round_result_scores(results: list[dict[str, Any]], digits: int) -> list[dict[str, Any]]:
    return [
        {
            **result,
            "relevance": round(result["relevance"], digits),
            "accuracy": round(result["accuracy"], digits),
        }
        for result in results
    ]


def summarize_results(
    results: list[dict[str, Any]],
    digits: int,
    task_label: str,
    source: str,
    directory: str | None = None,
    benchmark: str | None = None,
    predictions: str | None = None,
    ttl_file: str | None = None,
    missing_predictions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    average_relevance = safe_mean(float(result["relevance"]) for result in results)
    average_accuracy = safe_mean(float(result["accuracy"]) for result in results)
    rounded_average_relevance = round(average_relevance, digits)
    rounded_average_accuracy = round(average_accuracy, digits)
    missing_predictions = missing_predictions or []

    summary = {
        "task": task_label,
        "source": source,
        "directory": str(directory) if directory else None,
        "benchmark": str(benchmark) if benchmark else None,
        "predictions": str(predictions) if predictions else None,
        "ttl_file": str(ttl_file) if ttl_file else None,
        "result_count": len(results),
        "missing_prediction_count": len(missing_predictions),
        "macro": {
            "relevance": rounded_average_relevance,
            "accuracy": rounded_average_accuracy,
        },
    }
    summary_rows = [
        {
            "source": source,
            "result_count": len(results),
            "missing_prediction_count": len(missing_predictions),
            "average_relevance": rounded_average_relevance,
            "average_accuracy": rounded_average_accuracy,
        }
    ]
    return summary, summary_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate GeoOutageBench Task 2: Ontology Utility with OntoCheck.")
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK), help="Path to geooutagebench_questions.json.")
    parser.add_argument("--predictions", default=None, help="Task 1-style JSON file containing LLM SPARQL interpretations.")
    parser.add_argument(
        "--gold-source",
        choices=["benchmark", "directory"],
        default="benchmark",
        help="Source for gold scoring when --predictions is not supplied.",
    )
    parser.add_argument("--directory", type=str, default="./sparql_queries", help="Legacy directory containing SPARQL queries")
    parser.add_argument("--ttl_file", type=str, default="../GeoOutageOnto_Beta.ttl", help="Path to the ontology file")
    parser.add_argument("--round", type=int, default=4, help="Number of decimal places to round to")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for CSV/JSON outputs.")
    parser.add_argument("--model-output-dir", type=str, default="", help="Name of model to use as output directory.")
    parser.add_argument(
        "--count-missing-as-zero",
        action="store_true",
        help="In prediction mode, include benchmark questions with no predicted SPARQL as zero-score rows.",
    )
    args = parser.parse_args()

    if args.predictions:
        results, missing_predictions = run_ontocheck_prediction_interpretations(
            benchmark=args.benchmark,
            predictions_path=args.predictions,
            ttl_file=args.ttl_file,
            count_missing_as_zero=args.count_missing_as_zero,
        )
        source = "predictions"
    elif args.gold_source == "directory":
        results = run_ontocheck_per_query(args.directory, args.ttl_file)
        missing_predictions = []
        source = "directory"
    else:
        results = run_ontocheck_gold_interpretations(args.benchmark, args.ttl_file)
        missing_predictions = []
        source = "gold"

    rounded_results = round_result_scores(results, args.round)

    for i, result in enumerate(rounded_results, start=1):
        label = result.get("qid") or result.get("query_file") or i
        print(f"{i}: {label}")
        print(f"Relevance: {result['relevance']}")
        print(f"Accuracy:  {result['accuracy']}\n")

    summary, summary_rows = summarize_results(
        results=results,
        digits=args.round,
        task_label="Task 2: Ontology Utility",
        source=source,
        directory=args.directory if source == "directory" else None,
        benchmark=args.benchmark if source in {"gold", "predictions"} else None,
        predictions=args.predictions,
        ttl_file=args.ttl_file,
        missing_predictions=missing_predictions,
    )
    print(f"Average Relevance: {summary['macro']['relevance']}")
    print(f"Average Accuracy:  {summary['macro']['accuracy']}")
    if missing_predictions:
        print(f"Missing predictions: {len(missing_predictions)}")

    output_dir = Path(args.output_dir)
    if args.model_output_dir:
        output_dir = output_dir / args.model_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "task2_per_result.csv", rounded_results)
    write_json(output_dir / "task2_per_result.json", rounded_results)
    write_csv(output_dir / "task2_summary.csv", summary_rows)
    write_json(output_dir / "task2_summary.json", summary)
    if missing_predictions:
        write_csv(output_dir / "task2_missing_predictions.csv", missing_predictions)
        write_json(output_dir / "task2_missing_predictions.json", missing_predictions)

    print(f"Wrote Task 2 results to {output_dir}")

if __name__ == "__main__":
    main()
