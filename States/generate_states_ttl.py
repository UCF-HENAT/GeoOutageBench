from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import OWL, RDF, RDFS, XSD

COUNTIES_DIR = Path(__file__).resolve().parents[1] / "Counties"
sys.path.insert(0, str(COUNTIES_DIR))


GOO = Namespace("https://ucf-henat.github.io/GeoOutageOnto/#")
GOKG = Namespace("http://example.org/resource#")
DBR = Namespace("https://dbpedia.org/page/")
WD = Namespace("https://www.wikidata.org/wiki/")
GEOSPARQL = Namespace("http://www.opengis.net/ont/geosparql#")
GEO = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")
MDS = Namespace("https://cwrusdle.bitbucket.io/mds/")

STATE_FIPS_TO_POSTAL = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
    "60": "AS",
    "66": "GU",
    "69": "MP",
    "72": "PR",
    "78": "VI",
}


def format_state_resource_name(state_name: str) -> str:
    safe_name = (
        state_name.replace(" ", "")
        .replace("-", "")
        .replace("'", "")
        .replace(".", "")
    )
    return f"{safe_name}State"


def make_dbpedia_page(state_name: str) -> str:
    return quote(state_name.replace(" ", "_"), safe="-._,")


def load_sameas_mapping(mapping_path: str) -> dict:
    if mapping_path is None:
        return {}
    path_obj = Path(mapping_path)
    if not path_obj.exists():
        raise FileNotFoundError(f"SameAs mapping file not found: {mapping_path}")
    with path_obj.open("r", encoding="utf-8") as fi:
        return json.load(fi)


def lookup_wikidata_id(row, sameas_mapping: dict) -> str:
    geoid = row["GEOID"]
    state_name = row["NAME"].strip()
    state_postal = row["STUSPS"].strip()
    if geoid in sameas_mapping:
        return sameas_mapping[geoid].get("wikidata")
    if state_postal in sameas_mapping:
        return sameas_mapping[state_postal].get("wikidata")
    return sameas_mapping.get(state_name, {}).get("wikidata")


def geometry_to_geohashes(geometry, precision: int) -> list[str]:
    if geometry is None:
        return []
    try:
        from utils.polygon_to_hash import polygon_to_geohash
    except ModuleNotFoundError as exc:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        try:
            from utils.polygon_to_hash import polygon_to_geohash
        except ModuleNotFoundError:
            raise SystemExit(
                "Missing dependency: polygon_geohasher. Install the same geohash dependencies used by the county generator before running this script."
            ) from exc

    if geometry.geom_type == "MultiPolygon":
        geometry = max(geometry.geoms, key=lambda p: p.area)
    coords = list(geometry.exterior.coords)
    inner, outer = polygon_to_geohash(coords, precision)
    return sorted(set(inner).union(outer))


def geometry_to_wkt(geometry, use_bbox: bool) -> str:
    if geometry is None:
        return ""
    if use_bbox:
        return geometry.envelope.wkt
    return geometry.wkt


def compute_neighbors(gdf) -> dict:
    """
    Compute bordering state-level areas using spatial relationships.
    Returns a dict mapping state GEOID to a list of neighbor URIs.
    """
    neighbors_dict = {}
    gdf = gdf.reset_index(drop=True)
    sindex = gdf.sindex

    for idx, row in gdf[["GEOID", "NAME", "geometry"]].iterrows():
        potential_neighbors_idx = list(sindex.intersection(row.geometry.bounds))

        neighbors = []
        for neighbor_idx in potential_neighbors_idx:
            if neighbor_idx == idx:
                continue

            neighbor_row = gdf.iloc[neighbor_idx]
            if row.geometry.touches(neighbor_row.geometry) or row.geometry.intersects(neighbor_row.geometry):
                neighbor_uri = GOKG[format_state_resource_name(neighbor_row["NAME"].strip())]
                neighbors.append(neighbor_uri)

        neighbors_dict[row["GEOID"]] = sorted(set(neighbors), key=str)

    return neighbors_dict


def add_state_to_graph(
    graph: Graph,
    row,
    sameas_mapping: dict,
    use_bbox: bool,
    geohash_precision: int,
    neighbors: list = None,
) -> None:
    geoid = row["GEOID"]
    state_name = row["NAME"].strip()
    state_postal = row["STUSPS"].strip() or STATE_FIPS_TO_POSTAL.get(geoid, geoid)

    resource_name = format_state_resource_name(state_name)
    state_uri = GOKG[resource_name]
    geom_uri = GOKG[f"{resource_name}Geom"]

    graph.add((state_uri, RDF.type, GOO.State))
    graph.add((state_uri, RDFS.label, Literal(state_name, lang="en")))
    graph.add((state_uri, GOO.fipsCode, Literal(geoid, datatype=XSD.string)))
    graph.add((state_uri, GOO.statePostalCode, Literal(state_postal, datatype=XSD.string)))

    graph.add((state_uri, OWL.sameAs, DBR[make_dbpedia_page(state_name)]))

    wikidata_id = lookup_wikidata_id(row, sameas_mapping)
    if wikidata_id:
        graph.add((state_uri, OWL.sameAs, WD[wikidata_id]))

    centroid = row.geometry.centroid
    lat = round(float(centroid.y), 6)
    lon = round(float(centroid.x), 6)
    graph.add((state_uri, GOO.lat, Literal(lat, datatype=XSD.decimal)))
    graph.add((state_uri, GOO.lon, Literal(lon, datatype=XSD.decimal)))

    if neighbors:
        for neighbor_uri in neighbors:
            graph.add((state_uri, GOO.neighboringStates, neighbor_uri))

    geohashes = geometry_to_geohashes(row.geometry, geohash_precision)
    for geohash in geohashes:
        graph.add((state_uri, MDS.Geohash, Literal(geohash)))

    graph.add((state_uri, GEOSPARQL.hasGeometry, geom_uri))
    wkt = geometry_to_wkt(row.geometry, use_bbox)
    graph.add((geom_uri, RDF.type, GEOSPARQL.Geometry))
    graph.add((geom_uri, GEOSPARQL.asWKT, Literal(wkt, datatype=GEOSPARQL.wktLiteral)))


def generate_ttl(gdf, output_path: Path, sameas_mapping: dict, use_bbox: bool, geohash_precision: int) -> None:
    graph = Graph()

    graph.bind("goo", GOO)
    graph.bind("gokg", GOKG)
    graph.bind("dbr", DBR)
    graph.bind("wd", WD)
    graph.bind("rdfs", RDFS)
    graph.bind("rdf", RDF)
    graph.bind("owl", OWL)
    graph.bind("xsd", XSD)
    graph.bind("geosparql", GEOSPARQL)
    graph.bind("geo", GEO)
    graph.bind("mds", MDS)

    print("Computing neighboring states and territories...")
    neighbors_dict = compute_neighbors(gdf)

    gdf = gdf.sort_values(by="NAME")
    for _, row in gdf.iterrows():
        geoid = row["GEOID"]
        neighbors = neighbors_dict.get(geoid, [])
        add_state_to_graph(graph, row, sameas_mapping, use_bbox, geohash_precision, neighbors)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=output_path, format="turtle")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an RDF Turtle file for U.S. states, territories, and D.C. from a Census TIGER State shapefile."
    )
    parser.add_argument(
        "--input",
        default="https://www2.census.gov/geo/tiger/TIGER2025/STATE/tl_2025_us_state.zip",
        help="Input TIGER state shapefile ZIP file or directory path. Defaults to the 2025 US state TIGER ZIP.",
    )
    parser.add_argument(
        "--output",
        default="states.ttl",
        help="Output TTL file to write. Defaults to states.ttl in the current directory.",
    )
    parser.add_argument(
        "--state",
        nargs="*",
        help="Optional state or territory postal abbreviations to filter by (e.g. FL DC PR). If omitted, all records are exported.",
    )
    parser.add_argument(
        "--sameas-mapping",
        default=None,
        help="Optional JSON file mapping state GEOID, postal abbreviation, or name to wikidata IDs, e.g. {\"12\": {\"wikidata\": \"Q812\"}}.",
    )
    parser.add_argument(
        "--bbox",
        action="store_true",
        help="Write a rectangular bounding-box approximation instead of the full state polygon geometry.",
    )
    parser.add_argument(
        "--geohash-precision",
        type=int,
        default=3,
        help="Precision for mds:Geohash values. Default is 3 to match shorter geohash strings.",
    )
    args = parser.parse_args()

    try:
        import geopandas as gpd
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: geopandas. Install the same geospatial dependencies used by the county generator before running this script."
        ) from exc

    gdf = gpd.read_file(args.input)

    if args.state:
        state_set = {s.upper() for s in args.state}
        gdf = gdf[gdf["STUSPS"].isin(state_set)]

    if gdf.empty:
        raise SystemExit("No states or territories found for the selected filter.")

    sameas_mapping = load_sameas_mapping(args.sameas_mapping)
    generate_ttl(gdf, Path(args.output), sameas_mapping, args.bbox, args.geohash_precision)
    print(f"Wrote {len(gdf)} states, territories, and D.C. to {args.output}")


if __name__ == "__main__":
    main()
