from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD


Goo = Namespace("https://ucf-henat.github.io/GeoOutageOnto/#")
Gokg = Namespace("http://example.org/resource#")
Schema = Namespace("http://schema.org/")


class StormEventsKG:
    """
    Serialize NOAA StormEvents detail CSV rows into GeoResilienceOnto-compatible RDF.

    The writer uses StormEventRecord, TornadoEventRecord, and StormEpisodeRecord,
    plus optional county and state links resolved from TTL graphs by goo:fipsCode.
    """

    def __init__(
        self,
        fmt: str = "ttl",
        counties_ttl: Optional[str] = None,
        states_ttl: Optional[str] = None,
    ):
        self.fmt = "turtle" if fmt == "ttl" else fmt
        self.county_lookup = self._load_county_lookup(counties_ttl)
        self.state_lookup = self._load_state_lookup(states_ttl)

    def create_graph(self) -> Graph:
        g = Graph()
        g.bind("goo", Goo)
        g.bind("gokg", Gokg)
        g.bind("schema", Schema, override=True, replace=True)
        g.bind("rdfs", RDFS)
        g.bind("rdf", RDF)
        g.bind("xsd", XSD)
        return g

    def serialize_storm_events(
        self,
        csv_path: str,
        out_path: str,
        max_rows: Optional[int] = None,
    ) -> None:
        g = self.create_graph()
        seen_episodes: set[str] = set()

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                if max_rows is not None and idx >= max_rows:
                    break
                self._add_event(g, row, seen_episodes)

        g.serialize(destination=out_path, format=self.fmt)

    def _add_event(self, g: Graph, row: dict[str, str], seen_episodes: set[str]) -> None:
        event_id = clean(row.get("EVENT_ID"))
        if not event_id:
            return

        event_type = clean(row.get("EVENT_TYPE"))
        start_dt = parse_noaa_datetime(row.get("BEGIN_DATE_TIME"))
        end_dt = parse_noaa_datetime(row.get("END_DATE_TIME"))
        state = clean(row.get("STATE"))
        cz_name = clean(row.get("CZ_NAME"))

        event_uri = Gokg[f"stormevent.{event_id}"]
        is_tornado = event_type.lower() == "tornado" or bool(clean(row.get("TOR_F_SCALE")))
        g.add((event_uri, RDF.type, Goo.TornadoEventRecord if is_tornado else Goo.StormEventRecord))

        label_parts = [part for part in [event_type, cz_name, state, event_id] if part]
        g.add((event_uri, RDFS.label, Literal(" - ".join(label_parts), lang="en")))
        g.add((event_uri, Schema.name, Literal(" - ".join(label_parts), lang="en")))
        g.add((event_uri, Goo.stormEventId, Literal(event_id, datatype=XSD.string)))

        if event_type:
            g.add((event_uri, Goo.stormEventType, Literal(event_type, datatype=XSD.string)))
        if start_dt:
            g.add((event_uri, Goo.eventStartTime, Literal(start_dt, datatype=XSD.dateTime)))
        if end_dt:
            g.add((event_uri, Goo.eventEndTime, Literal(end_dt, datatype=XSD.dateTime)))

        add_int(g, event_uri, Goo.deathsDirect, row.get("DEATHS_DIRECT"))
        add_int(g, event_uri, Goo.injuriesDirect, row.get("INJURIES_DIRECT"))
        add_string(g, event_uri, Goo.damageProperty, row.get("DAMAGE_PROPERTY"))
        add_string(g, event_uri, Goo.damageCrops, row.get("DAMAGE_CROPS"))
        add_string(g, event_uri, Goo.eventNarrative, row.get("EVENT_NARRATIVE"))

        add_decimal(g, event_uri, Goo.lat, row.get("BEGIN_LAT"))
        add_decimal(g, event_uri, Goo.lon, row.get("BEGIN_LON"))

        if is_tornado:
            add_string(g, event_uri, Goo.torFScale, row.get("TOR_F_SCALE"))
            add_decimal(g, event_uri, Goo.torLength, row.get("TOR_LENGTH"))
            add_int(g, event_uri, Goo.torWidth, row.get("TOR_WIDTH"))

        county_uri = self._county_uri_for_row(row)
        if county_uri:
            g.add((event_uri, Goo.occurredInCounty, county_uri))

        state_uri = self._state_uri_for_row(row)
        if state_uri:
            g.add((event_uri, Goo.occurredInState, state_uri))

        episode_id = clean(row.get("EPISODE_ID"))
        if episode_id:
            episode_uri = Gokg[f"stormepisode.{episode_id}"]
            if episode_id not in seen_episodes:
                seen_episodes.add(episode_id)
                g.add((episode_uri, RDF.type, Goo.StormEpisodeRecord))
                g.add((episode_uri, RDFS.label, Literal(f"Storm Episode {episode_id}", lang="en")))
                g.add((episode_uri, Goo.episodeId, Literal(episode_id, datatype=XSD.string)))
                add_string(g, episode_uri, RDFS.comment, row.get("EPISODE_NARRATIVE"), lang="en")
            g.add((event_uri, Goo.partOfEpisode, episode_uri))

    def _load_county_lookup(self, counties_ttl: Optional[str]) -> dict[str, URIRef]:
        if not counties_ttl:
            return {}

        g = Graph()
        g.parse(counties_ttl, format="turtle")

        lookup: dict[str, URIRef] = {}
        for county, _, fips_literal in g.triples((None, Goo.fipsCode, None)):
            lookup[str(fips_literal).zfill(5)] = county
        return lookup

    def _load_state_lookup(self, states_ttl: Optional[str]) -> dict[str, URIRef]:
        if not states_ttl:
            return {}

        g = Graph()
        g.parse(states_ttl, format="turtle")

        lookup: dict[str, URIRef] = {}
        for state, _, fips_literal in g.triples((None, Goo.fipsCode, None)):
            lookup[str(fips_literal).zfill(2)] = state
        return lookup

    def _county_uri_for_row(self, row: dict[str, str]) -> Optional[URIRef]:
        if clean(row.get("CZ_TYPE")).upper() != "C":
            return None

        state_fips = clean(row.get("STATE_FIPS"))
        cz_fips = clean(row.get("CZ_FIPS"))
        if not state_fips or not cz_fips:
            return None

        fips = f"{int(state_fips):02d}{int(cz_fips):03d}"
        return self.county_lookup.get(fips)

    def _state_uri_for_row(self, row: dict[str, str]) -> Optional[URIRef]:
        state_fips = clean(row.get("STATE_FIPS"))
        if not state_fips:
            return None

        try:
            fips = f"{int(state_fips):02d}"
        except ValueError:
            return None
        return self.state_lookup.get(fips)


def clean(value: Optional[str]) -> str:
    return "" if value is None else str(value).strip().strip('"')


def parse_noaa_datetime(value: Optional[str]) -> Optional[str]:
    text = clean(value)
    if not text:
        return None

    match = re.fullmatch(r"(\d{1,2}-[A-Za-z]{3}-)(\d{2})( \d{2}:\d{2}:\d{2})", text)
    if match:
        year = int(match.group(2))
        century = 1900 if year >= 50 else 2000
        text = f"{match.group(1)}{century + year}{match.group(3)}"

    for fmt in ("%d-%b-%Y %H:%M:%S",):
        try:
            return datetime.strptime(text.title(), fmt).isoformat()
        except ValueError:
            continue
    return None


def add_string(
    g: Graph,
    subj: URIRef,
    pred: URIRef,
    value: Optional[str],
    *,
    lang: Optional[str] = None,
) -> None:
    text = clean(value)
    if text:
        g.add((subj, pred, Literal(text, lang=lang) if lang else Literal(text, datatype=XSD.string)))


def add_int(g: Graph, subj: URIRef, pred: URIRef, value: Optional[str]) -> None:
    text = clean(value)
    if text:
        try:
            g.add((subj, pred, Literal(int(float(text)), datatype=XSD.integer)))
        except ValueError:
            pass


def add_decimal(g: Graph, subj: URIRef, pred: URIRef, value: Optional[str]) -> None:
    text = clean(value)
    if text:
        try:
            g.add((subj, pred, Literal(float(text), datatype=XSD.decimal)))
        except ValueError:
            pass


def default_output_path(csv_path: str, fmt: str) -> str:
    suffix = "ttl" if fmt in {"ttl", "turtle"} else fmt
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(csv_path).stem)
    return f"{safe_stem}.{suffix}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Serialize NOAA StormEvents detail CSV rows to GeoResilienceOnto RDF."
    )
    parser.add_argument("csv_path", help="Path to a StormEvents_details CSV file.")
    parser.add_argument(
        "--out",
        default=None,
        help="Output RDF path. Defaults to the CSV stem with the selected format extension.",
    )
    parser.add_argument(
        "--counties",
        default=None,
        help="Optional counties.ttl path used to resolve county FIPS to goo:County URIs.",
    )
    parser.add_argument(
        "--states",
        default=None,
        help="Optional states.ttl path used to resolve STATE_FIPS to goo:State URIs.",
    )
    parser.add_argument("--fmt", default="ttl", help="RDFLib serialization format, e.g. ttl, xml, json-ld.")
    parser.add_argument("--max_rows", type=int, default=None, help="Optional row limit for testing.")
    args = parser.parse_args()

    out_path = args.out or default_output_path(args.csv_path, args.fmt)
    writer = StormEventsKG(fmt=args.fmt, counties_ttl=args.counties, states_ttl=args.states)
    writer.serialize_storm_events(args.csv_path, out_path=out_path, max_rows=args.max_rows)
    print(f"Serialized StormEvents RDF to {out_path}")
