from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import quote
import json
import geopandas as gpd
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

try:
    from utils.polygon_to_hash import polygon_to_geohash
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from utils.polygon_to_hash import polygon_to_geohash

# Define namespaces
GOO = Namespace("https://ucf-henat.github.io/GeoOutageOnto/#")
GOKG = Namespace("http://example.org/resource#")
DBR = Namespace("https://dbpedia.org/page/")
WD = Namespace("https://www.wikidata.org/wiki/")
GEOSPARQL = Namespace("http://www.opengis.net/ont/geosparql#")
GEO = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")
MDS = Namespace("https://cwrusdle.bitbucket.io/mds/")

STATE_FIPS_TO_NAME = {
    "01": "Alabama",
    "02": "Alaska",
    "04": "Arizona",
    "05": "Arkansas",
    "06": "California",
    "08": "Colorado",
    "09": "Connecticut",
    "10": "Delaware",
    "11": "District of Columbia",
    "12": "Florida",
    "13": "Georgia",
    "15": "Hawaii",
    "16": "Idaho",
    "17": "Illinois",
    "18": "Indiana",
    "19": "Iowa",
    "20": "Kansas",
    "21": "Kentucky",
    "22": "Louisiana",
    "23": "Maine",
    "24": "Maryland",
    "25": "Massachusetts",
    "26": "Michigan",
    "27": "Minnesota",
    "28": "Mississippi",
    "29": "Missouri",
    "30": "Montana",
    "31": "Nebraska",
    "32": "Nevada",
    "33": "New Hampshire",
    "34": "New Jersey",
    "35": "New Mexico",
    "36": "New York",
    "37": "North Carolina",
    "38": "North Dakota",
    "39": "Ohio",
    "40": "Oklahoma",
    "41": "Oregon",
    "42": "Pennsylvania",
    "44": "Rhode Island",
    "45": "South Carolina",
    "46": "South Dakota",
    "47": "Tennessee",
    "48": "Texas",
    "49": "Utah",
    "50": "Vermont",
    "51": "Virginia",
    "53": "Washington",
    "54": "West Virginia",
    "55": "Wisconsin",
    "56": "Wyoming",
    "60": "American Samoa",
    "66": "Guam",
    "69": "Northern Mariana Islands",
    "72": "Puerto Rico",
    "78": "U.S. Virgin Islands",
}

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


OTHER = [
    "Puerto Rico",
    "U.S. Virgin Islands",
    "District of Columbia",
]


def get_identifier(state_name: str) -> str:
    if state_name == "Louisiana":
        return "Parish"
    if state_name == "Alaska":
        return "Borough"
    if state_name == "American Samoa":
        return "District"
    if state_name == "Northern Mariana Islands":
        return "Municipality"
    if state_name in OTHER:
        return ""
    return "County"


def format_resource_name(county_name: str, state_postal: str, identifier) -> str:
    safe_name = county_name.replace(" ", "").replace("-", "").replace("'", "").replace(".", "")
    return f"{safe_name}{identifier}{state_postal}"


def format_state_resource_name(state_name: str) -> str:
    safe_name = (
        state_name.replace(" ", "")
        .replace("-", "")
        .replace("'", "")
        .replace(".", "")
    )
    return f"{safe_name}State"


def make_dbpedia_page(county_name: str, state_name: str, identifier: str) -> str:
    county_part = county_name.replace(" ", "_")
    state_part = state_name.replace(" ", "_")
    if identifier:
        page = f"{county_part}_{identifier},_{state_part}"
    else:
        page = f"{county_part},_{state_part}"
        
    return quote(page, safe="-._")

def load_sameas_mapping(mapping_path: str) -> dict:
    if mapping_path is None:
        return {}
    path_obj = Path(mapping_path)
    if not path_obj.exists():
        raise FileNotFoundError(f"SameAs mapping file not found: {mapping_path}")
    with path_obj.open("r", encoding="utf-8") as fi:
        return json.load(fi)


def lookup_wikidata_id(row, sameas_mapping: dict, identifier: str) -> str:
    geoid = row["GEOID"]
    if geoid in sameas_mapping:
        return sameas_mapping[geoid].get("wikidata")
    normalized_name = f"{row['NAME'].strip()} {identifier}, {STATE_FIPS_TO_NAME.get(row['STATEFP'], row['STATEFP'])}"
    return sameas_mapping.get(normalized_name, {}).get("wikidata")


def geometry_to_geohashes(geometry, precision: int) -> list[str]:
    if geometry is None:
        return []
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
    Compute neighboring counties for each county using spatial relationships.
    Returns a dict mapping GEOID to list of neighbor URIs.
    """
    neighbors_dict = {}
    
    # Create a spatial index for efficient lookup
    sindex = gdf.sindex
    
    for idx, (_, row) in enumerate(gdf[['GEOID', 'NAME', 'STATEFP', 'geometry']].iterrows()):
        # Find potential neighbors using spatial index
        potential_neighbors_idx = list(sindex.intersection(row.geometry.bounds))
        
        neighbors = []
        for neighbor_idx in potential_neighbors_idx:
            if neighbor_idx == idx:
                continue  # Skip self
            
            neighbor_row = gdf.iloc[neighbor_idx]
            
            # Check if geometries touch or intersect
            if row.geometry.touches(neighbor_row.geometry) or row.geometry.intersects(neighbor_row.geometry):
                neighbor_name = neighbor_row['NAME'].strip()
                neighbor_state_fp = neighbor_row['STATEFP']
                neighbor_state_abbr = STATE_FIPS_TO_POSTAL.get(neighbor_state_fp, neighbor_state_fp)
                neighbor_state_name = STATE_FIPS_TO_NAME.get(neighbor_state_fp, neighbor_state_abbr)
                neighbor_identifier = get_identifier(neighbor_state_name)
                
                neighbor_resource_name = format_resource_name(neighbor_name, neighbor_state_abbr, neighbor_identifier)
                neighbor_uri = GOKG[neighbor_resource_name]
                neighbors.append(neighbor_uri)
        
        full_geoid = row['GEOID']
        neighbors_dict[full_geoid] = neighbors
    
    return neighbors_dict


def add_county_to_graph(graph: Graph, row, sameas_mapping: dict, use_bbox: bool, geohash_precision: int, neighbors: list = None) -> None:
    geoid = row["GEOID"]
    county_name = row["NAME"].strip()
    state_fp = row["STATEFP"]
    state_abbr = STATE_FIPS_TO_POSTAL.get(state_fp, state_fp)
    state_name = STATE_FIPS_TO_NAME.get(state_fp, state_abbr)
    identifier = get_identifier(state_name)
    
    resource_name = format_resource_name(county_name, state_abbr, identifier)
    county_uri = GOKG[resource_name]
    state_uri = GOKG[format_state_resource_name(state_name)]
    geom_uri = GOKG[f"{resource_name}Geom"]

    # Basic properties
    if identifier:
        label = f"{county_name} {identifier}, {state_name}" if state_name else f"{county_name} {identifier}"
    else:
        label = f"{county_name}, {state_name}" if state_name else f"{county_name}"

    graph.add((county_uri, RDF.type, GOO.County))
    graph.add((county_uri, RDFS.label, Literal(label, lang="en")))
    graph.add((county_uri, GOO.fipsCode, Literal(geoid, datatype=XSD.string)))
    graph.add((county_uri, GOO.locatedInState, state_uri))

    # sameAs links
    dbpedia_page = make_dbpedia_page(county_name, state_name or state_abbr, identifier)
    graph.add((county_uri, OWL.sameAs, DBR[dbpedia_page]))
    
    wikidata_id = lookup_wikidata_id(row, sameas_mapping, identifier)
    if wikidata_id:
        graph.add((county_uri, OWL.sameAs, WD[wikidata_id]))

    # Coordinates
    centroid = row.geometry.centroid
    lat = round(float(centroid.y), 6)
    lon = round(float(centroid.x), 6)
    graph.add((county_uri, GOO.lat, Literal(lat, datatype=XSD.decimal)))
    graph.add((county_uri, GOO.lon, Literal(lon, datatype=XSD.decimal)))

    # Neighboring counties
    if neighbors:
        for neighbor_uri in neighbors:
            graph.add((county_uri, GOO.neighboringCounties, neighbor_uri))

    # Geohashes
    geohashes = geometry_to_geohashes(row.geometry, geohash_precision)
    for geohash in geohashes:
        graph.add((county_uri, MDS.Geohash, Literal(geohash)))

    # Geometry
    graph.add((county_uri, GEOSPARQL.hasGeometry, geom_uri))
    
    wkt = geometry_to_wkt(row.geometry, use_bbox)
    graph.add((geom_uri, RDF.type, GEOSPARQL.Geometry))
    graph.add((geom_uri, GEOSPARQL.asWKT, Literal(wkt, datatype=GEOSPARQL.wktLiteral)))


def generate_ttl(gdf, output_path: Path, sameas_mapping: dict, use_bbox: bool, geohash_precision: int) -> None:
    graph = Graph()
    
    # Bind namespaces
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
    
    # Compute neighbors
    print("Computing neighboring counties...")
    neighbors_dict = compute_neighbors(gdf)
    
    # Sort and add each county
    gdf = gdf.sort_values(by='NAME')
    for _, row in gdf.iterrows():
        geoid = row['GEOID']
        neighbors = neighbors_dict.get(geoid, [])
        add_county_to_graph(graph, row, sameas_mapping, use_bbox, geohash_precision, neighbors)
    
    # Serialize to Turtle
    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=output_path, format="turtle")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an RDF Turtle file for U.S. counties from a Census TIGER County shapefile."
    )
    parser.add_argument(
        "--input",
        default="https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/tl_2025_us_county.zip",
        help="Input TIGER county shapefile ZIP file or directory path. Defaults to the 2025 US county TIGER ZIP.",
    )
    parser.add_argument(
        "--output",
        default="counties.ttl",
        help="Output TTL file to write. Defaults to counties.ttl in the current directory.",
    )
    parser.add_argument(
        "--state",
        nargs="*",
        help="Optional state postal abbreviations to filter by (e.g. FL CA). If omitted, all U.S. counties are exported.",
    )
    parser.add_argument(
        "--sameas-mapping",
        default=None,
        help="Optional JSON file mapping county GEOID or name to wikidata IDs, e.g. {\"12001\": {\"wikidata\": \"Q488826\"}}.",
    )
    parser.add_argument(
        "--bbox",
        action="store_true",
        help="Write a rectangular bounding-box approximation instead of the full county polygon geometry.",
    )
    parser.add_argument(
        "--geohash-precision",
        type=int,
        default=3,
        help="Precision for mds:Geohash values. Default is 3 to match shorter geohash strings.",
    )
    args = parser.parse_args()

    gdf = gpd.read_file(args.input)

    if args.state:
        state_set = {s.upper() for s in args.state}
        gdf = gdf[gdf["STATEFP"].isin({k for k, v in STATE_FIPS_TO_POSTAL.items() if v in state_set})]

    if gdf.empty:
        raise SystemExit("No counties found for the selected filter.")

    sameas_mapping = load_sameas_mapping(args.sameas_mapping)
    generate_ttl(gdf, Path(args.output), sameas_mapping, args.bbox, args.geohash_precision)
    print(f"Wrote {len(gdf)} counties to {args.output}")


if __name__ == "__main__":
    main()
