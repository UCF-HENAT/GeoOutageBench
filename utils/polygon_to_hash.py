from __future__ import annotations

from polygon_geohasher.polygon_geohasher import polygon_to_geohashes, geohashes_to_polygon
from shapely import geometry
from typing import Any, Dict, List, Tuple
from rdflib import Graph, Namespace, Literal
from rdflib.namespace import RDFS

def polygon_to_geohash(polygon_coords: List, precision: int):
    """
    Convert a given list of lat, lng coordinate pairs into respective geohashes.
    Returns inner geohashes (everything within polygon) and outer geohashes (everything on edge of polygon).
    """
    
    polygon = geometry.Polygon(polygon_coords)
    inner_geohashes = polygon_to_geohashes(polygon, precision, inner=True)
    outer_geohashes = polygon_to_geohashes(polygon, precision, inner=False)

    return inner_geohashes, outer_geohashes

def parse_wkt_polygon(wkt_str: str) -> List[Tuple[float, float]]:
    """
    Turn a string like
      "POLYGON((x1 y1, x2 y2, …, xn yn))"
    into a Python list of (x, y) tuples.
    """
    # remove the prefix/suffix
    if not wkt_str.startswith("POLYGON((") or not wkt_str.endswith("))"):
        raise ValueError(f"Unexpected WKT: {wkt_str!r}")
    inner = wkt_str[len("POLYGON(("):-2]

    # split into coordinate strings, then map to floats
    coords: List[Tuple[float, float]] = []
    for pair in inner.split(","):
        x_str, y_str = pair.strip().split()
        coords.append((float(x_str), float(y_str)))
    return coords

def extract_county_geohash_data(
        ttl_file: str = "agents/tools/geohash/counties.ttl", 
        county: str = "Lee County, Florida",
        precision: int = 2
        ) -> Dict[str, Any]:
    """
    Read given ontology ttl file, extract all entries with a predicate "GEOSPARQL:asWKT" and convert polygon string
    to polygon list, then to inner and outer geohashes.
    """
    g = Graph()
    g.parse(ttl_file, format="turtle")

    # 2. Define namespaces
    GEO = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")
    EX = Namespace("http://example.org/")
    GEOSPARQL = Namespace("http://www.opengis.net/ont/geosparql#")

    # 3. (Optional) Bind prefixes for nicer serialization
    g.bind("geo", GEO)
    g.bind("ex", EX)
    g.bind("geosparql", GEOSPARQL)

    # 4. Iterate through each SpatialThing that has a latitude
    for subj in g.subjects(predicate=RDFS.label, object=Literal(county, lang="en")):
        geom_instance = g.value(subject=subj, predicate=GEOSPARQL.hasGeometry)
        county_polygon = g.value(subject=geom_instance, predicate=GEOSPARQL.asWKT)
        county_polygon_list = parse_wkt_polygon(county_polygon)
        inner_geohashes, outer_geohashes = polygon_to_geohash(county_polygon_list, precision)

    return {
        "county": county,
        "inner_geohashes": inner_geohashes,
        "outer_geohashes": outer_geohashes,
    }

if __name__ == "__main__":
    # polygon_coords = [(-99.1795917, 19.432134), (-99.1656847, 19.429034),
    #                   (-99.1776492, 19.414236), (-99.1795917, 19.432134)]
    # polygon_to_geohash(polygon_coords, 4)

    ttl_file = "counties.ttl"
    out = extract_county_geohash_data(ttl_file)
    print(f"Inner Geohashes: {out['inner_geohashes']}")
    print(f"Outer Geohashes: {out['outer_geohashes']}")
    