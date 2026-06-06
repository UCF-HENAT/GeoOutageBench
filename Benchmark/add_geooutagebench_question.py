"""Append a question entry to geooutagebench_questions.json.

This script creates the next qid, builds the standard GeoOutageBench question
metadata, appends the entry, and refreshes the benchmark summary/catalog counts.
Nested metadata can be passed as JSON on the command line or in a JSON file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_BENCHMARK_PATH = Path(__file__).with_name("geooutagebench_questions.json")

STANDARD_QUESTION_KEYS = [
    "qid",
    "question",
    "answer_type",
    "answer",
    "function",
    "commonness",
    "num_node",
    "num_edge",
    "graph_query",
    "primary_category",
    "secondary_categories",
    "query_complexity",
    "ambiguity_types",
    "sparql_query",
    "template_id",
    "template_family",
    "question_template",
    "template_variables",
    "spatiotemporal_axis",
    "ambiguity_profile",
    "window_and_constraint_bindings",
    "sparql_queries",
    "sparql_interpretations",
]

REQUIRED_METADATA_FIELDS = [
    "answer_type",
    "graph_query",
    "primary_category",
    "query_complexity",
    "ambiguity_types",
    "sparql_query",
    "template_id",
    "template_family",
    "question_template",
    "template_variables",
    "spatiotemporal_axis",
]

AMBIGUITY_PROFILE_BUCKETS = [
    "spatial",
    "temporal",
    "semantic",
    "spatiotemporal",
]

WINDOW_BINDING_DEFAULTS = OrderedDict(
    [
        ("datetime_literals", []),
        ("date_string_literals", []),
        ("outage_thresholds", []),
        ("svi_thresholds", []),
        ("duration_thresholds", []),
        ("distance_metre_thresholds", []),
        ("lat_lon_degree_thresholds", []),
        ("uses_same_day_substring_join", False),
        ("uses_geosparql", False),
    ]
)


def load_json_arg(value: str, label: str) -> Any:
    """Load JSON from a string, file path, or @file path."""
    candidate = value[1:] if value.startswith("@") else value
    path = Path(candidate)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                return json.load(handle)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{label} file must contain valid JSON: {exc}") from exc

    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} must be valid JSON or a readable file path: {exc}") from exc


def load_text_arg(value: str, label: str) -> str:
    """Load plain text from a string, file path, or @file path."""
    candidate = value[1:] if value.startswith("@") else value
    path = Path(candidate)
    if path.exists():
        return path.read_text(encoding="utf-8-sig")
    if value.startswith("@"):
        raise SystemExit(f"{label} file does not exist: {candidate}")
    return value


def ordered_unique(values: list[Any]) -> list[Any]:
    seen = set()
    result = []
    for value in values:
        marker = json.dumps(value, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def infer_next_qid(questions: list[dict[str, Any]]) -> int:
    qids = [question["qid"] for question in questions if isinstance(question.get("qid"), int)]
    return max(qids, default=999999) + 1


def count_graph_nodes_edges(graph_query: dict[str, Any]) -> tuple[int, int]:
    if not isinstance(graph_query, dict):
        raise SystemExit("graph_query must be a JSON object.")
    nodes = graph_query.get("nodes", [])
    edges = graph_query.get("edges", [])
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise SystemExit("graph_query must contain list-valued 'nodes' and 'edges' fields.")
    return len(nodes), len(edges)


def build_ambiguity_profile(ambiguity_types: list[str]) -> OrderedDict[str, Any]:
    profile: OrderedDict[str, Any] = OrderedDict()
    profile["declared_types"] = ambiguity_types
    for bucket in AMBIGUITY_PROFILE_BUCKETS:
        profile[bucket] = []
    return profile


def infer_window_and_constraint_bindings(sparql_query: str) -> OrderedDict[str, Any]:
    bindings = OrderedDict((key, value.copy() if isinstance(value, list) else value) for key, value in WINDOW_BINDING_DEFAULTS.items())

    datetime_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?"
    date_pattern = r"(?<!T)\b\d{4}-\d{2}-\d{2}\b(?!T)"
    duration_pattern = r"\bP(?:\d+D|T\d+[HMS]|\d+Y|\d+M)[A-Z0-9]*\b"

    bindings["datetime_literals"] = ordered_unique(re.findall(datetime_pattern, sparql_query))
    bindings["date_string_literals"] = ordered_unique(re.findall(date_pattern, sparql_query))
    bindings["duration_thresholds"] = ordered_unique(re.findall(duration_pattern, sparql_query))
    bindings["uses_same_day_substring_join"] = "SUBSTR" in sparql_query.upper()
    bindings["uses_geosparql"] = any(token in sparql_query for token in ("geof:", "geo:", "sfWithin", "sfContains", "sfIntersects"))
    return bindings


def build_sparql_interpretations(
    qid: int,
    sparql_query: str,
    ambiguity_types: list[str],
    provided_interpretations: list[dict[str, Any]] | None,
    provided_queries: list[str] | None,
) -> list[OrderedDict[str, Any]]:
    if provided_interpretations:
        interpretations: list[OrderedDict[str, Any]] = []
        for index, item in enumerate(provided_interpretations):
            if not isinstance(item, dict):
                raise SystemExit("Each sparql_interpretations item must be a JSON object.")
            interpretation = OrderedDict(item)
            interpretation.setdefault("interpretation_id", f"Q{qid}_I{index}")
            interpretation["interpretation_id"] = str(interpretation["interpretation_id"]).format(qid=qid, index=index)
            interpretation.setdefault("label", "Original exact KG interpretation from source JSON" if index == 0 else f"Additional interpretation {index}")
            interpretation.setdefault("is_original_source_query", index == 0)
            interpretation.setdefault("ambiguity_dimensions", ambiguity_types if index == 0 else [])
            interpretation.setdefault("spatial_window", None)
            interpretation.setdefault("temporal_window", None)
            interpretation.setdefault("semantic_constraints", [])
            interpretation.setdefault("rationale", "Preserves the supplied SPARQL query for this GeoOutageBench entry.")
            interpretation.setdefault("sparql_query", sparql_query if index == 0 else "")
            interpretation.setdefault("compatibility_note", "Not executed by this append script; validate against the target SPARQL engine and KG endpoint.")
            interpretations.append(interpretation)
        if interpretations[0].get("sparql_query") != sparql_query:
            raise SystemExit("sparql_interpretations[0].sparql_query must match sparql_query.")
        return interpretations

    queries = provided_queries or [sparql_query]
    if not queries:
        queries = [sparql_query]
    if queries[0] != sparql_query:
        raise SystemExit("sparql_queries[0] must match sparql_query.")

    interpretations = []
    for index, query in enumerate(queries):
        interpretations.append(
            OrderedDict(
                [
                    ("interpretation_id", f"Q{qid}_I{index}"),
                    ("label", "Original exact KG interpretation from source JSON" if index == 0 else f"Additional interpretation {index}"),
                    ("is_original_source_query", index == 0),
                    ("ambiguity_dimensions", ambiguity_types if index == 0 else []),
                    ("spatial_window", None),
                    ("temporal_window", None),
                    ("semantic_constraints", []),
                    (
                        "rationale",
                        "Preserves the original SPARQL query exactly as supplied for this GeoOutageBench entry."
                        if index == 0
                        else "Additional ambiguity-oriented interpretation supplied by the caller.",
                    ),
                    ("sparql_query", query),
                    ("compatibility_note", "Not executed by this append script; validate against the target SPARQL engine and KG endpoint."),
                ]
            )
        )
    return interpretations


def normalize_string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise SystemExit(f"{label} must be a string or list of strings.")


def normalize_primary_category(value: Any, label: str) -> str:
    categories = normalize_string_list(value, label)
    if len(categories) != 1:
        raise SystemExit(f"{label} must contain exactly one category.")
    return categories[0]


def normalize_question_categories(metadata: dict[str, Any]) -> tuple[str, list[str]]:
    legacy_categories = normalize_string_list(metadata.get("category"), "category")

    if "primary_category" in metadata:
        primary_category = normalize_primary_category(metadata["primary_category"], "primary_category")
        if "secondary_categories" in metadata:
            secondary_categories = normalize_string_list(metadata["secondary_categories"], "secondary_categories")
        else:
            secondary_categories = [category for category in legacy_categories if category != primary_category]
    elif legacy_categories:
        primary_category = legacy_categories[0]
        secondary_categories = legacy_categories[1:]
    else:
        raise SystemExit("Missing required metadata field(s): primary_category")

    secondary_categories = [category for category in ordered_unique(secondary_categories) if category != primary_category]
    return primary_category, secondary_categories


def build_entry(qid: int, question: str, metadata: dict[str, Any]) -> OrderedDict[str, Any]:
    missing = [
        field
        for field in REQUIRED_METADATA_FIELDS
        if field not in metadata and not (field == "primary_category" and "category" in metadata)
    ]
    if missing:
        raise SystemExit(f"Missing required metadata field(s): {', '.join(missing)}")

    answer_type = normalize_string_list(metadata["answer_type"], "answer_type")
    primary_category, secondary_categories = normalize_question_categories(metadata)
    ambiguity_types = normalize_string_list(metadata["ambiguity_types"], "ambiguity_types")
    graph_query = metadata["graph_query"]
    num_node, num_edge = count_graph_nodes_edges(graph_query)
    sparql_query = str(metadata["sparql_query"])

    entry = OrderedDict()
    entry["qid"] = qid
    entry["question"] = question
    entry["answer_type"] = answer_type
    entry["answer"] = []
    entry["function"] = metadata.get("function", "none")
    entry["commonness"] = metadata.get("commonness")
    entry["num_node"] = metadata.get("num_node", num_node)
    entry["num_edge"] = metadata.get("num_edge", num_edge)
    entry["graph_query"] = graph_query
    entry["primary_category"] = primary_category
    entry["secondary_categories"] = secondary_categories
    entry["query_complexity"] = metadata["query_complexity"]
    entry["ambiguity_types"] = ambiguity_types
    entry["sparql_query"] = sparql_query
    entry["template_id"] = metadata["template_id"]
    entry["template_family"] = metadata["template_family"]
    entry["question_template"] = metadata["question_template"]
    entry["template_variables"] = metadata["template_variables"]
    entry["spatiotemporal_axis"] = metadata["spatiotemporal_axis"]
    entry["ambiguity_profile"] = metadata.get("ambiguity_profile", build_ambiguity_profile(ambiguity_types))
    entry["window_and_constraint_bindings"] = metadata.get(
        "window_and_constraint_bindings",
        infer_window_and_constraint_bindings(sparql_query),
    )

    provided_interpretations = metadata.get("sparql_interpretations")
    provided_queries = metadata.get("sparql_queries")
    if provided_queries is not None:
        provided_queries = normalize_string_list(provided_queries, "sparql_queries")
    entry["sparql_interpretations"] = build_sparql_interpretations(
        qid,
        sparql_query,
        ambiguity_types,
        provided_interpretations,
        provided_queries,
    )
    entry["sparql_queries"] = [interpretation["sparql_query"] for interpretation in entry["sparql_interpretations"]]

    for key, value in metadata.items():
        if key not in entry and key not in {"answer", "category"}:
            entry[key] = value

    return OrderedDict((key, entry[key]) for key in STANDARD_QUESTION_KEYS if key in entry) | OrderedDict(
        (key, value) for key, value in entry.items() if key not in STANDARD_QUESTION_KEYS
    )


def sorted_counter(values: list[str]) -> OrderedDict[str, int]:
    counter = Counter(values)
    return OrderedDict((key, counter[key]) for key in sorted(counter))


def most_common_ordered(values: list[str]) -> list[str]:
    counter = Counter(values)
    return [key for key, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))]


def refresh_summary_and_catalog(benchmark: dict[str, Any]) -> None:
    questions = benchmark.get("questions", [])
    if not isinstance(questions, list):
        raise SystemExit("Benchmark JSON must contain a list-valued 'questions' field.")

    summary = benchmark.setdefault("transformation_summary", OrderedDict())
    summary["source_question_count"] = len(questions)
    summary["transformed_question_count"] = len(questions)
    summary["template_family_count"] = len({question.get("template_family") for question in questions})
    summary["total_sparql_interpretations"] = sum(len(question.get("sparql_interpretations", [])) for question in questions)
    summary["original_sparql_queries_preserved_exactly"] = all(
        question.get("sparql_interpretations", [{}])[0].get("sparql_query") == question.get("sparql_query")
        for question in questions
        if question.get("sparql_interpretations")
    )
    summary["additional_interpretation_queries_added"] = summary["total_sparql_interpretations"] - len(questions)
    summary["query_complexity_counts"] = sorted_counter([question.get("query_complexity") for question in questions if question.get("query_complexity")])
    summary["spatiotemporal_axis_counts"] = sorted_counter([question.get("spatiotemporal_axis") for question in questions if question.get("spatiotemporal_axis")])
    summary["primary_category_counts"] = sorted_counter([question.get("primary_category") for question in questions if question.get("primary_category")])
    secondary_categories = [
        category
        for question in questions
        for category in question.get("secondary_categories", [])
    ]
    summary["secondary_category_counts"] = sorted_counter(secondary_categories)
    summary["category_by_axis_and_complexity_counts"] = sorted_counter(
        [
            f"{question.get('spatiotemporal_axis')}|{question.get('query_complexity')}"
            for question in questions
            if question.get("spatiotemporal_axis") and question.get("query_complexity")
        ]
    )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        grouped[(question.get("template_id"), question.get("template_family"))].append(question)

    catalog = []
    for (template_id, template_family), members in sorted(grouped.items(), key=lambda item: str(item[0][0])):
        categories: list[str] = []
        primary_categories: list[str] = []
        secondary_categories: list[str] = []
        for member in members:
            primary_category = member.get("primary_category")
            if primary_category:
                categories.append(primary_category)
                primary_categories.append(primary_category)
            member_secondary_categories = member.get("secondary_categories", [])
            categories.extend(member_secondary_categories)
            secondary_categories.extend(member_secondary_categories)
        catalog.append(
            OrderedDict(
                [
                    ("template_id", template_id),
                    ("template_family", template_family),
                    ("question_count", len(members)),
                    ("member_qids", [member["qid"] for member in members]),
                    ("representative_question_template", members[0].get("question_template")),
                    ("dominant_categories", most_common_ordered(categories)),
                    ("dominant_primary_categories", most_common_ordered(primary_categories)),
                    ("dominant_secondary_categories", most_common_ordered(secondary_categories)),
                    ("query_complexity_counts", sorted_counter([member.get("query_complexity") for member in members if member.get("query_complexity")])),
                    ("spatiotemporal_axis_counts", sorted_counter([member.get("spatiotemporal_axis") for member in members if member.get("spatiotemporal_axis")])),
                ]
            )
        )
    benchmark["template_catalog"] = catalog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append a new GeoOutageBench question with the next qid.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--json-path", type=Path, default=DEFAULT_BENCHMARK_PATH, help="Path to geooutagebench_questions.json.")
    parser.add_argument("--question", required=True, help="Natural-language question string for the new entry.")
    parser.add_argument("--metadata", help="JSON object or path to a JSON object containing entry metadata.")
    parser.add_argument("--answer-type", action="append", dest="answer_type", help="Answer type string. Repeat for multiple values.")
    parser.add_argument("--function", dest="function_", help="Function metadata value.")
    parser.add_argument("--commonness", help="JSON value for commonness, for example null, 0.5, or a string.")
    parser.add_argument("--graph-query", help="JSON object or path for graph_query.")
    parser.add_argument("--primary-category", help="Primary category string.")
    parser.add_argument("--secondary-category", action="append", dest="secondary_categories", help="Secondary category string. Repeat for multiple values.")
    parser.add_argument("--category", action="append", help="Legacy category string. If primary-category is omitted, the first category is used as primary.")
    parser.add_argument("--query-complexity", help="Query complexity label, such as 1-hop, 2-hop, or multi-hop.")
    parser.add_argument("--ambiguity-type", action="append", dest="ambiguity_types", help="Ambiguity type string. Repeat for multiple values.")
    parser.add_argument("--sparql-query", help="SPARQL query string or path. Prefix with @ to force file loading.")
    parser.add_argument("--template-id", help="Template id, such as T001.")
    parser.add_argument("--template-family", help="Template family label.")
    parser.add_argument("--question-template", help="Question template string.")
    parser.add_argument("--template-variables", help="JSON object or path for template_variables.")
    parser.add_argument("--spatiotemporal-axis", help="Spatiotemporal axis label.")
    parser.add_argument("--ambiguity-profile", help="JSON object or path for ambiguity_profile.")
    parser.add_argument("--window-and-constraint-bindings", help="JSON object or path for window_and_constraint_bindings.")
    parser.add_argument("--sparql-queries", help="JSON list/path of SPARQL query strings. The first must match sparql_query.")
    parser.add_argument("--sparql-interpretations", help="JSON list/path of interpretation objects.")
    parser.add_argument("--dry-run", action="store_true", help="Print the generated entry without writing the benchmark file.")
    return parser.parse_args()


def merge_cli_metadata(args: argparse.Namespace) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if args.metadata:
        loaded = load_json_arg(args.metadata, "--metadata")
        if not isinstance(loaded, dict):
            raise SystemExit("--metadata must be a JSON object.")
        metadata.update(loaded)

    cli_values = {
        "answer_type": args.answer_type,
        "function": args.function_,
        "graph_query": load_json_arg(args.graph_query, "--graph-query") if args.graph_query else None,
        "primary_category": args.primary_category,
        "secondary_categories": args.secondary_categories,
        "category": args.category,
        "query_complexity": args.query_complexity,
        "ambiguity_types": args.ambiguity_types,
        "sparql_query": load_text_arg(args.sparql_query, "--sparql-query") if args.sparql_query else None,
        "template_id": args.template_id,
        "template_family": args.template_family,
        "question_template": args.question_template,
        "template_variables": load_json_arg(args.template_variables, "--template-variables") if args.template_variables else None,
        "spatiotemporal_axis": args.spatiotemporal_axis,
        "ambiguity_profile": load_json_arg(args.ambiguity_profile, "--ambiguity-profile") if args.ambiguity_profile else None,
        "window_and_constraint_bindings": load_json_arg(args.window_and_constraint_bindings, "--window-and-constraint-bindings")
        if args.window_and_constraint_bindings
        else None,
        "sparql_queries": load_json_arg(args.sparql_queries, "--sparql-queries") if args.sparql_queries else None,
        "sparql_interpretations": load_json_arg(args.sparql_interpretations, "--sparql-interpretations") if args.sparql_interpretations else None,
    }
    if args.commonness is not None:
        cli_values["commonness"] = load_json_arg(args.commonness, "--commonness")

    for key, value in cli_values.items():
        if value is not None:
            metadata[key] = value
    return metadata


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def main() -> int:
    args = parse_args()
    benchmark_path = args.json_path
    with benchmark_path.open("r", encoding="utf-8-sig") as handle:
        benchmark = json.load(handle, object_pairs_hook=OrderedDict)

    questions = benchmark.get("questions")
    if not isinstance(questions, list):
        raise SystemExit("Benchmark JSON must contain a list-valued 'questions' field.")

    qid = infer_next_qid(questions)
    metadata = merge_cli_metadata(args)
    entry = build_entry(qid, args.question, metadata)

    if args.dry_run:
        json.dump(entry, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    questions.append(entry)
    refresh_summary_and_catalog(benchmark)
    write_json_atomic(benchmark_path, benchmark)
    print(f"Added qid {qid} to {benchmark_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
