"""Create a Task 1 NL2SPARQL prediction template.

Use this to prepare the JSON file consumed by
``evaluate_task1_nl2temporalsparql.py``. In normal experiments, generate the
blank template, run your NL2SPARQL model on each question, and replace the empty
``sparql_query`` values with predicted queries. Ambiguous questions may also
fill ``sparql_interpretations`` with multiple labeled query interpretations.

Modes:
  blank          Empty predictions for model output collection.
  gold-original  Original benchmark SPARQL as an oracle smoke-test baseline.
  gold-all       All benchmark interpretations as oracle ambiguity predictions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from geooutage_eval_utils import get_gold_interpretations, get_original_gold_sparql, load_benchmark


DEFAULT_BENCHMARK = Path(__file__).with_name("geooutagebench_questions.json")
DEFAULT_OUTPUT = Path(__file__).with_name("nl2sparql_predictions_template.json")


def build_record(question: dict[str, Any], mode: str) -> dict[str, Any]:
    """Build one prediction-template record from one benchmark question.

    The non-answer metadata is copied into every mode so model outputs and
    evaluation rows are easy to inspect without repeatedly opening the full
    benchmark file.
    """
    record = {
        "qid": question["qid"],
        "question": question["question"],
        "template_id": question.get("template_id"),
        "template_family": question.get("template_family"),
        "primary_category": question.get("primary_category"),
        "spatiotemporal_axis": question.get("spatiotemporal_axis"),
    }
    if mode == "blank":
        # Empty slots for model-generated predictions. The list field invites
        # ambiguity-aware outputs while remaining harmless for single-query use.
        record["sparql_query"] = ""
        record["sparql_interpretations"] = []
    elif mode == "gold-original":
        # Single-query oracle baseline: useful for checking exact original
        # matching and endpoint connectivity.
        record["sparql_query"] = get_original_gold_sparql(question)
    elif mode == "gold-all":
        # Ambiguity oracle baseline: emits every benchmark interpretation in
        # the same rich format accepted by the evaluators.
        gold_interpretations = get_gold_interpretations(question)
        record["sparql_query"] = get_original_gold_sparql(question)
        record["sparql_interpretations"] = gold_interpretations
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a GeoOutageBench NL2SPARQL prediction template.")
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--mode", choices=["blank", "gold-original", "gold-all"], default="blank")
    args = parser.parse_args()

    questions = load_benchmark(args.benchmark)
    payload = {
        "benchmark": str(args.benchmark),
        "mode": args.mode,
        "predictions": [build_record(question, args.mode) for question in questions],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(f"Wrote {len(payload['predictions'])} prediction records to {output}")


if __name__ == "__main__":
    main()
