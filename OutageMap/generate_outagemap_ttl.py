from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image
from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

sys.path.append(str(Path(__file__).resolve().parents[2]))

from utils.geooutage_common import (  # noqa: E402
    Goo,
    Gokg,
    Ma,
    Schema,
    county_resource,
    create_graph,
    is_image_file,
    load_fips,
    normalize_format,
)


def create_outage_maps(
    outage_map_dir: str,
    ntl_dir: str,
    fips_json: str,
    out_path: str,
    fmt: str = "ttl",
) -> None:
    """
    Write OutageMap class instances to a TTL file with the outage map dataset.
    """
    g = create_graph()
    fips = load_fips(fips_json)

    for dirpath, _, files in os.walk(outage_map_dir):
        print(dirpath)
        county_name = os.path.basename(dirpath).title().replace(" ", "_")
        for file in files:
            if not is_image_file(file):
                continue
            date = file.split(".")[0]
            iso_date = datetime.strptime(date, "%Y_%m_%d").strftime("%Y-%m-%d")
            fips_code = fips[county_name.lower()]

            subj = Gokg[f"outagemap.{fips_code}.{iso_date}"]

            g.add((subj, RDF.type, Goo.OutageMap))
            g.add((subj, RDFS.label, Literal(f"{county_name} {iso_date}", lang="en")))
            g.add((subj, Schema.name, Literal(f"{county_name} {iso_date}", lang="en")))
            g.add(
                (
                    subj,
                    RDFS.comment,
                    Literal(f"Outage Map for {county_name} County at date {iso_date}", lang="en"),
                )
            )
            g.add((subj, Goo.representsCounty, county_resource(county_name)))

            with Image.open(os.path.join(dirpath, file)) as img:
                width, height = img.size
            g.add((subj, Ma.frameWidth, Literal(width, datatype=XSD.integer)))
            g.add((subj, Ma.frameHeight, Literal(height, datatype=XSD.integer)))

            g.add((subj, Ma.locator, URIRef(f"http://purl.archive.org/geooutagekg/imgs/outagemap/{county_name.lower()}/{date}.png")))

            ntl_png = os.path.join(ntl_dir, county_name.lower(), file)
            if os.path.exists(ntl_png):
                g.add((subj, Goo.generatedFromNTLImage, Gokg[f"ntlimage.{fips_code}.{iso_date}"]))

    g.serialize(destination=out_path, format=fmt)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root_dir", type=str, required=True)
    p.add_argument("--outage_map_dir", type=str)
    p.add_argument("--ntl_dir", type=str)
    p.add_argument("--fips_json", type=str)
    p.add_argument("--out_path", type=str)
    p.add_argument("--fmt", type=str, default="ttl")
    args = p.parse_args()

    fmt = normalize_format(args.fmt)
    outage_map_dir = args.outage_map_dir or os.path.join(args.root_dir, "outage_maps/")
    ntl_dir = args.ntl_dir or os.path.join(args.root_dir, "VNP46A2_county_imgs/")
    fips_json = args.fips_json or os.path.join(args.root_dir, "fips_codes.json")
    out_path = args.out_path or f"outagemap.{fmt}"

    print("Serializing Outage Maps...")
    create_outage_maps(outage_map_dir, ntl_dir, fips_json, out_path, fmt=fmt)
    print("Serialized Outage Maps.")


if __name__ == "__main__":
    main()
