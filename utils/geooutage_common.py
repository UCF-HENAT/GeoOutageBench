from __future__ import annotations

import json
import os

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD

Goo = Namespace("https://ucf-henat.github.io/GeoOutageOnto/#")
Gokg = Namespace("http://example.org/resource#")
Ma = Namespace("http://www.w3.org/ns/ma-ont#")
Mds = Namespace("https://cwrusdle.bitbucket.io/mds/")
Schema = Namespace("http://schema.org/")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
COUNTY_RESOURCE_NAME_OVERRIDES = {
    "Desoto": "DeSoto",
}


def create_graph() -> Graph:
    g = Graph()
    g.bind("goo", Goo)
    g.bind("gokg", Gokg)
    g.bind("ma-ont", Ma)
    g.bind("mds", Mds)
    g.bind("schema", Schema, override=True, replace=True)
    g.bind("rdfs", RDFS)
    g.bind("rdf", RDF)
    g.bind("xsd", XSD)
    return g


def county_resource(county_name: str) -> URIRef:
    """
    Match GeoResilience Counties resources, e.g. AlachuaCountyFL.
    These generators currently target the Florida Eagle-I/Black Marble data.
    """
    county_name = COUNTY_RESOURCE_NAME_OVERRIDES.get(county_name, county_name)
    safe_county = (
        county_name.replace("_", "")
        .replace(" ", "")
        .replace("-", "")
        .replace("'", "")
        .replace(".", "")
    )
    return Gokg[f"{safe_county}CountyFL"]


def is_image_file(file_name: str) -> bool:
    return os.path.splitext(file_name.lower())[1] in IMAGE_EXTENSIONS


def load_fips(path: str) -> dict[str, str]:
    with open(path, "r") as f:
        fips = json.load(f)
    return {key.replace(" ", "_").lower(): str(value).zfill(5) for key, value in fips.items()}


def normalize_format(fmt: str) -> str:
    if fmt == "turtle":
        return "ttl"
    return fmt
