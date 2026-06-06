from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from rdflib import Literal
from rdflib.namespace import RDF, RDFS, XSD

sys.path.append(str(Path(__file__).resolve().parents[2]))

from utils.geooutage_common import (  # noqa: E402
    Goo,
    Gokg,
    Schema,
    county_resource,
    create_graph,
    normalize_format,
)


def create_outage_records(year: int, root_dir: str, out_path: str, fmt: str = "ttl") -> None:
    """
    Write CustomerOutageRecord class instances to a TTL file with the Eagle-I dataset.
    """
    g = create_graph()
    data_path = os.path.join(root_dir, f"eagle-i/24237376/florida_data/eaglei_outages_{year}.csv")
    df = pd.read_csv(data_path)

    for _, entry in df.iterrows():
        county_name = str(entry["county"]).strip().title().replace(" ", "_")
        fips_code = str(int(entry["fips_code"])).zfill(5)
        num_outages = entry.get("customers_out", entry.get("sum"))
        if pd.isna(num_outages):
            continue
        time = entry["run_start_time"]
        time = datetime.strptime(time, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d_%H:%M:%S")
        record_datetime = datetime.strptime(time, "%Y-%m-%d_%H:%M:%S").strftime("%Y-%m-%dT%H:%M:%SZ")
        record_datetime_ttl = datetime.strptime(time, "%Y-%m-%d_%H:%M:%S").strftime("%Y-%m-%dT%H-%M-%SZ")
        subj = Gokg[f"customeroutagerecord.{fips_code}.{record_datetime_ttl}"]

        g.add((subj, RDF.type, Goo.CustomerOutageRecord))
        g.add((subj, RDFS.label, Literal(f"{county_name} {record_datetime}", lang="en")))
        g.add((subj, Schema.name, Literal(f"{county_name} {record_datetime}", lang="en")))
        g.add(
            (
                subj,
                RDFS.comment,
                Literal(f"Eagle-I outage data for {county_name} County at time {record_datetime}", lang="en"),
            )
        )

        g.add((subj, Goo.representsCounty, county_resource(county_name)))
        g.add((subj, Goo.recordDateTime, Literal(record_datetime, datatype=XSD.dateTime)))
        g.add((subj, Goo.numberOfOutages, Literal(int(num_outages), datatype=XSD.integer)))

    g.serialize(destination=out_path, format=fmt)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root_dir", type=str, required=True)
    p.add_argument("--fmt", type=str, default="ttl")
    p.add_argument("--start_year", type=int, default=2014)
    p.add_argument("--end_year", type=int, default=2026)
    p.add_argument("--out_dir", type=str, default=".")
    args = p.parse_args()

    fmt = normalize_format(args.fmt)

    print("Serializing Outage Timeseries Records...")
    for year in range(args.start_year, args.end_year):
        out_path = os.path.join(args.out_dir, f"outagerecord_{year}.{fmt}")
        create_outage_records(year, args.root_dir, out_path=out_path, fmt=fmt)
        print(f"Serialized Outage Records for year {year}.")


if __name__ == "__main__":
    main()
