from __future__ import annotations

import argparse
import csv
import io
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

try:
    from utils.geooutage_common import Goo, Gokg, Schema, create_graph, normalize_format
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from utils.geooutage_common import Goo, Gokg, Schema, create_graph, normalize_format


SUPPORTED_YEARS = {2000, 2010, 2014, 2016, 2018, 2020, 2022}
NULL_VALUES = {"", "-999", "-999.0", "nan", "na", "n/a", "null"}

CDC_SVI_DOWNLOAD_PAGE = "https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html"
DEFAULT_DOWNLOAD_URL_TEMPLATES = (
    "https://svi.cdc.gov/Documents/Data/{year}/SVI{year}_US_county.csv",
    "https://svi.cdc.gov/Documents/Data/{year}/SVI_{year}_US_county.csv",
    "https://svi.cdc.gov/Documents/Data/{year}/csv/SVI{year}_US_county.csv",
    "https://svi.cdc.gov/Documents/Data/{year}/csv/SVI_{year}_US_county.csv",
    "https://svi.cdc.gov/map/data/csv/SVI{year}_US_county.csv",
)


FIELD_MAP = {
    Goo.sviOverallScore: ("RPL_THEMES", "R_PL_THEMES", "USTP"),
    Goo.sviSocioeconomicTheme: ("RPL_THEME1", "R_PL_THEME1", "USG1TP"),
    Goo.sviHouseholdCompositionDisabilityTheme: ("RPL_THEME2", "R_PL_THEME2", "USG2TP"),
    Goo.sviMinorityStatusLanguageTheme: ("RPL_THEME3", "R_PL_THEME3", "USG3TP"),
    Goo.sviHousingTransportationTheme: ("RPL_THEME4", "R_PL_THEME4", "USG4TP"),
    Goo.povertyRate: ("EP_POV150", "EP_POV", "E_P_POV", "P_POV", "P_POV150", "G1V1R"),
    Goo.unemploymentRate: ("EP_UNEMP", "E_P_UNEMP", "P_UNEMP", "G1V2R"),
    Goo.minorityPercentage: ("EP_MINRTY", "EP_MINORITY", "P_MINRTY", "P_MINORITY", "G3V1R"),
    Goo.totalPopulation: ("E_TOTPOP", "TOTPOP", "TOTPOP2000"),
}


def clean(value: object) -> str:
    return "" if value is None else str(value).strip().strip('"')


def normalize_header(value: str) -> str:
    return clean(value).upper()


def first_value(row: dict[str, str], names: Iterable[str]) -> str:
    for name in names:
        value = clean(row.get(normalize_header(name)))
        if value.lower() not in NULL_VALUES:
            return value
    return ""


def parse_decimal(value: str) -> Optional[float]:
    text = clean(value).replace(",", "")
    if text.lower() in NULL_VALUES:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: str) -> Optional[int]:
    number = parse_decimal(value)
    if number is None:
        return None
    return int(number)


def normalize_fips(value: str, width: int = 5) -> str:
    text = clean(value)
    if not text:
        return ""

    try:
        return f"{int(float(text)):0{width}d}"
    except ValueError:
        pass

    digits = "".join(re.findall(r"\d+", text))
    if not digits:
        return ""
    if len(digits) > width:
        return digits[-width:]
    return digits.zfill(width)


def fips_from_row(row: dict[str, str]) -> str:
    for field in ("STCNTY", "STCOFIPS", "FIPS", "GEOID", "GEOIDFQ"):
        fips = normalize_fips(row.get(field, ""))
        if fips:
            return fips

    state_fips = normalize_fips(first_value(row, ("ST", "STATEFP", "STATE_FIPS")), width=2)
    county_fips = normalize_fips(first_value(row, ("COUNTYFP", "COUNTY_FIPS", "CNTY", "CNTY_FIPS", "COU")), width=3)
    if state_fips and county_fips:
        return f"{state_fips}{county_fips}"

    return ""


def label_from_row(row: dict[str, str], year: int, fips: str) -> str:
    county = first_value(row, ("COUNTY", "COUNTY_NAME", "NAME"))
    state = first_value(row, ("ST_ABBR", "STATE_ABBR", "STATE"))
    location = first_value(row, ("LOCATION",))

    if county and state:
        return f"CDC SVI {year} - {county}, {state}"
    if location:
        return f"CDC SVI {year} - {location}"
    return f"CDC SVI {year} - County FIPS {fips}"


def load_county_lookup(counties_ttl: Optional[str]) -> dict[str, URIRef]:
    if not counties_ttl:
        return {}

    graph = Graph()
    graph.parse(counties_ttl, format="turtle")

    lookup: dict[str, URIRef] = {}
    for county, _, fips_literal in graph.triples((None, Goo.fipsCode, None)):
        lookup[str(fips_literal).zfill(5)] = county
    return lookup


def county_uri_from_fips(fips: str, county_lookup: dict[str, URIRef]) -> URIRef:
    return county_lookup.get(fips, Gokg[f"county.{fips}"])


def add_decimal(g: Graph, subj: URIRef, pred: URIRef, value: str) -> None:
    number = parse_decimal(value)
    if number is not None:
        g.add((subj, pred, Literal(number, datatype=XSD.decimal)))


def add_int(g: Graph, subj: URIRef, pred: URIRef, value: str) -> None:
    number = parse_int(value)
    if number is not None:
        g.add((subj, pred, Literal(number, datatype=XSD.integer)))


def open_svi_csv(input_path: Path) -> tuple[io.TextIOBase, Optional[tempfile.TemporaryDirectory]]:
    if not input_path.exists():
        raise FileNotFoundError(f"SVI input file not found: {input_path}")

    if input_path.suffix.lower() != ".zip":
        return input_path.open("r", encoding="utf-8-sig", newline=""), None

    temp_dir = tempfile.TemporaryDirectory()
    with zipfile.ZipFile(input_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            temp_dir.cleanup()
            raise ValueError(f"No CSV file found inside {input_path}")
        selected_name = sorted(csv_names, key=lambda name: ("county" not in name.lower(), name))[0]
        extracted_path = Path(temp_dir.name) / Path(selected_name).name
        extracted_path.write_bytes(archive.read(selected_name))

    return extracted_path.open("r", encoding="utf-8-sig", newline=""), temp_dir


def generate_svi_ttl(
    input_path: Path,
    output_path: Path,
    year: int,
    counties_ttl: Optional[str] = None,
    fmt: str = "ttl",
    max_rows: Optional[int] = None,
) -> int:
    county_lookup = load_county_lookup(counties_ttl)
    graph = create_graph()
    graph.bind("schema", Schema, override=True, replace=True)

    record_count = 0
    csv_file, temp_dir = open_svi_csv(input_path)
    try:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            raise ValueError(f"No CSV header found in {input_path}")
        reader.fieldnames = [normalize_header(field) for field in reader.fieldnames]

        for row in reader:
            if max_rows is not None and record_count >= max_rows:
                break

            normalized_row = {normalize_header(key): value for key, value in row.items()}
            if year in {2000, 2010}:
                for key, value in normalized_row.items():
                    try:
                        for field_list in list(FIELD_MAP[Goo.minorityPercentage] + FIELD_MAP[Goo.povertyRate] + FIELD_MAP[Goo.unemploymentRate]):
                            if key in field_list:
                                normalized_row[key] = str(round(float(value) * 100, 2))
                    except ValueError:
                        pass
            
            fips = fips_from_row(normalized_row)
            if not fips:
                continue

            county_uri = county_uri_from_fips(fips, county_lookup)
            record_uri = Gokg[f"svi.{year}.{fips}"]
            label = label_from_row(normalized_row, year, fips)

            graph.add((record_uri, RDF.type, Goo.SVIRecord))
            graph.add((record_uri, RDFS.label, Literal(label, lang="en")))
            graph.add((record_uri, Schema.name, Literal(label, lang="en")))
            graph.add(
                (
                    record_uri,
                    RDFS.comment,
                    Literal(
                        f"CDC/ATSDR Social Vulnerability Index county record for {fips} in {year}.",
                        lang="en",
                    ),
                )
            )
            graph.add((record_uri, Goo.representsCounty, county_uri))
            graph.add((record_uri, Goo.sviYear, Literal(year, datatype=XSD.integer)))

            for predicate, source_fields in FIELD_MAP.items():
                value = first_value(normalized_row, source_fields)
                if predicate == Goo.totalPopulation:
                    add_int(graph, record_uri, predicate, value)
                else:
                    add_decimal(graph, record_uri, predicate, value)

            record_count += 1
    finally:
        csv_file.close()
        if temp_dir is not None:
            temp_dir.cleanup()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=output_path, format="turtle" if fmt == "ttl" else fmt)
    return record_count


def download_svi_csv(
    year: int,
    download_dir: Path,
    url_templates: Iterable[str] = DEFAULT_DOWNLOAD_URL_TEMPLATES,
) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    for template in url_templates:
        url = template.format(year=year)
        destination = download_dir / Path(url).name
        request = Request(url, headers={"User-Agent": "GeoResilienceKG SVI generator"})
        try:
            with urlopen(request, timeout=60) as response:
                destination.write_bytes(response.read())
            return destination
        except (HTTPError, URLError, TimeoutError) as exc:
            errors.append(f"{url}: {exc}")

    raise RuntimeError(
        "Could not download the CDC SVI county CSV from the known URL patterns. "
        f"Download the CSV from {CDC_SVI_DOWNLOAD_PAGE} and pass it with --input.\n"
        + "\n".join(errors)
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate GeoResilienceOnto Turtle records from a CDC/ATSDR SVI county CSV for a selected year."
    )
    parser.add_argument("--year", type=int, required=True, help="SVI release year, e.g. 2022.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to a CDC SVI county CSV or ZIP. If omitted, the script tries known CDC CSV URL patterns.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output RDF path. Defaults to svi_<year>.ttl in the current directory.",
    )
    parser.add_argument(
        "--counties",
        default=None,
        help="Optional counties.ttl path used to resolve county FIPS codes to existing goo:County URIs.",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=Path("."),
        help="Directory for downloaded CDC CSVs when --input is omitted. Defaults to the current directory.",
    )
    parser.add_argument(
        "--download-url-template",
        action="append",
        default=None,
        help="Optional CDC CSV URL template containing {year}. Can be passed more than once.",
    )
    parser.add_argument("--fmt", default="ttl", help="RDFLib serialization format, e.g. ttl, xml, json-ld.")
    parser.add_argument("--max-rows", type=int, default=None, help="Optional row limit for testing.")
    args = parser.parse_args()

    if args.year not in SUPPORTED_YEARS:
        raise SystemExit(f"Unsupported SVI year {args.year}. Expected one of {sorted(SUPPORTED_YEARS)}.")

    fmt = normalize_format(args.fmt)
    input_path = args.input
    if input_path is None:
        templates = args.download_url_template or DEFAULT_DOWNLOAD_URL_TEMPLATES
        input_path = download_svi_csv(args.year, args.download_dir, templates)
        print(f"Downloaded CDC SVI CSV to {input_path}")

    output_path = args.output or Path(f"svi_{args.year}.{fmt}")
    count = generate_svi_ttl(
        input_path=input_path,
        output_path=output_path,
        year=args.year,
        counties_ttl=args.counties,
        fmt=fmt,
        max_rows=args.max_rows,
    )
    print(f"Serialized {count} CDC SVI county records for {args.year} to {output_path}")


if __name__ == "__main__":
    main()
