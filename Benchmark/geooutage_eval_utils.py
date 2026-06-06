"""Shared utilities for GeoOutageBench evaluation scripts.

The helpers here stay lightweight while using RDFLib's SPARQL parser where
predicate-position awareness is needed and RDFLib graphs for local execution.
"""

from __future__ import annotations

import csv
import json
import math
import re
from base64 import b64encode
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pyparsing import ParseResults
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue


PREFIX_PATTERN = re.compile(r"^\s*PREFIX\s+\w+:\s*<[^>]+>\s*$", re.IGNORECASE | re.MULTILINE)
COMMENT_PATTERN = re.compile(r"#.*")
SPACE_PATTERN = re.compile(r"\s+")

# Class extraction intentionally remains approximate. Property extraction below
# uses RDFLib triple blocks so classes and resource identifiers are not scored
# as predicates.
CLASS_PATTERN = re.compile(
    r"(?:\ba\b|rdf:type(?:\s*/\s*rdfs:subClassOf\*)?)\s+([A-Za-z_][\w-]*:[A-Za-z_][\w-]*)",
    re.IGNORECASE,
)
QNAME_PATTERN = r"[A-Za-z_][\w-]*:[A-Za-z_][\w-]*"
SUBJECT_PATTERN = rf"(?:\?[A-Za-z_][\w-]*|{QNAME_PATTERN}|<[^>]+>)"
TRIPLE_PROPERTY_PATTERN = re.compile(
    rf"(?:^|[{{}}.])\s*{SUBJECT_PATTERN}\s+({QNAME_PATTERN})(?=\s)",
    re.MULTILINE,
)
CONTINUED_PROPERTY_PATTERN = re.compile(rf";\s*({QNAME_PATTERN})(?=\s)")
EXCLUDED_PROPERTY_QNAMES = {
    "rdf:type",
    "xsd:date",
    "xsd:datetime",
    "xsd:decimal",
    "xsd:float",
    "xsd:integer",
}
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?)?")
DISTANCE_UNITS_PATTERN = (
    r"kilometers?|kilometres?|kms?|km|"
    r"meters?|metres?|m|"
    r"centimeters?|centimetres?|cms?|cm|"
    r"millimeters?|millimetres?|mms?|mm|"
    r"miles?|mi|"
    r"yards?|yds?|yd|"
    r"feet|foot|ft|"
    r"inches?|in"
)
DISTANCE_PATTERN = re.compile(
    rf"\b(?:\d+(?:\.\d+)?\s*(?:{DISTANCE_UNITS_PATTERN})|units:(?:{DISTANCE_UNITS_PATTERN}))\b",
    re.IGNORECASE,
)
SPATIAL_TOKENS = {
    "geo:",
    "geof:",
    "sfwithin",
    "sfcontains",
    "sfintersects",
    "sfnearby",
    "distance",
    "buffer",
    "wkt",
    "lat",
    "lon",
    "latitude",
    "longitude",
}
TEMPORAL_TOKENS = {
    "xs:datetime",
    "xsd:datetime",
    "recorddatetime",
    "datetime",
    "date",
    "time",
    "year",
    "month",
    "day",
}


def load_benchmark(path: str | Path) -> list[dict[str, Any]]:
    """Load either a raw question list or a benchmark object with questions."""
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    questions = data.get("questions", data)
    if not isinstance(questions, list):
        raise ValueError(f"Could not find a question list in {path}.")
    return questions


def load_json(path: str | Path) -> Any:
    """Read JSON with utf-8-sig so BOM-prefixed files are accepted."""
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: str | Path, payload: Any) -> None:
    """Write pretty JSON, creating parent directories when needed."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    """Write rows to CSV using the union of all row keys as field names."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize_sparql(query: str | None) -> str:
    """Normalize SPARQL for exact-match checks independent of prefixes/case."""
    if not query:
        return ""
    without_prefixes = PREFIX_PATTERN.sub("", query)
    without_comments = COMMENT_PATTERN.sub("", without_prefixes)
    return SPACE_PATTERN.sub(" ", without_comments).strip().lower()


def get_gold_sparqls(question: dict[str, Any]) -> list[str]:
    """Return all deduplicated gold SPARQL strings for a benchmark question."""
    return [record["sparql_query"] for record in get_gold_interpretations(question)]


def get_gold_interpretations(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Return gold interpretations in a uniform record format.

    The benchmark keeps several backward-compatible fields:
    ``sparql_interpretations`` is the rich format, ``sparql_queries`` is a
    convenience list, and ``sparql_query`` is the original single-query field.
    This function merges those sources while preserving interpretation metadata
    where it exists.
    """
    records: list[dict[str, Any]] = []
    for index, interpretation in enumerate(question.get("sparql_interpretations", [])):
        query = interpretation.get("sparql_query")
        if query:
            record = dict(interpretation)
            record.setdefault("interpretation_index", index)
            records.append(record)
    for index, query in enumerate(question.get("sparql_queries", [])):
        if query:
            records.append(
                {
                    "interpretation_id": f"{question.get('qid', 'unknown')}_sparql_queries_{index}",
                    "interpretation_index": index,
                    "label": "Gold SPARQL interpretation",
                    "is_original_source_query": False,
                    "sparql_query": query,
                }
            )
    if question.get("sparql_query"):
        records.append(
            {
                "interpretation_id": f"{question.get('qid', 'unknown')}_sparql_query",
                "interpretation_index": 0,
                "label": "Original top-level SPARQL query",
                "is_original_source_query": True,
                "sparql_query": question["sparql_query"],
            }
        )

    # The same query can appear through multiple compatibility fields. Dedupe
    # by normalized SPARQL while preserving the richest first-seen record.
    deduped = []
    seen = set()
    for record in records:
        marker = normalize_sparql(record.get("sparql_query"))
        if marker and marker not in seen:
            seen.add(marker)
            deduped.append(record)
    return deduped


def get_original_gold_sparql(question: dict[str, Any]) -> str:
    """Return the original source query, falling back to the top-level query."""
    for interpretation in question.get("sparql_interpretations", []):
        if interpretation.get("is_original_source_query") and interpretation.get("sparql_query"):
            return interpretation["sparql_query"]
    return question.get("sparql_query", "")


def dedupe_preserve_order(values: Iterable[Any]) -> list[Any]:
    """Deduplicate arbitrary JSON-like values without changing order."""
    seen = set()
    result = []
    for value in values:
        marker = json.dumps(value, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def precision_recall_f1(predicted: Iterable[Any], gold: Iterable[Any]) -> dict[str, float]:
    """Compute set precision/recall/F1 after canonicalizing values to strings."""
    pred_set = {canonicalize_value(value) for value in predicted}
    gold_set = {canonicalize_value(value) for value in gold}
    if not pred_set and not gold_set:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not pred_set:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    true_positive = len(pred_set & gold_set)
    precision = true_positive / len(pred_set) if pred_set else 0.0
    recall = true_positive / len(gold_set) if gold_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def safe_mean(values: Iterable[float]) -> float:
    """Average numeric values while ignoring None and NaN entries."""
    values = [value for value in values if value is not None and not math.isnan(value)]
    return sum(values) / len(values) if values else 0.0


def harmonic_mean(a: float, b: float) -> float:
    """Return the two-value harmonic mean, or zero when both values are zero."""
    return 2 * a * b / (a + b) if a + b else 0.0


def canonicalize_value(value: Any) -> str:
    """Convert nested JSON-like values into stable strings for comparison."""
    if isinstance(value, dict):
        return json.dumps({key: canonicalize_value(val) for key, val in sorted(value.items())}, sort_keys=True)
    if isinstance(value, list):
        return json.dumps([canonicalize_value(item) for item in value], sort_keys=True)
    return str(value).strip()


def flatten_answer_rows(answer: Any) -> list[Any]:
    """Represent scalar, dict, and list answers as a list of rows."""
    if answer is None:
        return []
    if isinstance(answer, list):
        return answer
    return [answer]


def extract_classes(query: str) -> set[str]:
    """Extract qnames used as rdf:type targets in a SPARQL query."""
    return set(CLASS_PATTERN.findall(query or ""))


def extract_properties(query: str) -> set[str]:
    """Extract qname predicates from SPARQL graph patterns."""
    return set(_extract_properties_cached(query or ""))


@lru_cache(maxsize=None)
def _extract_properties_cached(query: str) -> frozenset[str]:
    """Parse predicates when possible and fall back for malformed predictions."""
    try:
        tokens = _extract_parsed_predicate_qnames(parseQuery(query))
    except Exception:
        query_body = COMMENT_PATTERN.sub("", PREFIX_PATTERN.sub("", query))
        tokens = set(TRIPLE_PROPERTY_PATTERN.findall(query_body))
        tokens.update(CONTINUED_PROPERTY_PATTERN.findall(query_body))
    return frozenset(token for token in tokens if token.lower() not in EXCLUDED_PROPERTY_QNAMES)


def _extract_parsed_predicate_qnames(node: Any) -> set[str]:
    """Collect qnames that RDFLib parsed in triple predicate positions."""
    tokens = set()
    if isinstance(node, CompValue):
        if node.name == "TriplesBlock":
            for triple_group in node.get("triples", []):
                for index in range(1, len(triple_group), 3):
                    tokens.update(_extract_qnames(triple_group[index]))
        for value in node.values():
            tokens.update(_extract_parsed_predicate_qnames(value))
    elif isinstance(node, (list, tuple, ParseResults)):
        for value in node:
            tokens.update(_extract_parsed_predicate_qnames(value))
    return tokens


def _extract_qnames(node: Any) -> set[str]:
    """Collect lexical qnames from one parsed SPARQL property path."""
    tokens = set()
    if isinstance(node, CompValue):
        if node.name == "pname":
            prefix = node.get("prefix")
            localname = node.get("localname")
            if prefix and localname:
                tokens.add(f"{prefix}:{localname}")
        else:
            for value in node.values():
                tokens.update(_extract_qnames(value))
    elif isinstance(node, (list, tuple, ParseResults)):
        for value in node:
            tokens.update(_extract_qnames(value))
    return tokens


def extract_temporal_constraints(query: str) -> set[str]:
    """Extract coarse temporal evidence such as dates and date/time tokens."""
    query_body = PREFIX_PATTERN.sub("", query or "")
    lower = query_body.lower()
    constraints = set(DATE_PATTERN.findall(query_body))
    constraints.update(token for token in TEMPORAL_TOKENS if token in lower)
    return constraints


def extract_spatial_constraints(query: str) -> set[str]:
    """Extract coarse spatial evidence such as GeoSPARQL tokens and distances."""
    query_body = PREFIX_PATTERN.sub("", query or "")
    lower = query_body.lower()
    constraints = set(DISTANCE_PATTERN.findall(query_body))
    constraints.update(token for token in SPATIAL_TOKENS if token in lower)
    return constraints


def coarse_sparql_syntax_ok(query: str) -> bool:
    """Perform a minimal syntax sanity check for SELECT- or ASK-style queries."""
    lower = (query or "").lower()
    has_braces = "{" in query and "}" in query
    is_ask = re.search(r"\bask\b", lower) is not None
    is_select = re.search(r"\bselect\b", lower) is not None
    has_where = re.search(r"\bwhere\b", lower) is not None
    return has_braces and (is_ask or (is_select and has_where))


def query_endpoint(
    endpoint: str,
    query: str,
    timeout: int = 60,
    username: str | None = None,
    password: str | None = None,
) -> list[dict[str, str]]:
    """Execute SPARQL against an HTTP endpoint and return flat result rows."""
    data = urlencode({"query": query, "format": "application/sparql-results+json"}).encode("utf-8")
    headers = {
        "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if username:
        token = b64encode(f"{username}:{password or ''}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = Request(
        endpoint,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
    return sparql_json_to_rows(payload)


def query_rdflib_graph(rdf_files: list[str], query: str) -> list[dict[str, str]]:
    """Load local RDF files into RDFLib and execute one SPARQL query."""
    try:
        from rdflib import Graph
    except ImportError as exc:
        raise RuntimeError("rdflib is required for --rdf-file execution.") from exc

    graph = Graph()
    for rdf_file in rdf_files:
        try:
            graph.parse(rdf_file)
        except Exception as exc:
            raise RuntimeError(f"Failed to parse RDF file {rdf_file}: {exc}") from exc
    try:
        result = graph.query(query)
    except Exception as exc:
        raise RuntimeError(f"RDFLib query failed: {exc}") from exc
    rows = []
    for row in result:
        if isinstance(row, bool):
            rows.append({"boolean": str(row).lower()})
            continue
        row_dict = {}
        for key, value in row.asdict().items():
            row_dict[str(key)] = str(value)
        rows.append(row_dict)
    return rows


def sparql_json_to_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Convert SPARQL JSON results into the row shape used by evaluators."""
    if "boolean" in payload:
        return [{"boolean": str(payload["boolean"]).lower()}]
    variables = payload.get("head", {}).get("vars", [])
    rows = []
    for binding in payload.get("results", {}).get("bindings", []):
        row = {}
        for variable in variables:
            if variable in binding:
                row[variable] = binding[variable].get("value", "")
        rows.append(row)
    return rows


def get_prediction_map(predictions_path: str | Path) -> dict[str, Any]:
    """Load predictions and key them by qid as strings.

    Accepted files may be a dict keyed by qid, a list of records with ``qid``,
    or a wrapper object containing ``predictions`` or ``questions``.
    """
    payload = load_json(predictions_path)
    if isinstance(payload, dict) and "predictions" in payload:
        payload = payload["predictions"]
    if isinstance(payload, dict) and "questions" in payload:
        payload = payload["questions"]
    if isinstance(payload, dict):
        return {str(key): value for key, value in payload.items()}
    if isinstance(payload, list):
        result = {}
        for item in payload:
            qid = item.get("qid") or item.get("question_id")
            if qid is not None:
                result[str(qid)] = item
        return result
    raise ValueError("Prediction file must be a dict, a list, or contain a top-level 'predictions' list.")


def get_predicted_queries(prediction: Any) -> list[str]:
    """Return predicted SPARQL strings from any supported prediction shape."""
    return [record["sparql_query"] for record in get_predicted_query_records(prediction)]


def get_predicted_query_records(prediction: Any) -> list[dict[str, Any]]:
    """Return predicted SPARQL interpretations in a uniform record format.

    This accepts legacy single-query fields plus richer ambiguity-aware fields:
    ``sparql_query``, ``query``, ``sparql_queries``, ``queries``,
    ``sparql_interpretations``, and ``interpretations``.
    """
    if prediction is None:
        return []
    if isinstance(prediction, str):
        return [{"prediction_index": 0, "sparql_query": prediction}]
    if isinstance(prediction, list):
        records = []
        for index, item in enumerate(prediction):
            records.extend(_prediction_item_to_query_records(item, index))
        return records
    if isinstance(prediction, dict):
        records = []
        # Prefer rich interpretation records first so labels/rationales survive
        # when the same query is also duplicated in the top-level sparql_query.
        for key in ("sparql_interpretations", "interpretations"):
            if key in prediction:
                for index, item in enumerate(flatten_answer_rows(prediction[key])):
                    records.extend(_prediction_item_to_query_records(item, index))
        for key in ("sparql_queries", "queries"):
            if key in prediction:
                for index, item in enumerate(flatten_answer_rows(prediction[key])):
                    records.extend(_prediction_item_to_query_records(item, index))
        for key in ("sparql_query", "query"):
            if key in prediction:
                records.extend(_prediction_item_to_query_records(prediction[key], 0))
        return dedupe_query_records(records)
    return []


def _prediction_item_to_query_records(item: Any, index: int) -> list[dict[str, Any]]:
    """Convert one prediction item into zero or more query records."""
    if not item:
        return []
    if isinstance(item, str):
        return [{"prediction_index": index, "sparql_query": item}]
    if isinstance(item, dict):
        records = []
        for key in ("sparql_query", "query"):
            value = item.get(key)
            if not value:
                continue
            if isinstance(value, list):
                for sub_index, query in enumerate(value):
                    if query:
                        record = dict(item)
                        record["prediction_index"] = item.get("prediction_index", index)
                        record["prediction_subindex"] = sub_index
                        record["sparql_query"] = query
                        records.append(record)
            else:
                record = dict(item)
                record["prediction_index"] = item.get("prediction_index", index)
                record["sparql_query"] = value
                records.append(record)
        return records
    return []


def dedupe_query_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate query records by normalized SPARQL while keeping metadata."""
    deduped = []
    seen = set()
    for index, record in enumerate(records):
        query = record.get("sparql_query")
        marker = normalize_sparql(query)
        if marker and marker not in seen:
            seen.add(marker)
            normalized_record = dict(record)
            normalized_record.setdefault("prediction_index", index)
            deduped.append(normalized_record)
    return deduped


def get_predicted_answers(prediction: Any) -> list[Any]:
    """Return predicted answer rows from common answer-oriented fields."""
    if prediction is None:
        return []
    if isinstance(prediction, list):
        return prediction
    if isinstance(prediction, dict):
        for key in ("answers", "answer", "rows", "results"):
            if key in prediction:
                return flatten_answer_rows(prediction[key])
    return [prediction]


def summarize_by(rows: list[dict[str, Any]], group_key: str, metric_keys: list[str]) -> dict[str, dict[str, float]]:
    """Macro-average metrics after grouping rows by one metadata field."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(group_key, "unknown"))].append(row)
    return {
        group: {metric: safe_mean(float(row.get(metric, 0.0)) for row in group_rows) for metric in metric_keys}
        for group, group_rows in sorted(grouped.items())
    }


def macro_average(rows: list[dict[str, Any]], metric_keys: list[str]) -> dict[str, float]:
    """Macro-average the requested metrics over per-question rows."""
    return {metric: safe_mean(float(row.get(metric, 0.0)) for row in rows) for metric in metric_keys}
