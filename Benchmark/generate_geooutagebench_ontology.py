"""Generate the allowlist-constrained GeoOutageBench ontology profile."""

from __future__ import annotations

import json
import re
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS


BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE = BENCHMARK_DIR.parent / "GeoOutageOnto.ttl"
DEFAULT_ALLOWED = BENCHMARK_DIR / "task1_allowed.txt"
DEFAULT_BENCHMARK = BENCHMARK_DIR / "geooutagebench_questions.json"
DEFAULT_OUTPUT = BENCHMARK_DIR.parent / "GeoOutageOnto_GeoOutageBench.ttl"

GOO = Namespace("https://ucf-henat.github.io/GeoOutageOnto/#")
MDS = Namespace("https://cwrusdle.bitbucket.io/mds/")
GEOSPARQL = Namespace("http://www.opengis.net/ont/geosparql#")
GEO = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")
PROFILE = URIRef("https://ucf-henat.github.io/GeoOutageOnto/geooutagebench-profile")
GOO_QNAME_PATTERN = re.compile(r"\bgoo:([A-Za-z_][\w-]*)")
SCHEMA_TYPES = {OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty}


def load_questions(path: Path) -> list[dict]:
    """Load benchmark questions from either supported JSON layout."""
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    questions = payload.get("questions", payload)
    if not isinstance(questions, list):
        raise ValueError(f"Could not find a question list in {path}.")
    return questions


def benchmark_queries(questions: list[dict]) -> list[str]:
    """Collect every gold SPARQL interpretation without relying on one JSON layout."""
    queries = []
    for question in questions:
        interpretations = question.get("sparql_interpretations", [])
        if interpretations:
            queries.extend(
                interpretation["sparql_query"]
                for interpretation in interpretations
                if interpretation.get("sparql_query")
            )
            continue
        queries.extend(query for query in question.get("sparql_queries", []) if query)
        if question.get("sparql_query"):
            queries.append(question["sparql_query"])
    return queries


def extract_goo_terms(text: str) -> set[URIRef]:
    """Return GeoOutageOnto URIs referenced as goo-prefixed names."""
    return {GOO[local_name] for local_name in GOO_QNAME_PATTERN.findall(text)}


def is_out_of_scope_goo_term(value: object, selected_terms: set[URIRef]) -> bool:
    """Return whether a triple value references an inactive GeoOutageOnto term."""
    return (
        isinstance(value, URIRef)
        and str(value).startswith(str(GOO))
        and value not in selected_terms
    )


def generate_subset(source_path: Path, allowed_path: Path, benchmark_path: Path) -> Graph:
    """Copy declarations for the GeoOutageOnto terms allowed by GeoOutageBench."""
    source = Graph().parse(source_path, format="turtle")
    workload_terms = extract_goo_terms("\n".join(benchmark_queries(load_questions(benchmark_path))))
    selected_terms = extract_goo_terms(allowed_path.read_text(encoding="utf-8-sig"))
    uncovered_terms = workload_terms - selected_terms
    if uncovered_terms:
        names = ", ".join(sorted(source.namespace_manager.normalizeUri(term) for term in uncovered_terms))
        raise ValueError(f"Benchmark queries reference terms outside the allowlist: {names}")

    declared_terms = {
        subject
        for schema_type in SCHEMA_TYPES
        for subject in source.subjects(RDF.type, schema_type)
        if isinstance(subject, URIRef) and str(subject).startswith(str(GOO))
    }
    undeclared_terms = selected_terms - declared_terms
    if undeclared_terms:
        names = ", ".join(sorted(source.namespace_manager.normalizeUri(term) for term in undeclared_terms))
        raise ValueError(f"Benchmark queries reference undeclared GeoOutageOnto terms: {names}")

    subset = Graph()
    for prefix, namespace in (
        ("goo", GOO),
        ("mds", MDS),
        ("geosparql", GEOSPARQL),
        ("geo", GEO),
        ("dcterms", DCTERMS),
        ("owl", OWL),
        ("rdf", RDF),
        ("rdfs", RDFS),
        ("xsd", URIRef("http://www.w3.org/2001/XMLSchema#")),
    ):
        subset.bind(prefix, namespace, replace=True)

    subset.add((PROFILE, RDF.type, OWL.Ontology))
    subset.add((PROFILE, RDFS.label, Literal("GeoResilienceOnto GeoOutageBench Profile", lang="en")))
    subset.add((PROFILE, DCTERMS.creator, Literal("UCF HENAT & CWRU SDLE Research Center")))
    subset.add(
        (
            PROFILE,
            DCTERMS.description,
            Literal(
                "A workload-specific subset of GeoResilienceOnto containing the "
                "GeoOutageOnto terms allowed for use by GeoOutageBench.",
                lang="en",
            ),
        )
    )
    subset.add((PROFILE, OWL.imports, MDS.Ontology))
    subset.add((PROFILE, OWL.versionInfo, Literal("0.0.1 beta GeoOutageBench profile")))

    for subject in selected_terms:
        for triple in source.triples((subject, None, None)):
            if any(is_out_of_scope_goo_term(value, selected_terms) for value in triple):
                continue
            subset.add(triple)

    return subset


def main() -> None:
    subset = generate_subset(DEFAULT_SOURCE, DEFAULT_ALLOWED, DEFAULT_BENCHMARK)
    serialized = subset.serialize(format="turtle")
    header = (
        "# Generated by Benchmark/generate_geooutagebench_ontology.py.\n"
        "# Contains only GeoOutageOnto schema terms allowed for use by GeoOutageBench.\n\n"
    )
    DEFAULT_OUTPUT.write_text(header + serialized, encoding="utf-8")
    print(f"Wrote {DEFAULT_OUTPUT}")


if __name__ == "__main__":
    main()
